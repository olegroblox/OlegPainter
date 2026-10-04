from __future__ import annotations

import ctypes
import itertools
import sys
import threading
from ctypes import wintypes
from typing import Callable, Iterable

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence
import logging

log = logging.getLogger("olegpainter.ui.helpers.global_hotkey")


# Fallback ctypes aliases for Windows types that might be absent in ctypes.wintypes.
if ctypes.sizeof(ctypes.c_void_p) == 8:
    ULONG_PTR = ctypes.c_uint64
    LONG_PTR = ctypes.c_int64
else:
    ULONG_PTR = ctypes.c_uint32
    LONG_PTR = ctypes.c_int32
LRESULT = getattr(wintypes, "LRESULT", LONG_PTR)
HHOOK = getattr(wintypes, "HHOOK", wintypes.HANDLE)
HINSTANCE = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


_LowLevelProc = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class HotkeyRegistrationError(RuntimeError):
    """Raised when native hotkey registration fails."""

    def __init__(self, sequence: str, error_code: int, message: str | None = None):
        self.sequence = sequence
        self.error_code = error_code
        msg = message or f"RegisterHotKey failed for '{sequence}' (error {error_code})."
        super().__init__(msg)


class GlobalHotkeyManager(QObject, QAbstractNativeEventFilter):
    """Register and dispatch WM_HOTKEY events for the Qt application."""

    WM_HOTKEY = 0x0312
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105

    HC_ACTION = 0
    WH_KEYBOARD_LL = 13
    LLKHF_UP = 0x80

    VK_F1 = 0x70
    VK_F24 = 0x87

    # Выключается на время записи нового хоткея: иначе съеденная F1 не дойдёт
    # до поля захвата и назначить её будет нечем.
    suppression_enabled = True
    dispatch_enabled = True

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    MOD_NOREPEAT = 0x4000

    ERROR_INVALID_PARAMETER = 87

    _MODIFIER_FOR_VK = {
        0x10: MOD_SHIFT, 0xA0: MOD_SHIFT, 0xA1: MOD_SHIFT,
        0x11: MOD_CONTROL, 0xA2: MOD_CONTROL, 0xA3: MOD_CONTROL,
        0x12: MOD_ALT, 0xA4: MOD_ALT, 0xA5: MOD_ALT,
        0x5B: MOD_WIN, 0x5C: MOD_WIN,
    }

    _RUS_TO_EN = {
        "\u0439": "q", "\u0446": "w", "\u0443": "e", "\u043a": "r", "\u0435": "t", "\u043d": "y", "\u0433": "u", "\u0448": "i", "\u0449": "o", "\u0437": "p", "\u0445": "[", "\u044a": "]",
        "\u0444": "a", "\u044b": "s", "\u0432": "d", "\u0430": "f", "\u043f": "g", "\u0440": "h", "\u043e": "j", "\u043b": "k", "\u0434": "l", "\u0436": ";", "\u044d": "'",
        "\u044f": "z", "\u0447": "x", "\u0441": "c", "\u043c": "v", "\u0438": "b", "\u0442": "n", "\u044c": "m", "\u0431": ",", "\u044e": ".", ".": "/",
    }

    _SPECIAL_QT_KEYS = {
        Qt.Key_F1: 0x70,
        Qt.Key_F2: 0x71,
        Qt.Key_F3: 0x72,
        Qt.Key_F4: 0x73,
        Qt.Key_F5: 0x74,
        Qt.Key_F6: 0x75,
        Qt.Key_F7: 0x76,
        Qt.Key_F8: 0x77,
        Qt.Key_F9: 0x78,
        Qt.Key_F10: 0x79,
        Qt.Key_F11: 0x7A,
        Qt.Key_F12: 0x7B,
        Qt.Key_F13: 0x7C,
        Qt.Key_F14: 0x7D,
        Qt.Key_F15: 0x7E,
        Qt.Key_F16: 0x7F,
        Qt.Key_F17: 0x80,
        Qt.Key_F18: 0x81,
        Qt.Key_F19: 0x82,
        Qt.Key_F20: 0x83,
        Qt.Key_F21: 0x84,
        Qt.Key_F22: 0x85,
        Qt.Key_F23: 0x86,
        Qt.Key_F24: 0x87,
        Qt.Key_Escape: 0x1B,
        Qt.Key_Tab: 0x09,
        Qt.Key_Backspace: 0x08,
        Qt.Key_Return: 0x0D,
        Qt.Key_Enter: 0x0D,
        Qt.Key_Space: 0x20,
        Qt.Key_Left: 0x25,
        Qt.Key_Up: 0x26,
        Qt.Key_Right: 0x27,
        Qt.Key_Down: 0x28,
        Qt.Key_PageUp: 0x21,
        Qt.Key_PageDown: 0x22,
        Qt.Key_Home: 0x24,
        Qt.Key_End: 0x23,
        Qt.Key_Insert: 0x2D,
        Qt.Key_Delete: 0x2E,
        Qt.Key_Print: 0x2C,
        Qt.Key_Pause: 0x13,
        Qt.Key_ScrollLock: 0x91,
        Qt.Key_CapsLock: 0x14,
    }

    _CHAR_OVERRIDES = {
        "": (0xC0, 0), "~": (0xC0, MOD_SHIFT),
        "-": (0xBD, 0), "_": (0xBD, MOD_SHIFT),
        "=": (0xBB, 0), "+": (0xBB, MOD_SHIFT),
        "[": (0xDB, 0), "{": (0xDB, MOD_SHIFT),
        "]": (0xDD, 0), "}": (0xDD, MOD_SHIFT),
        ";": (0xBA, 0), ":": (0xBA, MOD_SHIFT),
        "'": (0xDE, 0), "\"": (0xDE, MOD_SHIFT),
        ",": (0xBC, 0), "<": (0xBC, MOD_SHIFT),
        ".": (0xBE, 0), ">": (0xBE, MOD_SHIFT),
        "/": (0xBF, 0), "?": (0xBF, MOD_SHIFT),
        "\\": (0xDC, 0), "|": (0xDC, MOD_SHIFT),
    }

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.available = sys.platform.startswith("win")
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._registrations: dict[int, str] = {}
        self._next_id = itertools.count(1)
        self._installed = False
        self._layouts: list[int] = []

        self._hook_handle = None
        self._hook_proc = None
        self._hook_map: dict[tuple[int, int], list[int]] = {}
        self._hook_id_map: dict[int, tuple[int, int]] = {}
        self._hook_active_ids: set[int] = set()
        self._hook_pressed_keys: set[int] = set()
        self._hook_pressed_mod_keys: set[int] = set()
        self._hook_mod_state = 0
        self._suppressed_keys: set[int] = set()   # vk, чей нажим мы съели (см. _should_suppress)
        self._hook_lock = threading.RLock()

        if not self.available:
            self._user32 = None
            self._kernel32 = None
            return

        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except Exception:
            self.available = False
            self._user32 = None
            self._kernel32 = None
            return

        self._setup_win32_prototypes()
        self._layouts = self._enumerate_layouts()
        self._install_filter()

    def clear(self) -> None:
        if not self.available or not self._user32:
            self._callbacks.clear()
            self._registrations.clear()
            with self._hook_lock:
                self._release_hook_locked()
            return

        for hotkey_id, mode in list(self._registrations.items()):
            if mode == "native":
                try:
                    self._user32.UnregisterHotKey(None, hotkey_id)
                except Exception:
                    log.debug('ignored exception in self._user32.UnregisterHotKey(None, hotkey_id)', exc_info=True)
            elif mode == "hook":
                self._remove_hook_registration(hotkey_id)
            self._callbacks.pop(hotkey_id, None)
            self._registrations.pop(hotkey_id, None)

        with self._hook_lock:
            self._release_hook_locked()

    def register(self, sequence: str, callback: Callable[[], None]) -> int:
        if not self.available or not self._user32:
            raise HotkeyRegistrationError(sequence, -1, "Native hotkeys unavailable on this platform")

        parsed = self._parse_sequence(sequence)
        if parsed is None:
            raise HotkeyRegistrationError(sequence, -1, "Unsupported hotkey sequence")

        mods, vk = parsed
        hotkey_id = next(self._next_id)

        if self._register_hook_hotkey(hotkey_id, mods, vk, callback):
            self._callbacks[hotkey_id] = callback
            return hotkey_id

        raise HotkeyRegistrationError(sequence, ctypes.get_last_error() or -1)

    def replace(self, bindings: list[tuple[str, Callable[[], None]]]) -> list[int]:
        """Validate the entire profile before atomically replacing dispatch tables.

        Keep the hook and pressed-key bookkeeping alive: rebinding while a key is
        held must neither fire its new action nor leak an unmatched release.
        """
        parsed = []
        seen = set()
        for sequence, callback in bindings:
            combo = self._parse_sequence(sequence)
            if combo is None:
                raise HotkeyRegistrationError(sequence, -1, f"сочетание {sequence} нельзя назначить глобально")
            mods, vk = combo
            key = (mods & ~self.MOD_NOREPEAT, vk)
            if key in seen:
                raise HotkeyRegistrationError(
                    sequence, -1, f"клавиша {sequence} уже занята другим действием (в другой раскладке)")
            seen.add(key)
            parsed.append((key, callback))
        if parsed and not self._ensure_hook():
            raise HotkeyRegistrationError("", ctypes.get_last_error() or -1)
        with self._hook_lock:
            ids = [next(self._next_id) for _ in parsed]
            self._hook_map = {key: [hid] for hid, (key, _) in zip(ids, parsed)}
            self._hook_id_map = {hid: key for hid, (key, _) in zip(ids, parsed)}
            self._callbacks = {hid: callback for hid, (_, callback) in zip(ids, parsed)}
            self._registrations = {hid: "hook" for hid in ids}
            self._hook_active_ids.clear()
        return ids

    def nativeEventFilter(self, event_type: bytes, message: int) -> tuple[bool, int]:
        if not self.available or event_type != b"windows_generic_MSG":
            return False, 0
        msg = ctypes.cast(message, ctypes.POINTER(_MSG)).contents
        if msg.message == self.WM_HOTKEY:
            callback = self._callbacks.get(int(msg.wParam))
            if callback and self.dispatch_enabled:
                try:
                    callback()
                except Exception:
                    log.debug('ignored exception in callback()', exc_info=True)
            return True, 0
        return False, 0

    def _install_filter(self) -> None:
        if self._installed:
            return
        app = QCoreApplication.instance()
        if app is None:
            QTimer.singleShot(0, self._install_filter)
            return
        app.installNativeEventFilter(self)
        self._installed = True

    def _setup_win32_prototypes(self) -> None:
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        self._user32.GetKeyboardLayout.restype = wintypes.HKL
        self._user32.GetKeyboardLayoutList.argtypes = [wintypes.INT, ctypes.POINTER(wintypes.HKL)]
        self._user32.GetKeyboardLayoutList.restype = wintypes.UINT
        self._user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HKL]
        self._user32.VkKeyScanExW.restype = ctypes.c_short
        self._user32.SetWindowsHookExW.argtypes = [wintypes.INT, _LowLevelProc, HINSTANCE, wintypes.DWORD]
        self._user32.SetWindowsHookExW.restype = HHOOK
        self._user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.CallNextHookEx.restype = LRESULT
        self._user32.UnhookWindowsHookEx.argtypes = [HHOOK]
        self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    def _enumerate_layouts(self) -> list[int]:
        count = self._user32.GetKeyboardLayoutList(0, None)
        if not count:
            current = self._user32.GetKeyboardLayout(0)
            return [current] if current else []
        arr = (wintypes.HKL * count)()
        fetched = self._user32.GetKeyboardLayoutList(count, arr)
        if not fetched:
            current = self._user32.GetKeyboardLayout(0)
            return [current] if current else []
        return [int(arr[i]) for i in range(fetched)]

    def _register_hook_hotkey(self, hotkey_id: int, mods: int, vk: int, callback: Callable[[], None]) -> bool:
        if not self._ensure_hook():
            return False
        hook_mods = mods & ~self.MOD_NOREPEAT
        key = (hook_mods, vk)
        with self._hook_lock:
            ids = self._hook_map.setdefault(key, [])
            if hotkey_id not in ids:
                ids.append(hotkey_id)
            self._hook_id_map[hotkey_id] = key
            self._registrations[hotkey_id] = "hook"
        self._callbacks[hotkey_id] = callback
        return True

    def _ensure_hook(self) -> bool:
        if self._hook_handle:
            return True
        if not self._user32 or not self._kernel32:
            return False
        proc = _LowLevelProc(self._keyboard_hook_proc)
        h_instance = self._kernel32.GetModuleHandleW(None)
        handle = self._user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, proc, h_instance, 0)
        if not handle:
            return False
        self._hook_proc = proc
        self._hook_handle = handle
        return True

    def _keyboard_hook_proc(self, nCode: int, wParam: int, lParam: int):
        if nCode != self.HC_ACTION or not self._hook_handle:
            return self._user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)

        kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
        vk = int(kb.vkCode)
        flag_up = bool(kb.flags & self.LLKHF_UP)
        is_keydown = not flag_up and wParam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN)
        is_keyup = flag_up or wParam in (self.WM_KEYUP, self.WM_SYSKEYUP)

        callbacks_to_fire: list[Callable[[], None]] = []
        suppress = False

        with self._hook_lock:
            if is_keydown:
                if vk in self._MODIFIER_FOR_VK:
                    self._hook_pressed_mod_keys.add(vk)
                    self._recompute_hook_mods()
                already_pressed = vk in self._hook_pressed_keys
                self._hook_pressed_keys.add(vk)
                if not already_pressed and vk not in self._MODIFIER_FOR_VK and self.dispatch_enabled:
                    mods = self._hook_mod_state
                    key = (mods, vk)
                    ids = list(self._hook_map.get(key, ()))
                    for hotkey_id in ids:
                        if hotkey_id in self._hook_active_ids:
                            continue
                        callback = self._callbacks.get(hotkey_id)
                        if callback:
                            self._hook_active_ids.add(hotkey_id)
                            callbacks_to_fire.append(callback)
                    if callbacks_to_fire and self._should_suppress(mods, vk):
                        suppress = True
                        self._suppressed_keys.add(vk)
                elif already_pressed and vk in self._suppressed_keys:
                    suppress = True          # автоповтор удерживаемой клавиши
            elif is_keyup:
                if vk in self._suppressed_keys:
                    self._suppressed_keys.discard(vk)
                    suppress = True          # без этого окно получит «отпускание» без нажатия
                self._hook_pressed_keys.discard(vk)
                if vk in self._MODIFIER_FOR_VK:
                    self._hook_pressed_mod_keys.discard(vk)
                    self._recompute_hook_mods()
                    affected = [hid for hid in list(self._hook_active_ids)
                                if self._MODIFIER_FOR_VK.get(vk, 0) & self._hook_id_map.get(hid, (0, 0))[0]]
                    for hid in affected:
                        self._hook_active_ids.discard(hid)
                else:
                    for hid, combo in list(self._hook_id_map.items()):
                        if combo[1] == vk:
                            self._hook_active_ids.discard(hid)

        for cb in callbacks_to_fire:
            try:
                cb()
            except Exception:
                log.debug('ignored exception in cb()', exc_info=True)

        if suppress:
            return 1     # клавишу съели — активное окно её не увидит

        return self._user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)

    def _should_suppress(self, mods: int, vk: int) -> bool:
        """Съедать ли клавишу, раз она сработала как глобальный хоткей.

        Съедаем функциональные клавиши и сочетания с модификаторами: иначе F1 в окне
        браузера откроет справку, F3 — поиск, F5 перезагрузит страницу, а Ctrl+Shift+P
        откроет приватное окно ПОМИМО нашего действия (в любой чужой программе — то же
        самое). Печатные одиночные клавиши НЕ трогаем: подавление сломало бы набор этих
        символов во всей системе. Поэтому их и не назначаем по умолчанию: «[» и «]» —
        это русские Х/Ъ, запись действий включалась при наборе текста (HOTKEYS-002).
        """
        if not self.suppression_enabled:
            return False
        if mods:
            return True
        return self.VK_F1 <= vk <= self.VK_F24

    def set_suppression_enabled(self, enabled: bool) -> None:
        """Pause dispatch AND suppression while the UI records a new binding."""
        with self._hook_lock:
            self.suppression_enabled = bool(enabled)
            self.dispatch_enabled = bool(enabled)
            # Keep swallowed key-downs paired with their releases, even if the
            # capture dialog opened between those two events.

    def _recompute_hook_mods(self) -> None:
        mask = 0
        for vk in self._hook_pressed_mod_keys:
            mask |= self._MODIFIER_FOR_VK.get(vk, 0)
        self._hook_mod_state = mask

    def _remove_hook_registration(self, hotkey_id: int) -> None:
        with self._hook_lock:
            combo = self._hook_id_map.pop(hotkey_id, None)
            if combo:
                ids = self._hook_map.get(combo)
                if ids and hotkey_id in ids:
                    ids.remove(hotkey_id)
                    if not ids:
                        self._hook_map.pop(combo, None)
            self._hook_active_ids.discard(hotkey_id)
            if not self._hook_map:
                self._release_hook_locked()

    def _release_hook_locked(self) -> None:
        handle = self._hook_handle
        self._hook_handle = None
        self._hook_proc = None
        self._hook_map.clear()
        self._hook_id_map.clear()
        self._hook_active_ids.clear()
        self._hook_pressed_keys.clear()
        self._hook_pressed_mod_keys.clear()
        self._hook_mod_state = 0
        if handle:
            try:
                self._user32.UnhookWindowsHookEx(handle)
            except Exception:
                log.debug('ignored exception in self._user32.UnhookWindowsHookEx(handle)', exc_info=True)

    def _parse_sequence(self, sequence: str) -> tuple[int, int] | None:
        seq = QKeySequence(sequence)
        if seq.count() != 1:
            return None
        combo = seq[0]
        key = combo.key()
        if not key:
            return None

        def _flag_value(flag):
            try:
                return int(flag)
            except TypeError:
                return int(getattr(flag, "value", 0))

        mods_obj = combo.keyboardModifiers()
        mods_qt = _flag_value(mods_obj)
        mods = 0
        if mods_qt & _flag_value(Qt.ControlModifier):
            mods |= self.MOD_CONTROL
        if mods_qt & _flag_value(Qt.AltModifier):
            mods |= self.MOD_ALT
        if mods_qt & _flag_value(Qt.ShiftModifier):
            mods |= self.MOD_SHIFT
        if mods_qt & _flag_value(Qt.MetaModifier):
            mods |= self.MOD_WIN
        if mods:
            mods |= self.MOD_NOREPEAT

        vk, extra = self._qt_key_to_vk(key)
        if vk is None:
            return None
        mods |= extra
        return mods, vk

    def _qt_key_to_vk(self, key: int) -> tuple[int | None, int]:
        if key in self._SPECIAL_QT_KEYS:
            return self._SPECIAL_QT_KEYS[key], 0
        if key <= 0x10FFFF:
            char = chr(key)
            vk, extra = self._char_to_vk(char)
            if vk is not None:
                return vk, extra
        return None, 0

    def _char_to_vk(self, char: str) -> tuple[int | None, int]:
        if not char:
            return None, 0
        for candidate in self._char_candidates(char):
            vk, extra = self._scan_char(candidate)
            if vk is not None:
                return vk, extra
            override = self._CHAR_OVERRIDES.get(candidate)
            if override:
                return override
            if candidate.isalpha() and len(candidate) == 1 and ord(candidate) < 128:
                return ord(candidate.upper()), 0
        return None, 0

    def _char_candidates(self, char: str) -> Iterable[str]:
        lower = char.lower()
        upper = char.upper()
        base = self._RUS_TO_EN.get(lower, lower)
        yield lower
        if upper != lower:
            yield upper
        if base != lower:
            yield base
            base_upper = base.upper()
            if base_upper != base:
                yield base_upper
        if char not in (lower, upper):
            yield char

    def _scan_char(self, char: str) -> tuple[int | None, int]:
        if not char or len(char) != 1:
            return None, 0
        extra = 0
        layouts = self._layouts or [int(self._user32.GetKeyboardLayout(0))]
        for layout in layouts:
            if not layout:
                continue
            res = self._user32.VkKeyScanExW(char, layout)
            if res == -1:
                continue
            vk = res & 0xFF
            shift_state = (res >> 8) & 0xFF
            if shift_state & 1:
                extra |= self.MOD_SHIFT
            if shift_state & 2:
                extra |= self.MOD_CONTROL
            if shift_state & 4:
                extra |= self.MOD_ALT
            return vk, extra
        return None, 0

    def __del__(self):
        try:
            self.clear()
        except Exception:
            log.debug('ignored exception in self.clear()', exc_info=True)
