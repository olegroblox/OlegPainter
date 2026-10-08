"""DRIVER-001/002: Interception found however and wherever it was installed; the author's
installer downloaded, checked and held; installing, removing and the restart from the program."""
import contextlib
import hashlib
import io
import os
import urllib.error
import zipfile
from types import SimpleNamespace

import pytest

from application.driver_setup import DriverCenter
from infrastructure import input_driver
from infrastructure.input_driver import DriverError, DriverStatus, DriverPart

KB, MS = input_driver.KEYBOARD_CLASS, input_driver.MOUSE_CLASS
SYS = r"C:\Windows\System32\drivers"
EAC = "Easy Anti-Cheat (Fortnite, Apex Legends, Rust…)"


class FakeProbe:
    """What Windows would say, as plain data."""

    def __init__(self, *, filters=None, services=None, files=None, running=(), devices=False, counts=(2, 1),
                 hvci=False, present=(), boot=10_000.0, supported=True):
        self.filters = filters or {}
        self.services = services or {}
        self.files = files or {}
        self.running = set(running)
        self.devices = devices
        self.counts = counts
        self._hvci = hvci
        self.present = set(present)
        self.boot = boot
        self._supported = supported

    def devices_open(self):
        return self.devices

    def device_counts(self):
        return self.counts

    def class_filters(self, class_guid):
        return list(self.filters.get(class_guid, []))

    def service(self, name):
        return self.services.get(name.lower())

    def service_exists(self, name):
        return name in self.present or name.lower() in self.services

    def service_running(self, name):
        return name in self.running

    def file_info(self, path):
        if path not in self.files:
            return False, "", 0.0, ""
        version, modified, product = self.files[path]
        return True, version, modified, product

    def hvci(self):
        return self._hvci

    def boot_time(self):
        return self.boot

    def supported(self):
        return self._supported


def installed(**changes):
    """Interception as its author's installer leaves it, loaded and answering."""
    options = dict(filters={KB: ["keyboard", "kbdclass"], MS: ["mouse", "mouclass"]},
                   services={"keyboard": SYS + r"\keyboard.sys", "mouse": SYS + r"\mouse.sys"},
                   files={SYS + r"\keyboard.sys": ("1.0.0.0", 5_000.0, "Interception"),
                          SYS + r"\mouse.sys": ("1.0.0.0", 5_000.0, "Interception")},
                   running=("keyboard", "mouse"), devices=True)
    options.update(changes)
    return FakeProbe(**options)


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    return lambda probe: input_driver.check(tmp_path, probe=probe, installers_dir=tmp_path / "cache")


def test_driver_state_is_read_from_windows_not_from_how_it_was_installed(windows):
    status = windows(installed())
    assert status.state == "ready" and status.ready and status.installed
    assert status.location == SYS + r"\keyboard.sys, " + SYS + r"\mouse.sys"
    assert (status.version, status.installed_at, status.keyboards, status.mice) == ("1.0.0.0", 5_000.0, 2, 1)
    assert not status.custom_location and not status.renamed and status.installer == ""
    # Answers, but no mouse goes through it: drawing cannot move the cursor.
    assert windows(installed(counts=(2, 0))).state == "no_mouse"
    assert not windows(installed(counts=(2, 0))).ready
    # Registered after the last boot: Windows loads filter drivers only at the next one.
    status = windows(installed(devices=False, running=(), boot=5_030.0))
    assert status.state == "reboot" and not status.ready
    # Restarted since, and Windows did not start it (or only part of it).
    assert windows(installed(devices=False, running=(), boot=9_000.0)).state == "blocked"
    assert windows(installed(devices=False, running=("keyboard",), boot=9_000.0)).state == "blocked"
    # Started, but the program cannot open it.
    assert windows(installed(devices=False, boot=9_000.0)).state == "unreachable"
    # Only one of the two parts is wired into its device class.
    half = {KB: ["keyboard", "kbdclass"], MS: ["mouclass"]}
    assert windows(installed(filters=half)).state == "incomplete"
    assert windows(installed(filters=half, devices=False)).state == "incomplete"
    # Removed: no longer registered, still answering until the restart.
    status = windows(installed(filters={KB: ["kbdclass"], MS: ["mouclass"]}))
    assert status.state == "pending_removal" and status.ready and not status.installed


def test_a_class_filter_without_its_driver_is_reported_as_damaged(windows):
    """Deleting the .sys by hand (or an antivirus doing it) leaves the device class asking
    for it: after the next boot the keyboard or mouse would not start."""
    no_service = installed(services={"keyboard": SYS + r"\keyboard.sys"})
    no_file = installed(files={SYS + r"\keyboard.sys": ("1.0.0.0", 5_000.0, "Interception")})
    for probe in (no_service, no_file, installed(devices=False, services={})):
        status = windows(probe)
        assert status.state == "broken" and status.installed and not status.ready


def test_missing_driver_and_harmless_leftovers(windows):
    status = windows(FakeProbe())
    assert status.state == "missing" and not status.leftovers and not status.installed
    # An old install without its filters: files and services do nothing.
    status = windows(FakeProbe(services={"keyboard": SYS + r"\keyboard.sys"},
                               files={SYS + r"\mouse.sys": ("1.0.0.0", 1.0, "Interception")}))
    assert status.state == "missing" and status.leftovers


def test_driver_installed_by_another_program_under_other_names_is_found(windows):
    """Another installer may register Interception under its own service names and keep
    the files in its own folder: the class filters and the files' product name find it."""
    tools = r"D:\Tools\Veyon\interception"
    probe = FakeProbe(filters={KB: ["kbdclass", "KbHook"], MS: ["MsHook", "mouclass"]},
                      services={"kbhook": tools + r"\kbhook.sys", "mshook": tools + r"\mshook.sys",
                                "keyboard": SYS + r"\keyboard.sys"},    # an old unregistered copy
                      files={tools + r"\kbhook.sys": ("1.0.0.0", 7_000.0, "Interception"),
                             tools + r"\mshook.sys": ("1.0.0.0", 7_000.0, "Interception")},
                      running=("kbhook", "mshook"), devices=True, present=("VeyonService",))
    status = windows(probe)
    assert status.state == "ready" and status.renamed and status.custom_location
    assert [part.name for part in status.parts] == ["kbhook", "mshook"]
    assert status.location == tools + r"\kbhook.sys, " + tools + r"\mshook.sys"
    assert status.users == ("Veyon",)
    # Someone else's filter under the usual names is not Interception.
    foreign = installed(devices=False, files={SYS + r"\keyboard.sys": ("2.0", 5_000.0, "Contoso Keyboard Filter"),
                                              SYS + r"\mouse.sys": ("2.0", 5_000.0, "Contoso Mouse Filter")})
    assert windows(foreign).state == "missing"


def test_anticheats_memory_integrity_and_processors_are_reported(windows):
    status = windows(installed(present=("EasyAntiCheat", "EasyAntiCheat_EOS", "FACEIT"), hvci=True))
    assert status.anticheats == (EAC, "FACEIT") and status.hvci
    # An ARM computer: no Interception driver exists for it — never offer to install.
    status = windows(FakeProbe(supported=False))
    assert status.state == "unsupported" and status.detail == "arch" and not status.ready and not status.installed


def test_driver_file_paths_are_resolved_like_windows_does(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("TOOLS", r"D:\Tools")
    resolve = input_driver.resolve_image_path
    assert resolve("", "mouse") == SYS + r"\mouse.sys"
    assert resolve(None, "keyboard") == SYS + r"\keyboard.sys"
    assert resolve(r"\SystemRoot\System32\drivers\mouse.sys", "mouse") == SYS + r"\mouse.sys"
    assert resolve(r"System32\drivers\mouse.sys", "mouse") == SYS + r"\mouse.sys"
    assert resolve(r"\??\D:\Tools\mouse.sys", "mouse") == r"D:\Tools\mouse.sys"
    assert resolve(r'"%TOOLS%\mouse.sys"', "mouse") == r"D:\Tools\mouse.sys"


@pytest.mark.skipif(os.name != "nt", reason="reads Windows itself")
def test_this_computer_is_read_without_errors(tmp_path):
    status = input_driver.check(tmp_path, installers_dir=tmp_path / "cache")
    assert status.state in ("ready", "no_mouse", "pending_removal", "reboot", "blocked", "unreachable",
                            "incomplete", "broken", "missing", "unsupported")
    assert input_driver.native_machine() != 0


# ----- the author's installer ---------------------------------------------------------------
@pytest.fixture
def official(monkeypatch):
    """A small stand-in for the author's release: the checks keep their real code."""
    installer = b"MZ" + b"official installer " * 200
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(input_driver.INSTALLER_IN_ZIP, installer)
        bundle.writestr("Interception/library/interception.h", "/* api */")
    archive = buffer.getvalue()
    monkeypatch.setattr(input_driver, "OFFICIAL_ZIP_SIZE", len(archive))
    monkeypatch.setattr(input_driver, "OFFICIAL_ZIP_SHA256", hashlib.sha256(archive).hexdigest())
    monkeypatch.setattr(input_driver, "INSTALLER_SIZE", len(installer))
    monkeypatch.setattr(input_driver, "INSTALLER_SHA256", hashlib.sha256(installer).hexdigest())
    return SimpleNamespace(installer=installer, archive=archive)


def serve(data):
    return lambda request, timeout: contextlib.nullcontext(io.BytesIO(data))


def test_installer_is_downloaded_checked_and_kept(official, tmp_path):
    progress = []
    path = input_driver.fetch_installer(tmp_path, progress=lambda done, total: progress.append((done, total)),
                                        opener=serve(official.archive))
    assert path == tmp_path / "install-interception.exe" and path.read_bytes() == official.installer
    assert not (tmp_path / "Interception.zip").exists()
    assert progress[-1] == (len(official.archive), len(official.archive))
    # Kept for the removal later, also without the internet.
    offline = lambda request, timeout: (_ for _ in ()).throw(AssertionError("no download expected"))
    assert input_driver.fetch_installer(tmp_path, opener=offline) == path
    assert input_driver.find_installer(tmp_path / "program", tmp_path) == str(path)


def test_a_changed_archive_or_installer_never_runs(official, tmp_path, monkeypatch):
    with pytest.raises(DriverError, match="не совпадает"):
        input_driver.fetch_installer(tmp_path, opener=serve(official.archive[:-1] + b"!"))
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(input_driver, "INSTALLER_SHA256", "0" * 64)
    with pytest.raises(DriverError, match="не совпадает"):
        input_driver.fetch_installer(tmp_path, opener=serve(official.archive))
    assert list(tmp_path.iterdir()) == []
    offline = lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("offline"))
    with pytest.raises(DriverError, match="Нет связи"):
        input_driver.fetch_installer(tmp_path, opener=offline)
    stopped = []
    with pytest.raises(DriverError, match="остановлена"):
        input_driver.fetch_installer(tmp_path, opener=serve(official.archive), cancelled=lambda: stopped.append(1) or True)
    assert list(tmp_path.iterdir()) == []


def test_installer_next_to_the_program_is_used_only_if_official(official, tmp_path):
    bundled = tmp_path / input_driver.INSTALLER_RELATIVE
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ something else")
    assert input_driver.find_installer(tmp_path, tmp_path / "cache") == ""
    bundled.write_bytes(official.installer)
    assert input_driver.find_installer(tmp_path, tmp_path / "cache") == str(bundled)


def test_only_the_checked_installer_runs_and_nobody_can_swap_it_meanwhile(official, tmp_path):
    path = tmp_path / "install-interception.exe"
    path.write_bytes(official.installer)
    seen = []

    def shell(file, arguments, timeout):
        if os.name == "nt":
            # Held with read sharing only: the file Windows starts is the file just checked.
            with pytest.raises(PermissionError):
                open(file, "r+b")
            with pytest.raises(PermissionError):
                os.replace(file, file + ".old")
        seen.append((file, arguments))
        return 0

    assert input_driver.run_installer(path, "install", shell=shell) == 0
    assert input_driver.run_installer(path, "uninstall", shell=shell) == 0
    assert seen == [(str(path), "/install"), (str(path), "/uninstall")]
    with pytest.raises(ValueError):
        input_driver.run_installer(path, "format", shell=shell)
    path.write_bytes(official.installer + b"!")
    with pytest.raises(DriverError, match="не совпадает"):
        input_driver.run_installer(path, shell=shell)
    path.unlink()
    with pytest.raises(DriverError, match="антивирус"):
        input_driver.run_installer(path, shell=shell)
    assert len(seen) == 2


# ----- installing and removing from the program ---------------------------------------------
PARTS = (DriverPart("keyboard", "keyboard", True, True, SYS + r"\keyboard.sys", True, True, "1.0.0.0", 5_000.0),
         DriverPart("mouse", "mouse", True, True, SYS + r"\mouse.sys", True, True, "1.0.0.0", 5_000.0))


class Windows:
    """Statuses in turn, the installer runs and the commands DriverCenter sends."""

    def __init__(self, *statuses, code=0):
        self.statuses = list(statuses)
        self.runs, self.commands, self.launched, self.opened, self.fetched = [], [], [], [], 0
        self.code = code

    def check(self):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def fetch(self, progress, cancelled):
        self.fetched += 1
        progress(200_000, 400_000)
        return r"C:\cache\install-interception.exe"

    def run(self, path, action):
        self.runs.append((path, action))
        return self.code

    def center(self, **kwargs):
        notes = []
        options = dict(check=self.check, fetch=self.fetch, run=self.run, start=lambda target, name: target(),
                       command=lambda args: self.commands.append(args) or SimpleNamespace(returncode=self.code),
                       launch=self.launched.append, opener=self.opened.append, notify=lambda: notes.append(1))
        options.update(kwargs)
        center = DriverCenter("C:/program", **options)
        center.notes = notes
        return center


def test_install_downloads_runs_and_asks_for_a_restart():
    windows = Windows(DriverStatus("missing"), DriverStatus("reboot", parts=PARTS))
    center = windows.center()
    assert center.change("install")
    assert windows.fetched == 1 and windows.runs == [(r"C:\cache\install-interception.exe", "install")]
    info = center.snapshot()
    assert info["state"] == "reboot" and info["busy"] == "" and not info["error"] and info["progress"] == 1.0
    assert "Перезагрузите компьютер" in info["message"] and info["has_files"] and info["installed_at"]
    assert len(center.notes) >= 4


def test_removal_repair_and_failures_are_told_plainly():
    installer = r"C:\program\drivers\interception\install-interception.exe"
    windows = Windows(DriverStatus("ready", installer, parts=PARTS), DriverStatus("pending_removal", installer))
    center = windows.center()
    assert center.change("uninstall") and windows.fetched == 0
    assert windows.runs == [(installer, "uninstall")]
    assert center.snapshot()["state"] == "pending_removal" and "до перезагрузки" in center.message
    # The installer ended, Windows still lists the driver.
    windows = Windows(DriverStatus("ready", installer, parts=PARTS), code=5)
    center = windows.center()
    assert center.change("uninstall") and center.error and "код 5" in center.message
    # A repair that leaves it damaged: no restart before it is fixed.
    windows = Windows(DriverStatus("broken", installer, parts=PARTS))
    center = windows.center()
    assert center.change("repair") and windows.runs == [(installer, "install")]
    assert center.error and "Не перезагружайте" in center.message
    # A refusal the user can read, and an unexpected one; the window never stays busy.
    def refused(path, action):
        raise DriverError("Windows не получила разрешения администратора — драйвер не изменён.")
    center = Windows(DriverStatus("missing", installer)).center(run=refused)
    assert center.change("install") and center.busy == "" and center.error and "разрешения" in center.message
    center = Windows(DriverStatus("missing", installer)).center(run=lambda path, action: 1 / 0)
    assert center.change("install") and center.busy == "" and "Не удалось изменить драйвер" in center.message


def test_memory_integrity_and_arm_computers_never_get_the_driver():
    center = Windows(DriverStatus("missing", hvci=True)).center()
    for action in ("install", "repair"):
        with pytest.raises(DriverError, match="Целостность памяти"):
            center.change(action)
    assert center.busy == ""
    windows = Windows(DriverStatus("blocked", "C:/i.exe", parts=PARTS, hvci=True), DriverStatus("missing"))
    assert windows.center().change("uninstall")       # removing it is always allowed
    with pytest.raises(DriverError):
        Windows(DriverStatus("unsupported", detail="arch")).center().change("install")
    with pytest.raises(ValueError):
        Windows(DriverStatus("missing")).center().change("format")


def test_one_change_at_a_time():
    jobs = []
    windows = Windows(DriverStatus("missing"))
    center = windows.center(start=lambda target, name: jobs.append(target))
    assert center.change("install") and center.busy == "install"
    assert not center.change("uninstall")
    assert center.recheck() == "missing" and windows.runs == []
    jobs.pop()()
    assert center.busy == "" and windows.runs


def test_restart_is_scheduled_cancelled_and_refusals_are_explained():
    windows = Windows(DriverStatus("reboot", parts=PARTS))
    center = windows.center()
    center.schedule_restart()
    assert windows.commands[-1][:4] == ["shutdown", "/r", "/t", "60"] and center.restart_scheduled
    assert center.snapshot()["restart_scheduled"] and "60 секунд" in center.message
    center.cancel_restart()
    assert windows.commands[-1] == ["shutdown", "/a"] and not center.restart_scheduled
    windows.code = 1190
    with pytest.raises(DriverError, match="уже запланирована"):
        center.schedule_restart()
    windows.code = 1116                               # nothing left to cancel
    center.cancel_restart()
    windows.code = 5
    with pytest.raises(DriverError, match="код 5"):
        center.schedule_restart()


def test_preview_shows_a_state_and_never_touches_windows(monkeypatch):
    """dev.ps1 fresh nodriver: the program as on a computer without the driver."""
    monkeypatch.setenv("OLEGPAINTER_DRIVER_PREVIEW", "missing")
    touched = []
    center = DriverCenter("C:/program", run=lambda path, action: touched.append(action),
                          command=lambda args: touched.append(args), fetch=lambda *a: touched.append("fetch"))
    info = center.snapshot()
    assert center.preview and info["state"] == "missing" and not info["ready"]
    assert center.recheck() == "missing"
    with pytest.raises(DriverError, match="просмотр"):
        center.change("install")
    with pytest.raises(DriverError, match="просмотр"):
        center.schedule_restart()
    assert touched == [] and center.busy == ""
    # An explicit check (tests, the real program) is never replaced; unknown names are ignored.
    assert not DriverCenter("C:/program", check=lambda: DriverStatus("ready")).preview
    monkeypatch.setenv("OLEGPAINTER_DRIVER_PREVIEW", "format-c")
    assert not DriverCenter("C:/program", check=lambda: DriverStatus("ready")).preview


def test_driver_files_and_windows_settings_open_where_windows_keeps_them():
    windows = Windows(DriverStatus("ready", parts=PARTS))
    center = windows.center()
    center.show_files()
    assert windows.launched == [["explorer", "/select,", SYS + r"\keyboard.sys"]]
    center.open_core_isolation()
    center.open_restore_points()
    assert windows.opened == ["windowsdefender://coreisolation", "SystemPropertiesProtection.exe"]
    with pytest.raises(DriverError, match="нет"):
        Windows(DriverStatus("missing")).center().show_files()

    def unavailable(target):
        raise OSError("no handler")
    with pytest.raises(DriverError, match="Изоляция ядра"):
        windows.center(opener=unavailable).open_core_isolation()
