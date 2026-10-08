from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Set, Tuple

CATEGORY_INFO = [
    {
        "id": "drawing",
        "label": "Параметры рисования",
        "description": "Режим, маршрут, паузы, кисть, фон, порядок цветов и другие параметры.",
        "default": True,
    },
    {
        "id": "area",
        "label": "Область на экране",
        "description": "Где на экране рисовать: координаты холста.",
        "default": True,
    },
    {
        "id": "calibration",
        # It also carries the learned brush: «Выбор цвета» alone hid that (audit 2026-10-05).
        "label": "Выбор цвета и кисть",
        "description": "Поле HEX, цветовой круг и яркость, палитра рамкой, регулятор и обученная кисть.",
        "default": True,
    },
    {
        "id": "palette",
        "label": "Готовые цвета",
        "description": "Цвета, добавленные по одному на странице «Палитра».",
        "default": True,
    },
    {
        "id": "layers",
        "label": "Слои программы",
        "description": "Режим слоёв и кнопки слоёв в программе.",
        "default": True,
    },
    {
        "id": "image",
        "label": "Картинка",
        "description": "Путь к файлу картинки или отметка, что она из буфера.",
        "default": False,
    },
    {
        "id": "hotkeys",
        "label": "Горячие клавиши",
        "description": "Глобальные клавиши и клавиши окна программы.",
        "default": True,
    },
    {
        "id": "place",
        "label": "Место и способ цвета",
        "description": "Где рисуем, маршрут и способ выбора цвета.",
        "default": True,
    },
]

CATEGORY_LABELS = {item["id"]: item["label"] for item in CATEGORY_INFO}
DEFAULT_CATEGORY_IDS = {item["id"] for item in CATEGORY_INFO if item.get("default")}
ALL_CATEGORY_IDS = {item["id"] for item in CATEGORY_INFO}

PAINTER_CATEGORY_KEYS = {
    "area": {"draw_region", "crop_norm", "flip_horizontal", "flip_vertical"},
    "calibration": {
        "hex_input_coord",
        "color_picking_method",
        "circle_params_calib",
        "slider_params_calib",
        "palette_rotation_direction_calib",
        "screen_palette_calib",
        "wheel_square_calib",
        "alpha_slider_params",
        "dynamic_brush_enabled",
        "dynamic_brush_control_mode",
        "dynamic_brush_coord",
        "dynamic_brush_profile",
        "dynamic_brush_slider_params",
        "dynamic_brush_calibration",
    },
    "palette": {"manual_palette_coords", "manual_mix_enabled", "manual_mix_alpha", "manual_mix_canvas_rgb"},
    "layers": {"draw_with_layers_enabled", "target_app_layer_coords"},
    "image": {"IMAGE_PATH", "source_is_clipboard_placeholder"},
}


def collect_payload(service, settings_page, categories: Set[str], *, strict=False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if service is None:
        return payload
    try:
        snapshotter = getattr(service, "snapshot_painter_config", None)
        if callable(snapshotter):
            full_cfg = snapshotter()
        else:
            full_cfg = service.get_config()
    except Exception:
        if strict:
            raise
        full_cfg = {}
    if not isinstance(full_cfg, dict):
        full_cfg = {}

    painter_data: Dict[str, Any] = {}
    remaining_keys = set(full_cfg.keys())
    for category, keys in PAINTER_CATEGORY_KEYS.items():
        remaining_keys.difference_update(keys)
        if category not in categories:
            continue
        for key in keys:
            if key in full_cfg:
                painter_data[key] = deepcopy(full_cfg[key])

    if "drawing" in categories:
        for key in remaining_keys:
            if key == "draw_region":
                continue
            painter_data[key] = deepcopy(full_cfg.get(key))

    if painter_data:
        payload["painter"] = painter_data

    if "image" in categories:
        exporter = getattr(service, "export_image_state", None)
        if callable(exporter):
            try:
                image_state = exporter()
            except Exception:
                if strict:
                    raise
                image_state = {}
            if isinstance(image_state, dict) and image_state:
                session_section = payload.setdefault("session", {})
                if isinstance(session_section, dict):
                    session_section["image"] = deepcopy(image_state)

    if "hotkeys" in categories:
        try:
            hotkeys = service.get_hotkeys()
        except Exception:
            if strict:
                raise
            hotkeys = {}
        if isinstance(hotkeys, dict):
            payload["hotkeys"] = deepcopy(hotkeys)

    if "place" in categories and settings_page is not None:
        try:
            payload["place"] = deepcopy(settings_page.export_place_state())
        except Exception:
            if strict:
                raise
            payload["place"] = {}

    return payload


def apply_payload(service, payload: Dict[str, Any], *, settings_page=None) -> Dict[str, Tuple[bool, str]]:
    results: Dict[str, Tuple[bool, str]] = {}
    if service is None or not isinstance(payload, dict):
        return results

    painter_section = payload.get("painter")
    session_section = payload.get("session")
    image_state = None
    if isinstance(session_section, dict):
        candidate = session_section.get("image")
        if isinstance(candidate, dict) and candidate:
            image_state = deepcopy(candidate)
    if isinstance(painter_section, dict) and painter_section:
        try:
            if image_state and callable(getattr(service, "restore_session_state", None)):
                service.restore_session_state(
                    {
                        "image": image_state,
                        "painter": deepcopy(painter_section),
                    }
                )
            else:
                current = service.snapshot_painter_config() if callable(getattr(service, "snapshot_painter_config", None)) else service.get_config()
                merged = deepcopy(current) if isinstance(current, dict) else {}
                merged.update(deepcopy(painter_section))
                if image_state:
                    image_restored = False
                    restore_image = getattr(service, "restore_image_state", None)
                    if callable(restore_image):
                        try:
                            image_restored = bool(restore_image(image_state))
                        except Exception:
                            image_restored = False
                    if image_restored:
                        merged.pop("IMAGE_PATH", None)
                        merged.pop("source_is_clipboard_placeholder", None)
                service.load_config(merged)
            results["painter"] = (True, "")
        except Exception as exc:
            results["painter"] = (False, str(exc))
    elif image_state and callable(getattr(service, "restore_image_state", None)):
        try:
            service.restore_image_state(image_state)
            results["image"] = (True, "")
        except Exception as exc:
            results["image"] = (False, str(exc))

    hotkeys_section = payload.get("hotkeys")
    if isinstance(hotkeys_section, dict):
        try:
            from ui.helpers.hotkey_definitions import migrate_legacy_hotkeys
            ok, message = service.apply_hotkeys(migrate_legacy_hotkeys(hotkeys_section))
        except Exception as exc:
            ok, message = False, str(exc)
        results["hotkeys"] = (ok, message or "")

    place_section = payload.get("place")
    if settings_page is not None and isinstance(place_section, dict):
        try:
            settings_page.apply_place_state(place_section)
            results["place"] = (True, "")
        except Exception as exc:
            results["place"] = (False, str(exc))

    return results
