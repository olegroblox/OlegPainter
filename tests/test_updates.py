"""Program updates without an installer (UPDATE-001): no network, no real install."""
import hashlib
import io
import json
import os
import time
import urllib.error
import zipfile
from pathlib import Path

import pytest

from application import updates


def release_payload(**changes):
    payload = dict(tag_name="v1.5", name="OlegPainter 1.5", body="## Что нового\n- быстрее", draft=False,
                   prerelease=False, html_url="https://github.com/olegroblox/OlegPainter/releases/tag/v1.5",
                   assets=[dict(name="OlegPainter-1.5-win64.zip", size=7,
                                browser_download_url="https://example.invalid/OlegPainter-1.5-win64.zip",
                                digest="sha256:" + hashlib.sha256(b"archive").hexdigest()),
                           dict(name="notes.txt", size=1, browser_download_url="https://example.invalid/notes.txt")])
    payload.update(changes)
    return payload


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_versions_compare_by_numbers():
    assert updates.parse_version("v1.4.1") == (1, 4, 1)
    assert updates.parse_version("OlegPainter 1.5") == (1, 5)
    assert updates.parse_version("latest") == ()
    assert updates.is_newer("1.4.1", "1.4") and updates.is_newer("v1.10", "1.9")
    assert not updates.is_newer("1.4", "1.4.0") and not updates.is_newer("1.3", "1.4")
    assert not updates.is_newer("", "1.4")


def test_release_picks_the_windows_archive_and_its_checksum():
    release = updates.parse_release(release_payload())
    assert release.version == "1.5" and release.asset_name == "OlegPainter-1.5-win64.zip"
    assert release.sha256 == hashlib.sha256(b"archive").hexdigest() and release.notes.startswith("## Что нового")
    assert updates.parse_release(release_payload(prerelease=True)) is None
    assert updates.parse_release(release_payload(draft=True)) is None
    assert updates.parse_release(release_payload(assets=[])) is None
    assert updates.parse_release("nonsense") is None


def test_latest_release_reads_github_and_explains_failures():
    seen = []

    def opener(request, timeout):
        seen.append(request.full_url)
        return Response(json.dumps(release_payload()).encode("utf-8"))

    release = updates.latest_release("1.4", url="https://api.example.invalid/latest", opener=opener)
    assert release.version == "1.5" and seen == ["https://api.example.invalid/latest"]

    def failing(code):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
        return opener

    assert updates.latest_release("1.4", opener=failing(404)) is None          # nothing published yet
    with pytest.raises(updates.UpdateError, match="через час"):
        updates.latest_release("1.4", opener=failing(403))

    def offline(request, timeout):
        raise urllib.error.URLError("no route")

    with pytest.raises(updates.UpdateError, match="Нет связи"):
        updates.latest_release("1.4", opener=offline)


def test_download_resumes_and_checks_the_digest(tmp_path):
    data = b"archive"
    release = updates.parse_release(release_payload())
    (tmp_path / (release.asset_name + ".part")).write_bytes(data[:3])           # an interrupted download
    ranges = []

    def opener(request, timeout):
        ranges.append(request.get_header("Range"))
        return Response(data[3:], status=206)

    progress = []
    path = updates.download(release, tmp_path, "1.4", progress=lambda done, total: progress.append((done, total)),
                            opener=opener)
    assert path.read_bytes() == data and ranges == ["bytes=3-"] and progress[-1] == (7, 7)
    assert updates.download(release, tmp_path, "1.4", opener=lambda *a, **k: pytest.fail("already here")) == path

    path.unlink()
    with pytest.raises(updates.UpdateError, match="контрольная сумма"):
        updates.download(release, tmp_path, "1.4", opener=lambda request, timeout: Response(b"broken!"))
    assert not (tmp_path / (release.asset_name + ".part")).exists()


def make_archive(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return path


def test_extract_finds_the_program_and_refuses_paths_outside(tmp_path):
    archive = make_archive(tmp_path / "update.zip", {"OlegPainter/OlegPainter.exe": "new exe",
                                                     "OlegPainter/_internal/lib.txt": "new lib",
                                                     "OlegPainter/README.md": "readme"})
    root = updates.extract(archive, tmp_path / "work")
    assert root == tmp_path / "work" / "unpacked" / "OlegPainter" and (root / "OlegPainter.exe").read_text() == "new exe"
    evil = make_archive(tmp_path / "evil.zip", {"OlegPainter/OlegPainter.exe": "x", "../outside.txt": "x"})
    with pytest.raises(updates.UpdateError, match="недопустимые"):
        updates.extract(evil, tmp_path / "work2")
    assert not (tmp_path / "outside.txt").exists()
    (tmp_path / "broken.zip").write_bytes(b"not a zip")
    with pytest.raises(updates.UpdateError, match="повреждён"):
        updates.extract(tmp_path / "broken.zip", tmp_path / "work3")


def installed(folder: Path, version: str) -> Path:
    (folder / "_internal").mkdir(parents=True)
    (folder / "OlegPainter.exe").write_text(f"exe {version}")
    (folder / "_internal" / "lib.txt").write_text(f"lib {version}")
    return folder


def test_install_swaps_program_files_and_keeps_user_data(tmp_path):
    target = installed(tmp_path / "OlegPainter", "1.4")
    (target / "_internal" / "only_old.txt").write_text("old")
    (target / "configs").mkdir()
    (target / "configs" / "session.json").write_text("my settings")
    (target / "models").mkdir()
    (target / "models" / "big.onnx").write_text("model")
    source = installed(tmp_path / "new" / "OlegPainter", "1.5")
    (source / "README.md").write_text("readme 1.5")
    updates.install_into(source, target, sleep=lambda _: None)
    assert (target / "OlegPainter.exe").read_text() == "exe 1.5" and (target / "README.md").read_text() == "readme 1.5"
    assert (target / "_internal" / "lib.txt").read_text() == "lib 1.5"
    assert not (target / "_internal" / "only_old.txt").exists()            # the whole old _internal is gone
    assert (target / "configs" / "session.json").read_text() == "my settings"
    assert (target / "models" / "big.onnx").read_text() == "model"
    assert not (target / ".update-backup").exists()


def test_failed_copy_puts_the_previous_version_back(tmp_path, monkeypatch):
    target = installed(tmp_path / "OlegPainter", "1.4")
    (target / "configs").mkdir()
    (target / "configs" / "session.json").write_text("my settings")
    source = installed(tmp_path / "new" / "OlegPainter", "1.5")

    def broken_copytree(src, dst, *args, **kwargs):
        Path(dst).mkdir()
        (Path(dst) / "half.txt").write_text("partial")
        raise OSError("disk went away")

    monkeypatch.setattr(updates.shutil, "copytree", broken_copytree)
    with pytest.raises(updates.UpdateError, match="Прежняя версия на месте"):
        updates.install_into(source, target, sleep=lambda _: None)
    assert (target / "OlegPainter.exe").read_text() == "exe 1.4"
    assert (target / "_internal" / "lib.txt").read_text() == "lib 1.4" and not (target / "_internal" / "half.txt").exists()
    assert (target / "configs" / "session.json").read_text() == "my settings"


def test_a_locked_file_leaves_everything_as_it_was(tmp_path, monkeypatch):
    target = installed(tmp_path / "OlegPainter", "1.4")
    source = installed(tmp_path / "new" / "OlegPainter", "1.5")
    real_replace = os.replace

    def replace(src, dst):
        if Path(src).name == "_internal" and Path(dst).parent.name == ".update-backup":
            raise PermissionError("in use")
        return real_replace(src, dst)

    monkeypatch.setattr(updates.os, "replace", replace)
    with pytest.raises(updates.UpdateError, match="заняты"):
        updates.install_into(source, target, sleep=lambda _: None)
    assert (target / "OlegPainter.exe").read_text() == "exe 1.4"            # moved aside, then put back
    assert (target / "_internal" / "lib.txt").read_text() == "lib 1.4"


def test_install_refuses_a_folder_that_is_not_olegpainter(tmp_path):
    source = installed(tmp_path / "new" / "OlegPainter", "1.5")
    stranger = tmp_path / "Documents"
    stranger.mkdir()
    (stranger / "thesis.docx").write_text("important")
    with pytest.raises(updates.UpdateError, match="не похожа"):
        updates.install_into(source, stranger)
    assert (stranger / "thesis.docx").read_text() == "important"


def test_handoff_arguments_round_trip(tmp_path, monkeypatch):
    args = updates.installer_args(tmp_path / "new", tmp_path / "OlegPainter", "1.5")
    options = updates.parse_flag_args(args[1:], updates.INSTALL_FLAG)
    assert options == dict(path=str(tmp_path / "OlegPainter"), wait_pid=os.getpid(), version="1.5")
    argv = ["OlegPainter.exe", updates.FINISHED_FLAG, str(updates.updates_dir()), "--wait-pid", "12", "--version", "1.5"]
    cleaned = []
    monkeypatch.setattr(updates, "cleanup", lambda folder, wait_pid: cleaned.append((folder, wait_pid)))
    monkeypatch.setattr(updates, "finished_update", None)
    assert updates.finish_on_start(argv, tmp_path) == ["OlegPainter.exe"]
    assert updates.finished_update == {"version": "1.5"}
    for _ in range(50):
        if cleaned:
            break
        time.sleep(0.02)
    assert cleaned == [(updates.updates_dir(), 12)]
    assert updates.finish_on_start(["OlegPainter.exe"], tmp_path) == ["OlegPainter.exe"]


def test_cleanup_removes_only_the_update_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    folder = updates.updates_dir()
    (folder / "unpacked").mkdir(parents=True)
    other = tmp_path / "Important"
    other.mkdir()
    updates.cleanup(other)
    assert other.exists()
    updates.cleanup(folder)
    assert not folder.exists()


def wait(center, *states):
    for _ in range(200):
        if center.state in states:
            return
        time.sleep(0.01)
    raise AssertionError(center.state)


def fake_center(tmp_path, **kwargs):
    release = updates.parse_release(release_payload())
    launched = []
    defaults = dict(frozen=True, work_dir=tmp_path / "updates", fetch=lambda: release,
                    fetch_archive=lambda release, progress, cancelled: progress(7, 7) or tmp_path / "update.zip",
                    unpack=lambda archive, progress, cancelled: tmp_path / "new" / "OlegPainter",
                    launch=launched.append, writable=lambda folder: True)
    defaults.update(kwargs)
    center = updates.UpdateCenter("1.4", tmp_path / "OlegPainter", **defaults)
    return center, launched


def test_center_goes_from_check_to_handing_over(tmp_path):
    center, launched = fake_center(tmp_path)
    assert center.check()
    wait(center, "available")
    assert center.snapshot()["latest"] == "1.5" and center.snapshot()["size_mb"] == 0
    assert center.download()
    wait(center, "ready")
    assert not center.check()                                  # a downloaded update is not thrown away
    assert center.install()
    assert launched[0][1] == updates.INSTALL_FLAG and launched[0][2] == str(tmp_path / "OlegPainter")
    assert center.state == "installing"


def test_center_from_sources_only_opens_the_page(tmp_path):
    center, _ = fake_center(tmp_path, frozen=False)
    center.check()
    wait(center, "available")
    assert not center.can_install
    with pytest.raises(updates.UpdateError, match="странице загрузки"):
        center.download()


def test_center_reports_a_failed_check_and_the_latest_version(tmp_path):
    def offline():
        raise updates.UpdateError("Нет связи с GitHub: проверьте интернет и попробуйте ещё раз.")

    center, _ = fake_center(tmp_path, fetch=offline)
    center.check()
    for _ in range(200):
        if center.error:
            break
        time.sleep(0.01)
    assert center.state == "idle" and "Нет связи" in center.message
    same = updates.parse_release(release_payload(tag_name="v1.4"))
    center, _ = fake_center(tmp_path, fetch=lambda: same)
    center.check()
    wait(center, "latest")
    assert "последняя версия" in center.message


def test_release_archive_has_the_program_and_no_personal_folders(tmp_path):
    from tools import package_release
    build = installed(tmp_path / "dist" / "OlegPainter", "1.5")
    for folder in ("configs", "logs", "models"):
        (build / folder).mkdir()
        (build / folder / "private.txt").write_text("personal")
    archive = package_release.package(build, tmp_path / "dist" / package_release.archive_name("1.5"))
    assert archive.name == "OlegPainter-1.5-win64.zip"
    names = zipfile.ZipFile(archive).namelist()
    assert "OlegPainter/OlegPainter.exe" in names and "OlegPainter/_internal/lib.txt" in names
    assert "OlegPainter/LICENSE" in names and not any("private" in name for name in names)
    assert updates.extract(archive, tmp_path / "work") == tmp_path / "work" / "unpacked" / "OlegPainter"


def test_offline_release_file_works_like_github(tmp_path, monkeypatch):
    """OLEGPAINTER_UPDATE_URL may point at local files (file:///…) to rehearse a
    release without the network: urllib's file responses have no HTTP status."""
    archive = make_archive(tmp_path / "OlegPainter-1.5-win64.zip", {"OlegPainter/OlegPainter.exe": "new exe"})
    payload = release_payload(assets=[dict(name=archive.name, size=archive.stat().st_size,
                                           browser_download_url=archive.as_uri(),
                                           digest="sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest())])
    (tmp_path / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("OLEGPAINTER_UPDATE_URL", (tmp_path / "latest.json").as_uri())
    release = updates.latest_release("1.4")
    assert release.version == "1.5" and release.asset_url.startswith("file:")
    downloaded = updates.download(release, tmp_path / "work", "1.4")
    assert downloaded.read_bytes() == archive.read_bytes()
    assert (updates.extract(downloaded, tmp_path / "work") / "OlegPainter.exe").read_text() == "new exe"
