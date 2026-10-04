"""Profile transactions and capture dispatch, without installing a system hook."""
import ctypes
import itertools
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from ui.helpers.global_hotkey import GlobalHotkeyManager, _KBDLLHOOKSTRUCT
from ui.services.painter_service import PainterService
from engine.olegpainter.drawing_events import DrawingPhase


@pytest.fixture
def service():
    app = QApplication.instance() or QApplication([])
    service = PainterService()
    yield service
    service.shutdown()
    service.deleteLater()
    app.processEvents()


def fake_manager():
    manager = GlobalHotkeyManager.__new__(GlobalHotkeyManager)
    manager.available = True
    manager._hook_handle = 1
    manager._hook_lock = threading.RLock()
    manager._next_id = itertools.count(1)
    manager._hook_map = {}
    manager._hook_id_map = {}
    manager._hook_active_ids = set()
    manager._hook_pressed_keys = set()
    manager._hook_pressed_mod_keys = set()
    manager._hook_mod_state = 0
    manager._suppressed_keys = set()
    manager._callbacks = {}
    manager._registrations = {}
    manager._user32 = SimpleNamespace(CallNextHookEx=lambda *args: 0, UnhookWindowsHookEx=lambda *args: 1)
    return manager


def key(manager, vk, *, up=False):
    data = _KBDLLHOOKSTRUCT(vkCode=vk, flags=manager.LLKHF_UP if up else 0)
    return manager._keyboard_hook_proc(0, manager.WM_KEYUP if up else manager.WM_KEYDOWN,
                                       ctypes.addressof(data))


def test_capture_neither_fires_old_action_nor_repeats_after_rebind():
    manager = fake_manager()
    called = []
    manager.replace([('F1', lambda: called.append('old'))])
    manager.set_suppression_enabled(False)
    assert key(manager, 0x70) == 0
    manager.replace([('F1', lambda: called.append('new'))])
    manager.set_suppression_enabled(True)
    assert key(manager, 0x70) == 0  # auto-repeat of the captured key
    assert called == []
    key(manager, 0x70, up=True)
    assert key(manager, 0x70) == 1
    assert called == ['new']
    assert key(manager, 0x70, up=True) == 1


def test_capture_does_not_leak_release_of_previously_swallowed_key():
    manager = fake_manager()
    manager.replace([('F1', lambda: None)])
    assert key(manager, 0x70) == 1
    manager.set_suppression_enabled(False)
    assert key(manager, 0x70, up=True) == 1
    assert key(manager, 0x70) == 0
    assert key(manager, 0x70, up=True) == 0


def test_native_profile_replacement_is_all_or_nothing():
    manager = fake_manager()
    called = []
    manager.replace([('F1', lambda: called.append('old'))])
    with pytest.raises(RuntimeError):
        manager.replace([('F2', lambda: None), ('F3, F4', lambda: None)])
    assert key(manager, 0x70) == 1
    assert called == ['old']
    with pytest.raises(RuntimeError, match='уже занята'):
        manager.replace([('F2', lambda: None), ('F2', lambda: None)])


@pytest.mark.parametrize('scope,code,sequence', [
    ('app', 'open_file', 'F1'),  # global and app scopes overlap
    ('global', 'select_area', 'Ctrl+O'),
    ('app', 'show_help', 'Ctrl+V'),
    ('global', 'stop', 'F2'),
    ('app', 'open_file', 'Ctrl+O, F7'),
    ('app', 'open_file', 'not a key'),
    ('app', 'open_file', None),
    ('unknown', 'open_file', 'F7'),
])
def test_invalid_profile_does_not_change_or_publish_state(service, scope, code, sequence):
    before = service.get_hotkeys()
    events = []
    service.hotkeysChanged.connect(events.append)
    ok, message = service.apply_hotkeys({scope: {code: sequence}})
    assert not ok and message
    assert service.get_hotkeys() == before
    assert events == []


def test_atomic_swap_disable_and_reset(service):
    assert service.apply_hotkeys({'global': {'select_area': 'F2', 'toggle_stencil': 'F1'}})[0]
    assert service.set_hotkey('select_area', '')[0]
    assert service.get_hotkeys()['global']['select_area'] == ''
    # Resetting one binding would collide with the other swapped binding.
    assert not service.reset_hotkey('select_area')[0]
    assert service.reset_all_hotkeys()[0]
    assert service.get_hotkeys()['global']['select_area'] == 'F1'


@pytest.mark.parametrize('first,second', [
    ('Return', 'Enter'), ('Ctrl+?', 'Ctrl+Shift+/'), ('Ctrl+Ж', 'Ctrl+;'),
])
def test_physical_aliases_conflict_across_scopes(service, first, second):
    assert service.set_hotkey('select_area', first)[0]
    assert not service.set_hotkey('open_file', second)[0]


def test_capture_and_drawing_cannot_disable_each_others_controls(service, monkeypatch):
    for phase in (DrawingPhase.RUNNING, DrawingPhase.PAUSED, DrawingPhase.STOPPING):
        service.drawing_phase = phase
        with pytest.raises(ValueError, match='Остановите рисование'):
            token = service.begin_hotkey_capture()
        assert not getattr(service, '_hotkey_capture_active', False)
    service.drawing_phase = DrawingPhase.IDLE
    token = service.begin_hotkey_capture()
    started = []
    monkeypatch.setattr(service, '_preflight_check', lambda: started.append(True))
    service.start_pause()
    assert started == []
    service.end_hotkey_capture(token)


def test_hotkey_recording_shares_capture_ownership(service):
    from infrastructure.capture_session import CaptureSession, automation_input
    from infrastructure.input_ownership import InputBusyError
    capture = CaptureSession("palette")
    try:
        with pytest.raises(InputBusyError):
            service.begin_hotkey_capture()
        assert not service._hotkey_capture_active
    finally:
        capture.close()
    token = service.begin_hotkey_capture()
    with pytest.raises(InputBusyError):
        with automation_input.claim(object(), "drawing"):
            pytest.fail("drawing entered hotkey capture")
    service.end_hotkey_capture(token)
    assert automation_input.current is None


def test_registration_failure_preserves_service_profile_and_emits_no_success(service, monkeypatch):
    before = service.get_hotkeys()
    events = []
    service.hotkeysChanged.connect(events.append)
    monkeypatch.setattr(service, '_headless_qt_platform', False)
    def fail(bindings):
        raise RuntimeError('hook unavailable')
    monkeypatch.setattr(service, '_hotkey_manager', SimpleNamespace(available=True, replace=fail, clear=lambda: None))
    ok, message = service.set_hotkey('select_area', 'F11')
    assert not ok and 'hook unavailable' in message
    assert service.get_hotkeys() == before
    assert events == []


def test_queued_events_do_not_cross_capture_or_profile_change(service, monkeypatch):
    queued, called, bindings = [], [], []
    manager = SimpleNamespace(available=True, clear=lambda: None,
                              set_suppression_enabled=lambda value: None)
    def replace(items):
        bindings[:] = items
        return list(range(len(items)))
    manager.replace = replace
    monkeypatch.setattr(service, '_headless_qt_platform', False)
    monkeypatch.setattr(service, '_hotkey_manager', manager)
    monkeypatch.setattr(service, '_invoke_on_qt', queued.append)
    monkeypatch.setattr(service, 'select_area', lambda: called.append('area'))
    assert service._register_global_hotkeys()[0]
    handler = dict(bindings)['F1']
    handler()
    token = service.begin_hotkey_capture()
    service.end_hotkey_capture(token)
    queued.pop()()
    assert not called
    handler()
    assert service.set_hotkey('select_area', 'F11')[0]
    queued.pop()()
    assert not called
    dict(bindings)['F11']()
    queued.pop()()
    assert called == ['area']


def test_old_cancel_and_commit_cannot_end_or_modify_new_recording(service):
    first = service.begin_hotkey_capture()
    assert service.end_hotkey_capture(first)
    second = service.begin_hotkey_capture()
    before = service.get_hotkeys()
    assert not service.end_hotkey_capture(first)
    assert not service.commit_hotkey_capture(first, 'select_area', 'F11')[0]
    assert service.get_hotkeys() == before
    assert service.hotkey_capture_state() == {'active': True, 'token': second}
    assert service.commit_hotkey_capture(second, 'select_area', 'F11')[0]
    assert not service._hotkey_capture_active


def test_save_on_close_blocks_dispatch_and_discards_old_events_after_failure(service, monkeypatch):
    manager = fake_manager()
    queued, bindings, called = [], [], []
    def replace(items):
        bindings[:] = items
        return list(range(len(items)))
    manager.replace = replace
    monkeypatch.setattr(service, '_headless_qt_platform', False)
    monkeypatch.setattr(service, '_hotkey_manager', manager)
    monkeypatch.setattr(service, '_invoke_on_qt', queued.append)
    monkeypatch.setattr(service, 'select_area', lambda: called.append('area'))
    assert service._register_global_hotkeys()[0]
    handler = dict(bindings)['F1']
    handler()
    service.set_close_pending(True)
    queued.pop()()
    handler()
    service.set_close_pending(False)
    queued.pop()()
    assert not called
    handler()
    queued.pop()()
    assert called == ['area']


def test_expired_recording_cannot_save_even_before_qt_delivers_timeout(service):
    token = service.begin_hotkey_capture()
    service._hotkey_capture_deadline = 0
    before = service.get_hotkeys()
    ok, error = service.commit_hotkey_capture(token, 'select_area', 'F11')
    assert not ok and 'истекло' in error
    assert service.get_hotkeys() == before
    assert not service._hotkey_capture_active


def test_timeout_restores_dispatch_and_stale_timer_does_not_end_new_session(service):
    changes = []
    service.hotkeyCaptureChanged.connect(changes.append)
    service.begin_hotkey_capture()
    service._hotkey_capture_deadline = 0
    service._hotkey_capture_timer.timeout.emit()
    assert not service._hotkey_capture_active
    second = service.begin_hotkey_capture()
    service._hotkey_capture_timer.timeout.emit()
    assert service.hotkey_capture_state() == {'active': True, 'token': second}
    assert service._hotkey_capture_timer.isActive()
    assert [state['active'] for state in changes] == [True, False, True]


def test_rejected_assignment_ends_recording_and_shutdown_stops_timer(service):
    token = service.begin_hotkey_capture()
    assert not service.commit_hotkey_capture(token, 'open_file', 'F1')[0]
    assert not service._hotkey_capture_active
    service.begin_hotkey_capture()
    service.shutdown()
    assert not service._hotkey_capture_active
    assert not service._hotkey_capture_timer.isActive()


def test_saved_bracket_recording_keys_move_to_f6_f7_unless_taken():
    # HOTKEYS-002: "[" / "]" are the Russian Х/Ъ keys and toggled recording while typing.
    from ui.helpers.hotkey_definitions import default_hotkey_profile, migrate_legacy_hotkeys
    assert default_hotkey_profile()["record_pre_color_actions"] == "F6"
    assert default_hotkey_profile()["record_post_color_actions"] == "F7"
    saved = {"global": {"record_pre_color_actions": "[", "record_post_color_actions": "]", "stop": "F4"},
             "app": {"open_file": "Ctrl+O"}}
    migrated = migrate_legacy_hotkeys(saved)
    assert migrated["global"]["record_pre_color_actions"] == "F6"
    assert migrated["global"]["record_post_color_actions"] == "F7"
    assert saved["global"]["record_pre_color_actions"] == "["  # the stored document is not mutated
    # A user who put another action on F7 keeps the old key rather than a conflict.
    busy = {"global": {"record_pre_color_actions": "[", "record_post_color_actions": "]", "stop": "F7"}}
    migrated = migrate_legacy_hotkeys(busy)
    assert migrated["global"]["record_pre_color_actions"] == "F6"
    assert migrated["global"]["record_post_color_actions"] == "]"
    custom = {"global": {"record_pre_color_actions": "Ctrl+1"}}
    assert migrate_legacy_hotkeys(custom)["global"]["record_pre_color_actions"] == "Ctrl+1"
