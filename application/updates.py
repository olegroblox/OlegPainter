"""Program updates without an installer or a signature (UPDATE-001).

Once a day (and on «Проверить обновления») the program asks GitHub for the latest
release. «Обновить» downloads its archive, checks the SHA-256 GitHub publishes for
every asset, unpacks it into the user's folder and starts the new version in
installer mode: it waits for this copy to close, swaps the program files in the
same folder — settings, models and journals stay where they are — and starts
itself there. A failed swap puts the previous files back.

Qt-free: the presenter passes a `notify` callback and marshals it to the GUI.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

REPOSITORY = "olegroblox/OlegPainter"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
EXE = "OlegPainter.exe"
# Names next to the exe that belong to the user, never to a release.
USER_DATA = frozenset({"configs", "models", "logs", "telemetry", ".update-backup"})
INSTALL_FLAG = "--install-update"
FINISHED_FLAG = "--update-finished"
CHECK_INTERVAL = 20 * 3600          # «once a day», a little early so the same hour still checks
_CHUNK = 1 << 20

# Set at start-up when this copy was just installed by an update ({"version": …}).
finished_update: Optional[dict] = None


class UpdateError(RuntimeError):
    """A problem the user can read."""


class UpdateCancelled(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    notes: str
    page_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    sha256: str


# ----- versions and releases ------------------------------------------------------------
def parse_version(text) -> tuple[int, ...]:
    """(1, 4, 1) from «v1.4.1», «1.4.1» or «OlegPainter 1.4.1»; () when there is none."""
    match = re.search(r"\d+(?:\.\d+)*", str(text or ""))
    return tuple(int(part) for part in match.group(0).split(".")[:4]) if match else ()


def is_newer(candidate, current) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if not new or not old:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def _pick_asset(assets) -> Optional[dict]:
    """The Windows archive: «OlegPainter-1.5-win64.zip», else any zip for Windows."""
    zips = [a for a in assets if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".zip")]
    for pattern in (r"^olegpainter-[\d.]+-win64\.zip$", r"win"):
        found = [a for a in zips if re.search(pattern, str(a.get("name", "")).lower())]
        if found:
            return found[0]
    return None


def parse_release(payload) -> Optional[Release]:
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name") or "")
    version = ".".join(str(part) for part in parse_version(tag or payload.get("name")))
    asset = _pick_asset(payload.get("assets") or [])
    if not version or asset is None or not asset.get("browser_download_url"):
        return None
    digest = str(asset.get("digest") or "")
    return Release(version=version, tag=tag, notes=str(payload.get("body") or "").strip(),
                   page_url=str(payload.get("html_url") or RELEASES_PAGE),
                   asset_name=str(asset["name"]), asset_url=str(asset["browser_download_url"]),
                   asset_size=int(asset.get("size") or 0),
                   sha256=digest[len("sha256:"):].lower() if digest.startswith("sha256:") else "")


def _request(url, current_version):
    return urllib.request.Request(url, headers={"User-Agent": f"OlegPainter/{current_version}",
                                                "Accept": "application/vnd.github+json"})


def latest_release(current_version, *, url=None, opener=None, timeout=15) -> Optional[Release]:
    """The latest published release, or None when nothing is published yet. Only the
    version number is asked for: GitHub sees the request, nothing about the user."""
    url = url or os.environ.get("OLEGPAINTER_UPDATE_URL") or API_URL
    try:
        with (opener or urllib.request.urlopen)(_request(url, current_version), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code in (403, 429):
            raise UpdateError("GitHub временно ограничил проверки. Попробуйте через час.") from error
        raise UpdateError(f"Сервер обновлений ответил ошибкой {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise UpdateError("Нет связи с GitHub: проверьте интернет и попробуйте ещё раз.") from error
    except ValueError as error:
        raise UpdateError("Сервер обновлений прислал непонятный ответ.") from error
    return parse_release(payload)


# ----- download and unpacking -------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(release: Release, folder: Path, current_version, *, progress=lambda done, total: None,
             cancelled=lambda: False, opener=None) -> Path:
    """The release archive in `folder`; an interrupted download continues where it stopped."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / release.asset_name
    if target.is_file() and (not release.asset_size or target.stat().st_size == release.asset_size) \
            and (not release.sha256 or _sha256(target) == release.sha256):
        return target
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if release.asset_size and have > release.asset_size:
        part.unlink()
        have = 0
    if not release.asset_size or have < release.asset_size:
        request = _request(release.asset_url, current_version)
        request.add_header("Accept", "application/octet-stream")
        if have:
            request.add_header("Range", f"bytes={have}-")
        try:
            with (opener or urllib.request.urlopen)(request, timeout=60) as response:
                if have and getattr(response, "status", 200) != 206:
                    have = 0                      # the server ignored Range: start over
                total = release.asset_size or (int(response.headers.get("Content-Length") or 0) + have)
                with open(part, "ab" if have else "wb") as out:
                    done = have
                    while True:
                        if cancelled():
                            raise UpdateCancelled()
                        block = response.read(_CHUNK)
                        if not block:
                            break
                        out.write(block)
                        done += len(block)
                        progress(done, total)
        except UpdateCancelled:
            raise
        except urllib.error.HTTPError as error:
            raise UpdateError(f"Сервер ответил ошибкой {error.code}. Попробуйте позже — скачанная часть сохранится.") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise UpdateError("Загрузка прервалась: нет связи. Нажмите «Обновить» ещё раз — она продолжится.") from error
        except OSError as error:
            raise UpdateError(_disk_error(error)) from error
    if release.asset_size and part.stat().st_size != release.asset_size:
        raise UpdateError("Архив скачался не полностью. Нажмите «Обновить» ещё раз — загрузка продолжится.")
    if release.sha256 and _sha256(part) != release.sha256:
        part.unlink()
        raise UpdateError("Архив пришёл повреждённым: контрольная сумма не совпала. Скачайте его ещё раз.")
    os.replace(part, target)
    return target


def _disk_error(error: OSError) -> str:
    if getattr(error, "errno", None) == 28 or getattr(error, "winerror", None) == 112:
        return "Не хватает места на диске для обновления."
    return f"Не удалось сохранить обновление: {error.strerror or error}."


def program_root(folder: Path) -> Optional[Path]:
    """Where OlegPainter.exe is: the folder itself or its one subfolder (the archive
    keeps the program in «OlegPainter/»)."""
    if (folder / EXE).is_file():
        return folder
    found = [child for child in folder.iterdir() if child.is_dir() and (child / EXE).is_file()] if folder.is_dir() else []
    return found[0] if len(found) == 1 else None


def extract(archive: Path, folder: Path, *, progress=lambda done, total: None, cancelled=lambda: False) -> Path:
    """Unpack `archive` into `folder`/unpacked and return the folder with the new exe."""
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / "unpacked"
    if destination.exists():
        shutil.rmtree(destination)
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            need = sum(member.file_size for member in members) + 100 * 2**20
            free = shutil.disk_usage(folder).free
            if free < need:
                raise UpdateError(f"Не хватает места на диске: для распаковки нужно ещё {round((need - free) / 2**20)} МБ.")
            root = destination.resolve()
            for index, member in enumerate(members, 1):
                if cancelled():
                    raise UpdateCancelled()
                place = (destination / member.filename).resolve()
                if place != root and root not in place.parents:
                    raise UpdateError("В архиве обновления недопустимые пути — он не будет распакован.")
                bundle.extract(member, destination)
                progress(index, len(members))
    except zipfile.BadZipFile as error:
        archive.unlink(missing_ok=True)
        raise UpdateError("Архив обновления повреждён. Скачайте его ещё раз.") from error
    except OSError as error:
        raise UpdateError(_disk_error(error)) from error
    found = program_root(destination)
    if found is None:
        raise UpdateError("В архиве обновления нет OlegPainter.exe.")
    return found


# ----- replacing the program files (installer mode of the new version) --------------------------------
def _retry(action, *, attempts=40, delay=0.25, sleep=time.sleep):
    """Antivirus and the closing copy may hold a file for a moment."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            sleep(delay)


def install_into(source: Path, target: Path, *, sleep=time.sleep) -> None:
    """Replace the program files of `target` with those of `source`. User data is left
    alone; on any failure the previous files are put back and UpdateError is raised."""
    source, target = Path(source), Path(target)
    if not (source / EXE).is_file():
        raise UpdateError("Новая версия не найдена — скачайте обновление ещё раз.")
    if not (target / EXE).is_file() or not (target / "_internal").is_dir():
        raise UpdateError(f"Папка «{target}» не похожа на OlegPainter — обновлять её нельзя.")
    names = sorted(child.name for child in source.iterdir() if child.name not in USER_DATA)
    backup = target / ".update-backup"
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir()
    moved: list[str] = []         # set aside in the backup
    copied: list[str] = []        # new copies started in the target
    try:
        for name in names:
            if (target / name).exists():
                _retry(lambda name=name: os.replace(target / name, backup / name), sleep=sleep)
                moved.append(name)
        for name in names:
            copied.append(name)
            if (source / name).is_dir():
                shutil.copytree(source / name, target / name)
            else:
                shutil.copy2(source / name, target / name)
    except Exception as error:
        log.exception("Update swap failed; restoring the previous files")
        for name in copied:
            path = target / name
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink(missing_ok=True)
        for name in moved:
            try:
                os.replace(backup / name, target / name)
            except OSError:
                log.exception("Could not restore %s", name)
        try:
            backup.rmdir()                # kept when something could not be put back
        except OSError:
            pass
        if isinstance(error, PermissionError):
            raise UpdateError("Файлы программы заняты или папка защищена от записи. Прежняя версия на месте.") from error
        raise UpdateError(f"Не удалось заменить файлы программы: {error}. Прежняя версия на месте.") from error
    shutil.rmtree(backup, ignore_errors=True)


def wait_for_exit(pid: int, timeout: float) -> bool:
    """True once process `pid` has ended (or never existed)."""
    if pid <= 0 or pid == os.getpid():
        return True
    if os.name != "nt":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.2)
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, int(pid))     # SYNCHRONIZE
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


def installer_args(new_root: Path, target: Path, version: str) -> list[str]:
    return [str(new_root / EXE), INSTALL_FLAG, str(target), "--wait-pid", str(os.getpid()), "--version", version]


def parse_flag_args(argv, flag: str) -> Optional[dict]:
    """{"path": …, "wait_pid": int, "version": str} after `flag` in argv, or None."""
    if flag not in argv:
        return None
    position = argv.index(flag)
    if position + 1 >= len(argv):
        return None
    result = dict(path=argv[position + 1], wait_pid=0, version="")
    for name, key, cast in (("--wait-pid", "wait_pid", int), ("--version", "version", str)):
        if name in argv and argv.index(name) + 1 < len(argv):
            try:
                result[key] = cast(argv[argv.index(name) + 1])
            except ValueError:
                pass
    return result


def strip_flag_args(argv, flag: str) -> list[str]:
    """argv without `flag`, its path and the --wait-pid/--version values."""
    result, skip = [], 0
    for item in argv:
        if skip:
            skip -= 1
            continue
        if item in (flag, "--wait-pid", "--version"):
            skip = 1
            continue
        result.append(item)
    return result


def updates_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "OlegPainter" / "updates"


def finish_on_start(argv, app_root: Path) -> list[str]:
    """At a normal start: note an update that just installed this copy, clean up its
    download in the background and return argv without the update arguments."""
    global finished_update
    options = parse_flag_args(list(argv), FINISHED_FLAG)
    leftover = Path(app_root) / ".update-backup"
    if leftover.is_dir():                     # the previous files of a swap that already succeeded
        threading.Thread(target=shutil.rmtree, args=(leftover,), kwargs=dict(ignore_errors=True),
                         name="update-backup-cleanup", daemon=True).start()
    if options is None:
        return list(argv)
    finished_update = dict(version=options["version"])
    threading.Thread(target=cleanup, args=(Path(options["path"]), options["wait_pid"]),
                     name="update-cleanup", daemon=True).start()
    return strip_flag_args(list(argv), FINISHED_FLAG)


def cleanup(folder: Path, wait_pid: int = 0) -> None:
    """Remove the downloaded archive and unpacked copy once the installer has exited.
    Only a folder named «updates» under the user's OlegPainter folder is ever removed."""
    folder = Path(folder)
    if folder.resolve() != updates_dir().resolve():
        log.warning("Not removing an unexpected update folder: %s", folder)
        return
    wait_for_exit(wait_pid, 30)
    for _attempt in range(10):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.5)


# ----- the state the window shows ---------------------------------------------------------------------
class UpdateCenter:
    """Check → download → unpack → hand over to the new version, one step at a time."""

    def __init__(self, current_version: str, app_root: Path, *, frozen: bool, notify: Callable[[], None] = lambda: None,
                 work_dir: Path | None = None, fetch=None, fetch_archive=None, unpack=None, launch=None,
                 writable: Callable[[Path], bool] | None = None):
        self.current = str(current_version)
        self.app_root = Path(app_root)
        self.frozen = bool(frozen)
        self.work_dir = Path(work_dir) if work_dir else updates_dir()
        self._notify = notify
        self._fetch = fetch or (lambda: latest_release(self.current))
        self._download = fetch_archive or (lambda release, progress, cancelled: download(
            release, self.work_dir, self.current, progress=progress, cancelled=cancelled))
        self._unpack = unpack or (lambda archive, progress, cancelled: extract(
            archive, self.work_dir, progress=progress, cancelled=cancelled))
        self._launch = launch or (lambda args: subprocess.Popen(
            args, cwd=str(Path(args[0]).parent), close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        self._writable = writable or _writable
        self._can_install: Optional[bool] = None
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self.state = "idle"        # idle checking latest available downloading unpacking ready installing
        self.release: Optional[Release] = None
        self.message = ""
        self.error = False
        self.progress = 0.0
        self.new_root: Optional[Path] = None

    # -- view
    @property
    def busy(self) -> bool:
        return self.state in ("checking", "downloading", "unpacking", "installing")

    @property
    def can_install(self) -> bool:
        """Only the built program swaps its files; from sources or a protected folder
        the download page is opened instead. Probed once: the view asks often."""
        if self._can_install is None:
            self._can_install = self.frozen and self._writable(self.app_root)
        return self._can_install

    def snapshot(self) -> dict:
        with self._lock:
            release = self.release
            return dict(state=self.state, current=self.current, latest=release.version if release else "",
                        notes=release.notes if release else "",
                        size_mb=round(release.asset_size / 2**20) if release and release.asset_size else 0,
                        page_url=release.page_url if release else RELEASES_PAGE,
                        progress=self.progress, message=self.message, error=self.error,
                        can_install=self.can_install)

    def _set(self, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
        self._notify()

    # -- commands
    def check(self) -> bool:
        """Ask GitHub in the background; False when something is already running."""
        with self._lock:
            if self.busy or self.state == "ready":
                return False
            self.state, self.message, self.error = "checking", "Проверяем обновления…", False
        self._notify()

        def run():
            try:
                release = self._fetch()
            except UpdateError as error:
                self._set(state="idle", message=str(error), error=True)
                return
            except Exception as error:                       # never crash the window over a check
                log.warning("Update check failed", exc_info=True)
                self._set(state="idle", message=f"Не удалось проверить обновления: {error}", error=True)
                return
            if release is None:
                self._set(state="latest", release=None, error=False,
                          message="Опубликованных версий пока нет.")
            elif is_newer(release.version, self.current):
                # the card's title and the banner already name the version
                self._set(state="available", release=release, error=False, message="")
            else:
                self._set(state="latest", release=release, error=False,
                          message=f"У вас последняя версия ({self.current}).")

        threading.Thread(target=run, name="update-check", daemon=True).start()
        return True

    def download(self) -> bool:
        with self._lock:
            release = self.release
            if self.busy or release is None or self.state not in ("available", "idle"):
                return False
            if not self.can_install:
                raise UpdateError("Отсюда программа не может обновить себя сама: скачайте новую версию на странице загрузки.")
            self._cancel.clear()
            self.state, self.progress, self.message, self.error = "downloading", 0.0, f"Скачиваем версию {release.version}…", False
        self._notify()

        def progress(done, total):
            if total:
                self._set(progress=min(1.0, done / total))

        def unpacking(done, total):
            if total:
                self._set(progress=min(1.0, done / total))

        def run():
            try:
                archive = self._download(release, progress, self._cancel.is_set)
                self._set(state="unpacking", progress=0.0, message="Распаковываем…")
                new_root = self._unpack(archive, unpacking, self._cancel.is_set)
            except UpdateCancelled:
                self._set(state="available", progress=0.0, message="Загрузка остановлена. Скачанная часть сохранится.", error=False)
                return
            except UpdateError as error:
                self._set(state="available", progress=0.0, message=str(error), error=True)
                return
            except Exception as error:
                log.warning("Update download failed", exc_info=True)
                self._set(state="available", progress=0.0, message=f"Не удалось скачать обновление: {error}", error=True)
                return
            self._set(state="ready", new_root=new_root, progress=1.0, error=False,
                      message=f"Версия {release.version} готова. OlegPainter закроется и откроется уже обновлённым.")

        threading.Thread(target=run, name="update-download", daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def install(self) -> bool:
        """Start the new version in installer mode; the caller then closes this copy."""
        with self._lock:
            if self.state != "ready" or self.new_root is None or self.release is None:
                return False
            args = installer_args(self.new_root, self.app_root, self.release.version)
        try:
            self._launch(args)
        except OSError as error:
            self._set(message=f"Не удалось запустить новую версию: {error}", error=True)
            return False
        self._set(state="installing", message="Закрываем OlegPainter для обновления…", error=False)
        return True


def _writable(folder: Path) -> bool:
    probe = Path(folder) / ".op_update_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
