import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from infrastructure import documents
from ui.helpers.config_store import ConfigStore
from application.session import SessionStore


def test_replace_failure_keeps_primary_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / "preset.json"
    documents.write_document(path, {"version": 1})
    replace = documents.os.replace

    def fail_primary(source, target):
        if target == path:
            raise OSError("disk failure")
        replace(source, target)

    monkeypatch.setattr(documents.os, "replace", fail_primary)
    with pytest.raises(OSError):
        documents.write_document(path, {"version": 2})
    assert json.loads(path.read_text()) == {"version": 1}
    assert documents.read_document(documents.backup_path(path)) == {"version": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_flush_does_not_replace_valid_data(tmp_path, monkeypatch):
    path = tmp_path / "preset.json"
    documents.write_document(path, {"version": 1})
    def fail(_fd):
        raise OSError("flush failure")
    monkeypatch.setattr(documents.os, "fsync", fail)
    with pytest.raises(OSError):
        documents.write_document(path, {"version": 2})
    assert documents.read_document(path) == {"version": 1}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("damage", [b"{broken", b"[]", b'{"x": NaN}', b'{"x": 1e400}', b"\xff"])
def test_recovery_preserves_corrupt_file_and_reports_it(tmp_path, caplog, damage):
    path = tmp_path / "preset.json"
    documents.write_document(path, {"version": 1})
    documents.write_document(path, {"version": 2})
    path.write_bytes(damage)
    assert documents.read_document(path) == {"version": 1}
    assert list(tmp_path.glob("*.corrupt-*"))[0].read_bytes() == damage
    assert "Recovered document" in caplog.text
    assert json.loads(path.read_text()) == {"version": 1}


def test_both_versions_damaged_are_not_silently_reset(tmp_path):
    path = tmp_path / "preset.json"
    path.write_text("broken")
    documents.backup_path(path).write_text("also broken")
    with pytest.raises(ValueError):
        documents.write_document(path, {"new": True})
    assert path.read_text() == "broken"
    assert documents.backup_path(path).read_text() == "also broken"


def test_serialization_failure_does_not_touch_primary_or_backup(tmp_path):
    path = tmp_path / "preset.json"
    documents.write_document(path, {"version": 1})
    with pytest.raises(ValueError):
        documents.write_document(path, {"invalid": float("nan")})
    assert documents.read_document(path) == {"version": 1}
    assert not documents.backup_path(path).exists()


def test_concurrent_writers_produce_complete_documents(tmp_path):
    path = tmp_path / "preset.json"
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda n: documents.write_document(path, {"id": n, "data": [n] * 100}), range(30)))
    for version in (path, documents.backup_path(path)):
        result = documents.read_document(version)
        assert result["data"] == [result["id"]] * 100
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("slug", ["../outside", "..\\outside", "C:outside", "a/b", "a\\b", "", ".", "bad\x00"])
def test_config_operations_cannot_escape_store(tmp_path, slug):
    store = ConfigStore(tmp_path)
    for operation in (store.load, store.delete):
        with pytest.raises(ValueError):
            operation(slug)


def test_delete_removes_recovery_version(tmp_path):
    store = ConfigStore(tmp_path)
    item = store.save_new("example", [], {})
    store.update(item.slug, "example", [], {})
    assert documents.backup_path(item.path).exists()
    store.delete(item.slug)
    with pytest.raises(FileNotFoundError):
        store.load(item.slug)


def test_missing_autosave_can_be_recovered(tmp_path):
    store = SessionStore(ConfigStore(tmp_path))
    documents.write_document(store.session_path, {"payload": {"session": {"marker": 1}}})
    documents.write_document(store.session_path, {"payload": {"session": {"marker": 2}}})
    store.session_path.unlink()
    assert store.load_document()["payload"]["session"]["marker"] == 1


def test_stencil_storage_uses_isolated_directory_and_recovers(tmp_path, monkeypatch):
    from ui.kalka.state import AppState, state_file
    monkeypatch.setenv("OLEGPAINTER_CONFIG_DIR", str(tmp_path))
    state = AppState()
    state.window.x = 123
    state.save()
    state.window.x = 456
    state.save()
    assert state_file().is_relative_to(tmp_path)
    state_file().write_text("broken")
    assert AppState.load().window.x == 123
