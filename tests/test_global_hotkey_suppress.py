# -*- coding: utf-8 -*-
"""Глобальный хоткей не должен «протекать» в активное окно.

Иначе F1 срабатывает у нас И открывает справку браузера/целевой программы,
F5 заодно перезагружает страницу, а Ctrl+Shift+P открывает приватное окно.
Исключение — печатные клавиши: их подавление сломало бы набор текста в системе.
"""
import threading

from ui.helpers.global_hotkey import GlobalHotkeyManager

VK_LEFT_BRACKET = 0xDB
VK_RIGHT_BRACKET = 0xDD
VK_P = 0x50
VK_F1 = 0x70
VK_F5 = 0x74
VK_F24 = 0x87


def _manager() -> GlobalHotkeyManager:
    # без __init__: он ставит Win32-хук и просит QCoreApplication, а нам нужна чистая логика
    return GlobalHotkeyManager.__new__(GlobalHotkeyManager)


def test_function_keys_are_eaten():
    mgr = _manager()
    for vk in (VK_F1, VK_F5, VK_F24):
        assert mgr._should_suppress(0, vk) is True


def test_combos_with_modifiers_are_eaten():
    mgr = _manager()
    mods = GlobalHotkeyManager.MOD_CONTROL | GlobalHotkeyManager.MOD_SHIFT
    assert mgr._should_suppress(mods, VK_P) is True
    assert mgr._should_suppress(GlobalHotkeyManager.MOD_ALT, VK_F1) is True


def test_capture_pause_lets_keys_through():
    # пока пользователь записывает новый хоткей, клавиша обязана дойти до поля захвата
    mgr = _manager()
    mgr._hook_lock = threading.RLock()
    mgr._suppressed_keys = set()
    mgr.set_suppression_enabled(False)
    assert mgr._should_suppress(0, VK_F1) is False
    mgr.set_suppression_enabled(True)
    assert mgr._should_suppress(0, VK_F1) is True


def test_printable_keys_pass_through():
    # «[» и «]» — размер кисти по умолчанию; съедать их нельзя, иначе эти символы
    # перестанут набираться где угодно в системе
    mgr = _manager()
    assert mgr._should_suppress(0, VK_LEFT_BRACKET) is False
    assert mgr._should_suppress(0, VK_RIGHT_BRACKET) is False
    assert mgr._should_suppress(0, VK_P) is False
