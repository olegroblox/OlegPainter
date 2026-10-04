"""Mixin extracted from engine/olegpainter/core.py.

Pre/post-color extra actions: recording, sanitisation, playback.
"""
from __future__ import annotations

import logging

import math
from collections import deque
from pathlib import Path  # noqa: F401

from .common import *  # noqa: F401,F403
from .translations import tr, LANGUAGES, current_lang  # noqa: F401
from .calibration import (  # noqa: F401
    calib_grab_screen,
    calib_show_fullscreen_top,
    calib_select_two_points,
    calib_select_region,
)
from .cluster_cleanup import apply_cleanup_mode  # noqa: F401
from .color_spaces import perceptual_lightness_from_rgb  # noqa: F401
from .prep_pipeline import build_perceptual_cluster_map  # noqa: F401
from .viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    full_area_rect,
    normalize_area_rect,
    normalize_desktop_rect,
)


log = logging.getLogger("olegpainter.engine.olegpainter.extra_actions")


class ExtraActionsMixin:
    """Pre/post-color extra actions: recording, sanitisation, playback."""

    def _normalize_actions_slot(self, slot: str | None) -> str:
        value = (slot or "pre").strip().lower()
        if value in ("post", "after", "post_color", "post_color_actions"):
            return "post"
        return "pre"

    def _actions_bucket(self, slot: str | None):
        key = self._normalize_actions_slot(slot)
        bucket = self.extra_actions_state.get(key)
        if bucket is None:
            bucket = {"events": [], "enabled": False}
            self.extra_actions_state[key] = bucket
        if "events" not in bucket:
            bucket["events"] = []
        if "enabled" not in bucket:
            bucket["enabled"] = False
        return bucket

    def _actions_slot_label(self, slot: str | None) -> str:
        key = self._normalize_actions_slot(slot)
        lookup_key = "extra_actions_slot_post" if key == "post" else "extra_actions_slot_pre"
        try:
            return tr(lookup_key)
        except Exception:
            return "after color" if key == "post" else "before color"

    def _serialize_actions(self, slot: str | None, *, include_draft=False):
        if include_draft and self.is_capturing_extra_actions and self.active_extra_actions_slot == self._normalize_actions_slot(slot):
            return [dict(item) for item in self._capture_events_buffer]
        bucket = self._actions_bucket(slot)
        return [dict(item) for item in bucket.get("events", [])]

    def _extra_actions_summary(self) -> dict:
        summary = {}
        for key in ("pre", "post"):
            bucket = self._actions_bucket(key)
            summary[key] = {
                "count": len(self._serialize_actions(key, include_draft=True)),
                "enabled": bool(bucket.get("enabled", False)),
                "capturing": self.is_capturing_extra_actions and self.active_extra_actions_slot == key,
            }
        summary["active_slot"] = self.active_extra_actions_slot
        return summary

    def _sanitize_action_event(self, raw: dict) -> dict | None:
        if not isinstance(raw, dict):
            return None
        device = str(raw.get("device", "")).lower()
        action = str(raw.get("action", "")).lower()
        delay = float(raw.get("delay", 0) or 0)
        if device != "mouse":
            return None
        if action not in ("down", "up", "move", "click"):
            return None
        button = raw.get("button", "left")
        if button is None:
            button = "left"
        button = str(button).lower()
        x = raw.get("x")
        y = raw.get("y")
        try:
            x_int = int(x)
            y_int = int(y)
        except Exception:
            return None
        event = {
            "device": "mouse",
            "action": action,
            "button": button,
            "x": x_int,
            "y": y_int,
        }
        max_delay = getattr(self, "max_extra_action_delay", 2.0)
        if delay < 0:
            delay = 0.0
        if delay > max_delay:
            delay = max_delay
        if action == "click":
            delay = float(self.extra_action_default_delay or 0.0)
        event["delay"] = float(delay)
        return event

    def _convert_legacy_coords_to_events(self, legacy_items):
        events = []
        if not isinstance(legacy_items, (list, tuple)):
            return events
        for item in legacy_items:
            if not isinstance(item, dict):
                continue
            x = item.get("x")
            y = item.get("y")
            try:
                x_int = int(x)
                y_int = int(y)
            except Exception:
                continue
            events.append({"device": "mouse", "action": "click", "button": "left", "x": x_int, "y": y_int, "delay": float(self.extra_action_default_delay or 0.0)})
        return events

    def _load_actions_from_config(self, data: dict):
        for slot in ("pre", "post"):
            config_key = f"{slot}_color_actions"
            raw_events = data.get(config_key, [])
            bucket = self._actions_bucket(slot)
            events = []
            if isinstance(raw_events, (list, tuple)):
                for entry in raw_events:
                    normalized = self._sanitize_action_event(entry)
                    if normalized is not None:
                        events.append(normalized)
            bucket["events"] = events
            enabled_flag = data.get(f"{slot}_actions_enabled")
            if enabled_flag is not None:
                bucket["enabled"] = bool(enabled_flag)
        legacy_actions = data.get("extra_action_coords")
        legacy_enabled = data.get("extra_actions_enabled")
        if legacy_actions and not self._actions_bucket("pre")["events"]:
            migrated = self._convert_legacy_coords_to_events(legacy_actions)
            if migrated:
                pre_bucket = self._actions_bucket("pre")
                pre_bucket["events"] = migrated
                pre_bucket["enabled"] = bool(legacy_enabled)
        for slot in ("pre", "post"):
            bucket = self._actions_bucket(slot)
            if bucket.get("enabled") and not bucket.get("events"):
                bucket["enabled"] = False
        self._notify_extra_actions_changed()

    def _append_capture_event(self, entry: dict):
        session = getattr(self, "_capture_session", None)
        if session is None:
            return
        if not self.is_capturing_extra_actions:
            return
        device = str(entry.get("device", "")).lower()
        if device != "mouse":
            return
        try:
            now = time.time()
        except Exception:
            now = None
        if now is not None and now < self._capture_ignore_until:
            return
        delay = float(self.extra_action_default_delay or 0.0)
        max_delay = getattr(self, "max_extra_action_delay", 2.0)
        if delay < 0:
            delay = 0.0
        elif delay > max_delay:
            delay = max_delay
        event = dict(entry)
        event["delay"] = float(delay)
        def commit():
            self._capture_events_buffer.append(event)
            self._notify_extra_actions_changed()
            if len(self._capture_events_buffer) >= self.max_extra_action_events:
                slot_label = self._actions_slot_label(self.active_extra_actions_slot)
                self.status_callback(tr("status_extra_actions_capture_max_slot", slot=slot_label, max_actions=self.max_extra_action_events))
                self._finish_extra_actions_capture(auto=True)
        session.publish(commit)

    def _on_mouse_capture_event(self, x, y, button, pressed):
        if not self.is_capturing_extra_actions:
            return
        try:
            btn_name = button.name  # type: ignore[attr-defined]
        except Exception:
            btn_name = str(button)
        if pressed:
            return
        try:
            entry = {
                "device": "mouse",
                "action": "click",
                "button": str(btn_name or "left").lower(),
                "x": int(x),
                "y": int(y),
            }
        except Exception:
            return
        self._append_capture_event(entry)

    def _should_play_actions(self, slot: str) -> bool:
        bucket = self._actions_bucket(slot)
        return bool(bucket.get("enabled") and bucket.get("events") and not self.is_capturing_extra_actions)

    def _play_actions_sequence(self, slot: str) -> bool:
        bucket = self._actions_bucket(slot)
        events = bucket.get("events") or []
        if not events:
            return True
        try:
            label = self._actions_slot_label(slot)
        except Exception:
            label = slot
        pressed = set()
        try:
            for event in events:
                if self.stop_flag:
                    break
                # Recorded clicks open menus and pickers: the next click must wait
                # for the program to show them (Gartic Phone lost every click after
                # the first one without it).
                delay = max(float(event.get("delay", 0) or 0), float(getattr(self, "extra_action_min_gap", 0.15)))
                time.sleep(delay)
                device = event.get("device")
                action = event.get("action")
                if device != "mouse":
                    continue
                x = int(event.get("x", 0))
                y = int(event.get("y", 0))
                button = str(event.get("button", "left") or "left").lower()
                if action == "move":
                    self._click_abs(x, y)
                    continue
                self._click_abs(x, y)
                if action == "click":
                    self._input.button_down(button)
                    pressed.add(button)
                    click_delay = getattr(self, "extra_action_click_press_delay", 0.01)
                    if click_delay > 0:
                        time.sleep(click_delay)
                    self._mouse_up_with_settle(button)
                    pressed.discard(button)
                elif action == "down":
                    self._input.button_down(button)
                    pressed.add(button)
                elif action == "up":
                    self._mouse_up_with_settle(button)
                    pressed.discard(button)
        except Exception as exc:
            self._log(f"Extra actions ({label}) playback error: {exc}", True)
            self.stop_flag = True
            self.drawing_enabled = False
            message = tr("status_error_extra_actions_playback_slot", slot=label, reason=exc)
            # Итоговое событие FAILED получает исходный отказ транспорта без контекста;
            # draw_colors_thread подставит это сообщение вместо него.
            self._drawing_failure_message = message
            if self.status_callback:
                try:
                    self.status_callback("error: " + message)
                except Exception:
                    log.debug("ignored exception in extra-actions playback status callback", exc_info=True)
            return False
        finally:
            # Never leave a mouse button physically held down (a recorded "down"
            # with no matching "up", or a stop/exception mid-sequence).
            for btn in list(pressed):
                try:
                    self._mouse_up_with_settle(btn)
                except Exception:
                    log.debug("ignored exception releasing held button in extra-actions playback", exc_info=True)
        # the last recorded click opens something too (the HEX box): let it appear
        time.sleep(float(getattr(self, "extra_action_min_gap", 0.15)))
        return True

    def _stop_actions_listeners(self):
        listener = getattr(self, 'mouse_listener_extra_actions', None)
        if listener and getattr(listener, 'is_alive', lambda: False)():
            try:
                listener.stop()
            except Exception:
                log.debug('ignored exception in listener.stop()', exc_info=True)
        self.mouse_listener_extra_actions = None
        hook = getattr(self, 'keyboard_hook_extra_actions', None)
        if hook is not None:
            try:
                keyboard.unhook(hook)
            except Exception:
                log.debug('ignored exception in keyboard.unhook(hook)', exc_info=True)
        self.keyboard_hook_extra_actions = None
