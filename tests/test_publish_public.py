"""The public mirror: before a commit the repository folder becomes exactly the export."""
import subprocess

import pytest

from tools.publish_public import changes, check_author, mirror


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, encoding="utf-8", check=True).stdout


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "public"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "tester")
    git(repo, "config", "user.email", "1+tester@users.noreply.github.com")
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (repo / "keep.txt").write_text("old\n", encoding="utf-8")
    (repo / "gone").mkdir()
    (repo / "gone" / "old.txt").write_text("remove me\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    return repo


def test_mirror_makes_the_repository_equal_to_the_export(tmp_path, repo):
    source = tmp_path / "source"
    (source / "new").mkdir(parents=True)
    (source / ".gitignore").write_bytes((repo / ".gitignore").read_bytes())
    (source / "keep.txt").write_text("new\n", encoding="utf-8")
    (source / "new" / "file.txt").write_text("added\n", encoding="utf-8")
    # Left in the folder by hand: it must not slip into the public commit.
    (repo / "stray.txt").write_text("left by hand\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "x.pyc").write_bytes(b"cache")

    mirror(source, [".gitignore", "keep.txt", "new/file.txt"], repo)

    assert sorted(changes(repo)) == ["A\tnew/file.txt", "D\tgone/old.txt", "M\tkeep.txt"]
    assert not (repo / "stray.txt").exists() and not (repo / "gone").exists()
    assert (repo / "__pycache__" / "x.pyc").exists() and (repo / ".git").is_dir()


def test_nothing_changes_when_the_export_is_already_published(tmp_path, repo):
    source = tmp_path / "source"
    names = [".gitignore", "keep.txt", "gone/old.txt"]
    for name in names:
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_bytes((repo / name).read_bytes())

    mirror(source, names, repo)

    assert changes(repo) == []


def test_commits_need_the_github_no_reply_address(repo):
    assert check_author(repo).endswith("@users.noreply.github.com")
    git(repo, "config", "user.email", "person@example.com")
    with pytest.raises(RuntimeError, match="скрытая почта"):
        check_author(repo)
