"""The Interception driver: is it there, where, does it work, and install or remove it
(DRIVER-001, DRIVER-002).

Qt-free. Interception may have been installed by anyone — by us, by hand, by another
program (Veyon ships it) — under its usual service names `keyboard` and `mouse` or
under others, with its files anywhere. So the state is read from Windows itself: the
UpperFilters of the keyboard and mouse device classes, the services they name, the
files those services start (recognised by their product name «Interception») and
whether the driver answers:

  ready            the driver answers and a mouse goes through it — drawing works
  no_mouse         it answers but no mouse goes through it (devices reconnected)
  pending_removal  it answers but is no longer registered: removed, gone after a reboot
  reboot           registered, not loaded yet: restart the computer
  blocked          registered, the computer restarted, Windows did not start it
                   (Memory Integrity, an antivirus, no keyboard or mouse plugged in)
  unreachable      Windows started it but the program cannot open it
  incomplete       only the keyboard or only the mouse part is registered
  broken           a device class still asks for a driver whose service or file is
                   missing: after a restart the keyboard or mouse can stop working
  missing          not installed (old files without registration are harmless)
  unsupported      not Windows, or a processor Interception has no driver for (ARM)

The author's installer is downloaded from his GitHub release on the user's request and
runs only when its SHA-256 matches the official file; it is held open meanwhile so it
cannot be swapped before Windows starts it with administrator rights. Interception is
free for non-commercial use; the program does not ship it (our Python port talks to
the driver without his library).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import platform
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

OFFICIAL_PAGE = "https://github.com/oblitum/Interception/releases"
OFFICIAL_ZIP_URL = "https://github.com/oblitum/Interception/releases/download/v1.0.1/Interception.zip"
OFFICIAL_ZIP_SIZE = 389_119
OFFICIAL_ZIP_SHA256 = "ad038963d6413055765128b0b931f6e765147c9916dba79e65d872b261f9af10"
INSTALLER_IN_ZIP = "Interception/command line installer/install-interception.exe"
INSTALLER_SIZE = 470_528
INSTALLER_SHA256 = "e137863a79da797f08e7a137280ff2a123809044a888fd75ce9c973198915abe"
# An installer placed next to the program is used too, if it is the official one.
INSTALLER_RELATIVE = Path("drivers") / "interception" / "install-interception.exe"

KEYBOARD_CLASS = "{4D36E96B-E325-11CE-BFC1-08002BE10318}"
MOUSE_CLASS = "{4D36E96F-E325-11CE-BFC1-08002BE10318}"
PARTS = (("keyboard", KEYBOARD_CLASS), ("mouse", MOUSE_CLASS))
PRODUCT = "Interception"                       # ProductName in the version resource of its files
CLASS_DRIVERS = {"kbdclass", "mouclass"}       # Windows' own filters in the same lists
# Kernel anti-cheats that refuse to start their games while Interception is installed.
ANTICHEATS = {
    "vgk": "Riot Vanguard (Valorant, League of Legends)",
    "EasyAntiCheat": "Easy Anti-Cheat (Fortnite, Apex Legends, Rust…)",
    "EasyAntiCheat_EOS": "Easy Anti-Cheat (Fortnite, Apex Legends, Rust…)",
    "FACEIT": "FACEIT",
    "EAAntiCheatService": "EA Javelin (Battlefield, EA Sports FC)",
}
# Programs that install Interception themselves and stop working without it.
USERS = {"VeyonService": "Veyon"}
_NATIVE_SUPPORTED = (0x8664, 0x014C)           # IMAGE_FILE_MACHINE_AMD64, _I386: no ARM driver exists


class DriverError(RuntimeError):
    """A problem the user can read."""


@dataclass(frozen=True)
class DriverPart:
    kind: str                 # "keyboard" or "mouse"
    name: str                 # its service: "keyboard"/"mouse" unless another installer chose others
    filter: bool = False      # listed in the UpperFilters of its device class
    service: bool = False     # the service is registered
    path: str = ""            # the driver file the service starts
    file: bool = False        # that file exists
    running: bool = False
    version: str = ""
    modified: float = 0.0     # when the file was written: the install time


@dataclass(frozen=True)
class DriverStatus:
    state: str
    installer: str = ""                       # a verified installer at hand ("" = download it)
    parts: tuple = ()
    hvci: bool = False                        # Memory Integrity (core isolation) is on
    anticheats: tuple = ()
    users: tuple = ()                         # programs that brought Interception themselves
    leftovers: bool = False                   # files or services of an old install, not wired in
    keyboards: int = -1                       # devices going through the driver; -1 = unknown
    mice: int = -1
    detail: str = ""

    @property
    def ready(self) -> bool:
        """Drawing works now (a removed driver still answers until the restart)."""
        return self.state in ("ready", "pending_removal") or (self.state == "unsupported" and self.detail != "arch")

    @property
    def installed(self) -> bool:
        return self.state in ("ready", "no_mouse", "reboot", "blocked", "unreachable", "incomplete", "broken")

    @property
    def location(self) -> str:
        paths = [part.path for part in self.parts if part.path]
        return ", ".join(dict.fromkeys(paths))

    @property
    def custom_location(self) -> bool:
        """Its files are not where the author's installer puts them: another program installed it."""
        default = os.path.normcase(str(Path(windows_dir()) / "System32" / "drivers"))
        return any(os.path.normcase(str(Path(part.path).parent)) != default for part in self.parts if part.path)

    @property
    def renamed(self) -> bool:
        return any(part.filter and part.name != part.kind for part in self.parts)

    @property
    def version(self) -> str:
        return next((part.version for part in self.parts if part.version), "")

    @property
    def installed_at(self) -> float:
        return max((part.modified for part in self.parts), default=0.0)


# ----- reading Windows -----------------------------------------------------------------
class SystemProbe:
    """What Windows says, without administrator rights. Tests pass a fake."""

    def devices_open(self) -> bool:
        """Both parts answer: the keyboard driver opens interception00–09, the mouse one 10–19.
        The library counts itself ready with any one of them."""
        try:
            import interception.inputs as inputs
            return len(inputs._g_context.devices) >= 20
        except Exception:
            return False

    def device_counts(self) -> tuple[int, int]:
        """(keyboards, mice) going through the driver: slots that report a hardware id."""
        try:
            import interception.inputs as inputs
            devices = inputs._g_context.devices
            if len(devices) < 20:
                return -1, -1
            return (sum(1 for device in devices[:10] if device.get_HWID()),
                    sum(1 for device in devices[10:20] if device.get_HWID()))
        except Exception:
            return -1, -1

    def _value(self, path, name):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    def class_filters(self, class_guid: str) -> list[str]:
        value = self._value(rf"SYSTEM\CurrentControlSet\Control\Class\{class_guid}", "UpperFilters")
        return [str(item) for item in value] if isinstance(value, (list, tuple)) else []

    def service(self, name: str):
        """None when not registered, else the driver file it starts."""
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{name}"):
                pass
        except OSError:
            return None
        return resolve_image_path(self._value(rf"SYSTEM\CurrentControlSet\Services\{name}", "ImagePath"), name)

    def service_exists(self, name: str) -> bool:
        return self.service(name) is not None

    def service_running(self, name: str) -> bool:
        import ctypes
        from ctypes import wintypes

        class ServiceStatus(ctypes.Structure):
            _fields_ = [(field_name, wintypes.DWORD) for field_name in (
                "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
                "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint")]

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.OpenSCManagerW.restype = wintypes.HANDLE
        advapi.OpenServiceW.restype = wintypes.HANDLE
        manager = advapi.OpenSCManagerW(None, None, 0x0001)          # SC_MANAGER_CONNECT
        if not manager:
            return False
        try:
            handle = advapi.OpenServiceW(wintypes.HANDLE(manager), name, 0x0004)   # SERVICE_QUERY_STATUS
            if not handle:
                return False
            try:
                status = ServiceStatus()
                if not advapi.QueryServiceStatus(wintypes.HANDLE(handle), ctypes.byref(status)):
                    return False
                return status.dwCurrentState == 4                     # SERVICE_RUNNING
            finally:
                advapi.CloseServiceHandle(wintypes.HANDLE(handle))
        finally:
            advapi.CloseServiceHandle(wintypes.HANDLE(manager))

    def file_info(self, path: str):
        """(exists, version, modified, product)."""
        try:
            stat = os.stat(path)
        except OSError:
            return False, "", 0.0, ""
        version, product = file_version(path)
        return True, version, stat.st_mtime, product

    def hvci(self) -> bool:
        """Memory Integrity is on — from the Windows Security switch or a group policy."""
        if self._value(r"SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity",
                       "Enabled") == 1:
            return True
        policy = r"SOFTWARE\Policies\Microsoft\Windows\DeviceGuard"
        return (self._value(policy, "EnableVirtualizationBasedSecurity") == 1
                and self._value(policy, "HypervisorEnforcedCodeIntegrity") in (1, 2))

    def boot_time(self) -> float:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return time.time() - kernel32.GetTickCount64() / 1000.0

    def supported(self) -> bool:
        return os.name == "nt" and native_machine() in _NATIVE_SUPPORTED


def windows_dir() -> str:
    return os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"


def native_machine() -> int:
    """IMAGE_FILE_MACHINE_* of Windows itself. An x64 program emulated on ARM hears
    «AMD64» from everything else — and an x64 driver there would leave the computer
    without keyboard and mouse."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.IsWow64Process2.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT),
                                             ctypes.POINTER(wintypes.USHORT))
        process, native = wintypes.USHORT(), wintypes.USHORT()
        if kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return int(native.value)
    except (AttributeError, OSError):
        pass
    return {"AMD64": 0x8664, "X86": 0x014C, "ARM64": 0xAA64}.get(platform.machine().upper(), 0)


def resolve_image_path(image_path, name: str) -> str:
    """The file a driver service starts. Empty means Windows' default location."""
    windows = windows_dir()
    text = str(image_path or "").strip().strip('"')
    if not text:
        return str(Path(windows) / "System32" / "drivers" / f"{name}.sys")
    lowered = text.lower()
    if lowered.startswith("\\??\\"):
        text = text[4:]
    elif lowered.startswith("\\systemroot\\"):
        text = windows + text[len("\\systemroot"):]
    elif lowered.startswith("system32\\"):
        text = str(Path(windows) / text)
    return os.path.expandvars(text)


def file_version(path: str) -> tuple[str, str]:
    """(«1.0.0.0», product name) from the file's version resource; empty when unknown."""
    if os.name != "nt":
        return "", ""
    try:
        import ctypes
        api = ctypes.WinDLL("version")
        size = api.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return "", ""
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
            return "", ""
        pointer, length = ctypes.c_void_p(), ctypes.c_uint()
        version = ""
        if api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) and length.value:
            words = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
            ms, ls = words[2], words[3]                               # dwFileVersionMS / LS
            version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        tables = ["040904b0", "040904e4"]
        if api.VerQueryValueW(buffer, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)) \
                and length.value >= 4:
            language, codepage = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint16 * 2)).contents
            tables.insert(0, f"{language:04x}{codepage:04x}")
        for table in tables:
            if api.VerQueryValueW(buffer, f"\\StringFileInfo\\{table}\\ProductName", ctypes.byref(pointer),
                                  ctypes.byref(length)) and length.value:
                return version, ctypes.wstring_at(pointer, length.value).rstrip("\0").strip()
        return version, ""
    except Exception:
        return "", ""


def _find_part(probe, kind: str, class_guid: str) -> DriverPart:
    """The keyboard or mouse part wherever and under whatever name it is installed."""
    filters = list(dict.fromkeys(name.lower() for name in probe.class_filters(class_guid)))
    wired = [name for name in filters if name not in CLASS_DRIVERS]
    # Wired names first: an old unregistered «keyboard» must not hide a working copy
    # that another program installed under its own name.
    for name in wired + ([] if kind in wired else [kind]):
        path = probe.service(name)
        exists, version, modified, product = probe.file_info(path) if path else (False, "", 0.0, "")
        # Under its usual name it is Interception unless the file says otherwise;
        # under another name only when the file says so.
        if product != PRODUCT and (name != kind or product):
            continue
        return DriverPart(kind=kind, name=name, filter=name in filters, service=path is not None,
                          path=path or "", file=exists, running=bool(path) and probe.service_running(name),
                          version=version, modified=modified)
    return DriverPart(kind=kind, name=kind)


def _present(probe, table: dict) -> tuple:
    return tuple(dict.fromkeys(label for service, label in table.items() if probe.service_exists(service)))


def check(app_root: Path, *, probe: SystemProbe | None = None, installers_dir: Path | None = None) -> DriverStatus:
    probe = probe or SystemProbe()
    installer = find_installer(app_root, installers_dir)
    if os.name != "nt":
        return DriverStatus("unsupported", installer)
    if not probe.supported():
        return DriverStatus("unsupported", installer, detail="arch")
    parts = tuple(_find_part(probe, kind, class_guid) for kind, class_guid in PARTS)
    common = dict(installer=installer, parts=parts, hvci=probe.hvci(),
                  anticheats=_present(probe, ANTICHEATS), users=_present(probe, USERS))
    wired = [part for part in parts if part.filter]
    if any(not (part.service and part.file) for part in wired):
        # A class filter without its driver: Windows cannot start the keyboard or
        # mouse at the next boot — the failure people know from manual deletions.
        return DriverStatus("broken", **common)
    if probe.devices_open():
        if not wired:
            return DriverStatus("pending_removal", **common)
        if len(wired) < len(parts):
            return DriverStatus("incomplete", **common)
        keyboards, mice = probe.device_counts()
        return DriverStatus("no_mouse" if mice == 0 else "ready", keyboards=keyboards, mice=mice, **common)
    if wired:
        if len(wired) < len(parts):
            return DriverStatus("incomplete", **common)
        # The installer writes the files: a boot later than that means Windows had its chance.
        if probe.boot_time() <= max(part.modified for part in wired) + 60:
            return DriverStatus("reboot", **common)
        if not all(part.running for part in wired):
            return DriverStatus("blocked", **common)
        return DriverStatus("unreachable", **common)
    return DriverStatus("missing", leftovers=any(part.service or part.file for part in parts), **common)


# ----- the author's installer ---------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_official_installer(path) -> bool:
    try:
        path = Path(path)
        return path.is_file() and path.stat().st_size == INSTALLER_SIZE and _sha256(path) == INSTALLER_SHA256
    except OSError:
        return False


def installers_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "OlegPainter" / "drivers" / "interception"


def find_installer(app_root: Path, folder: Path | None = None) -> str:
    """The official installer next to the program or downloaded earlier, or ""."""
    for candidate in (Path(app_root) / INSTALLER_RELATIVE, Path(folder or installers_dir()) / "install-interception.exe"):
        if is_official_installer(candidate):
            return str(candidate)
    return ""


def fetch_installer(folder: Path | None = None, *, progress=lambda done, total: None,
                    cancelled=lambda: False, opener=None) -> Path:
    """Download the author's release archive, check it and take the installer out of it."""
    import urllib.error
    import urllib.request
    folder = Path(folder or installers_dir())
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "install-interception.exe"
    if is_official_installer(target):
        return target
    archive = folder / "Interception.zip"
    try:
        request = urllib.request.Request(OFFICIAL_ZIP_URL, headers={"User-Agent": "OlegPainter"})
        with (opener or urllib.request.urlopen)(request, timeout=60) as response, open(archive, "wb") as out:
            done = 0
            while True:
                if cancelled():
                    raise DriverError("Загрузка остановлена.")
                block = response.read(1 << 16)
                if not block:
                    break
                out.write(block)
                done += len(block)
                progress(done, OFFICIAL_ZIP_SIZE)
    except DriverError:
        archive.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        archive.unlink(missing_ok=True)
        raise DriverError("Нет связи с GitHub: архив драйвера не скачался. Проверьте интернет.") from error
    except OSError as error:
        archive.unlink(missing_ok=True)
        raise DriverError(f"Не удалось сохранить архив драйвера: {error}") from error
    if archive.stat().st_size != OFFICIAL_ZIP_SIZE or _sha256(archive) != OFFICIAL_ZIP_SHA256:
        archive.unlink(missing_ok=True)
        raise DriverError("Скачанный архив не совпадает с официальным файлом автора — запускать его нельзя.")
    try:
        with zipfile.ZipFile(archive) as bundle, open(target, "wb") as out:
            out.write(bundle.read(INSTALLER_IN_ZIP))
    except (KeyError, zipfile.BadZipFile, OSError) as error:
        target.unlink(missing_ok=True)
        raise DriverError("В архиве автора нет установщика.") from error
    finally:
        archive.unlink(missing_ok=True)
    if not target.exists():
        raise DriverError(_VANISHED)
    if not is_official_installer(target):
        target.unlink(missing_ok=True)
        raise DriverError("Установщик не совпадает с официальным файлом автора — запускать его нельзя.")
    return target


_VANISHED = ("Установщик драйвера пропал сразу после распаковки — похоже, его удалил антивирус. "
             "Установите драйвер вручную со страницы автора или разрешите файл в антивирусе.")
_SHELL_ERRORS = {
    1223: "Windows не получила разрешения администратора — драйвер не изменён.",      # «Нет» in the UAC prompt
    2: _VANISHED, 3: _VANISHED,
    225: "Антивирус заблокировал установщик Interception — драйвер не изменён.",     # ERROR_VIRUS_INFECTED
    226: "Антивирус заблокировал установщик Interception — драйвер не изменён.",     # ERROR_VIRUS_DELETED
    1260: "Политика Windows не разрешает запускать установщик драйвера — драйвер не изменён.",
    4551: "Политика Windows (например, Smart App Control) не разрешает запускать установщик драйвера — "
          "драйвер не изменён.",
}


def _open_held(path):
    """Open for reading while nobody may write, rename or delete the file."""
    if os.name != "nt":
        return open(path, "rb")
    import _winapi
    import msvcrt
    handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 0x1,          # FILE_SHARE_READ only
                                _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL)
    try:
        return open(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")
    except Exception:
        _winapi.CloseHandle(handle)
        raise


@contextlib.contextmanager
def _held_official(path):
    """The installer, checked and held: what Windows starts is what was checked."""
    try:
        stream = _open_held(path)
    except FileNotFoundError as error:
        raise DriverError(_VANISHED) from error
    except OSError as error:
        raise DriverError(f"Не удалось открыть установщик драйвера: {error}") from error
    with stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
        if os.fstat(stream.fileno()).st_size != INSTALLER_SIZE or digest.hexdigest() != INSTALLER_SHA256:
            raise DriverError("Установщик не совпадает с официальным файлом автора — запускать его нельзя.")
        yield


def run_installer(path, action: str = "install", *, timeout: float = 180, shell=None) -> int:
    """Run the author's installer with /install or /uninstall as administrator and wait
    for it. Returns its exit code; the result is read from Windows afterwards."""
    if action not in ("install", "uninstall"):
        raise ValueError("Неизвестное действие установщика драйвера.")
    with _held_official(path):
        return (shell or _shell_execute_elevated)(str(path), "/" + action, timeout)


def _shell_execute_elevated(path: str, arguments: str, timeout: float) -> int:
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000400            # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb, info.lpFile, info.lpParameters = "runas", path, arguments
    info.lpDirectory = str(Path(path).parent)
    info.nShow = 0                                   # the console installer runs hidden
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(ShellExecuteInfo),)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        raise DriverError(_SHELL_ERRORS.get(code, f"Не удалось запустить установщик драйвера (код {code})."))
    if not info.hProcess:
        return -1                                    # started without a handle: Windows tells the result
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) != 0:
            raise DriverError("Установщик драйвера не закончил работу вовремя.")
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)
