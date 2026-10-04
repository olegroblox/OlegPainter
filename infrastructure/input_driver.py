"""Is the Interception driver ready, and how can the user install it? (DRIVER-001)

Qt-free. Without the driver every setup step still works (pickers use ordinary
hooks), and the user only learned about it from «Драйвер ввода недоступен» on
start. The shell checks this once at launch and after an install attempt.

Licensing: Interception is free for non-commercial use only; a paid OlegPainter
needs the author's commercial license, whose installer library can install the
driver silently. Until that is settled the program bundles nothing: an installer
placed at `drivers/interception/install-interception.exe` is used if present,
otherwise the user downloads it from the author's page.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

OFFICIAL_PAGE = "https://github.com/oblitum/Interception/releases"
INSTALLER_RELATIVE = Path("drivers") / "interception" / "install-interception.exe"


@dataclass(frozen=True)
class DriverStatus:
    state: str               # "ready" | "reboot" | "missing" | "unsupported"
    installer: str = ""      # a bundled installer that can be run, or ""

    @property
    def ready(self) -> bool:
        return self.state in ("ready", "unsupported")


def _devices_open() -> bool:
    try:
        import interception.inputs as inputs
    except Exception:
        return False
    try:
        return bool(inputs._g_context.valid)
    except Exception:
        return False


def _service_registered() -> bool:
    """The installer registers the `keyboard`/`mouse` filter services; the devices
    appear only after a reboot."""
    if os.name != "nt":
        return False
    try:
        import winreg
        for name in ("keyboard", "mouse"):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{name}"):
                pass
        return True
    except OSError:
        return False


def find_installer(app_root: Path) -> str:
    candidate = Path(app_root) / INSTALLER_RELATIVE
    return str(candidate) if candidate.is_file() else ""


def check(app_root: Path, *, devices_open=_devices_open, service_registered=_service_registered) -> DriverStatus:
    if os.name != "nt":
        return DriverStatus("unsupported")
    installer = find_installer(app_root)
    if devices_open():
        return DriverStatus("ready", installer)
    return DriverStatus("reboot" if service_registered() else "missing", installer)


def run_installer(path: str, action: str = "install") -> bool:
    """Start the author's installer elevated (UAC) with /install or /uninstall. True
    when Windows accepted the request; the user still confirms the prompt and reboots."""
    if action not in ("install", "uninstall"):
        raise ValueError("Неизвестное действие установщика драйвера.")
    if os.name != "nt" or not path:
        return False
    import ctypes
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), "/" + action,
                                                 str(Path(path).parent), 1)
    return int(result) > 32
