"""Update the public GitHub copy in one step.

    dev.ps1 publish                      what would change on GitHub (nothing is sent)
    dev.ps1 publish "что изменилось"     the same, then commit and push

The repository folder (OLEGPAINTER_PUBLIC_REPO, otherwise «OlegPainter-github» next
to the project) is only a mirror of tools/export_public.py: every run makes it equal
to a fresh export, so never edit files there by hand. Commits use that repository's
own author, which must be the GitHub no-reply address.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_public import ROOT, problems, public_files  # noqa: E402

NOREPLY = "@users.noreply.github.com"


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=repo,
                            capture_output=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def default_repo() -> Path:
    configured = os.environ.get("OLEGPAINTER_PUBLIC_REPO", "").strip()
    return Path(configured) if configured else ROOT.parent / "OlegPainter-github"


def _inside(repo: Path, name: str) -> Path:
    path = (repo / name).resolve()
    if repo not in path.parents or ".git" in Path(name).parts:
        raise RuntimeError(f"Путь вне репозитория: {name}")
    return path


def mirror(source_root: Path, files: list[str], repo: Path) -> None:
    """Make the repository's working tree equal to `files` from `source_root`.

    Tracked and untracked (not ignored) files missing from the export are removed,
    so nothing stray in the repository folder can be published. Ignored files stay.
    """
    repo = repo.resolve()
    wanted = set(files)
    present = [name for name in git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") if name]
    for name in present:
        if name not in wanted:
            path = _inside(repo, name)
            if path.is_file():
                path.unlink()
    for name in files:
        source = source_root / name
        target = _inside(repo, name)
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for folder in sorted((p for p in repo.rglob("*") if p.is_dir() and ".git" not in p.relative_to(repo).parts),
                         key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()


def changes(repo: Path) -> list[str]:
    git(repo, "add", "-A")
    return [line for line in git(repo, "diff", "--cached", "--name-status").splitlines() if line.strip()]


def check_author(repo: Path) -> str:
    email = git(repo, "config", "user.email", check=False).strip()
    if not email.endswith(NOREPLY):
        raise RuntimeError(f"Автор коммитов в {repo} — «{email or 'не задан'}». Нужна скрытая почта GitHub "
                           f"(…{NOREPLY}): git -C \"{repo}\" config user.email <адрес>")
    return email


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Обновить публичную копию на GitHub")
    parser.add_argument("message", nargs="?", help="что изменилось; без него — только предпросмотр")
    parser.add_argument("--message-file", help="описание из файла (несколько строк)")
    parser.add_argument("--repo", help="папка публичного репозитория")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve() if args.repo else default_repo().resolve()
    if not (repo / ".git").is_dir():
        print(f"Нет репозитория: {repo}\nСоздайте его выгрузкой tools/export_public.py и git init.")
        return 2
    files = public_files()
    found = problems(files)
    if found:
        print("Обновление остановлено:\n  " + "\n  ".join(found))
        return 1
    mirror(ROOT, files, repo)
    changed = changes(repo)
    if not changed:
        print("Изменений нет: на GitHub уже актуальная версия.")
        return 0
    counts = {kind: sum(1 for line in changed if line.startswith(kind)) for kind in "AMDR"}
    print(f"Изменится файлов: {len(changed)} (новых {counts['A']}, изменённых {counts['M']}, "
          f"удалённых {counts['D']}, переименованных {counts['R']})")
    print("\n".join("  " + line for line in changed[:40]) + ("\n  …" if len(changed) > 40 else ""))
    message = Path(args.message_file).read_text(encoding="utf-8") if args.message_file else args.message
    if not message or not message.strip():
        print('\nЭто предпросмотр. Отправить: dev.ps1 publish "что изменилось"')
        return 0
    check_author(repo)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(message.strip() + "\n")
    try:
        git(repo, "commit", "-q", "-F", handle.name)
    finally:
        os.unlink(handle.name)
    print("Сохранено: " + git(repo, "log", "--oneline", "-1").strip())
    pushed = subprocess.run(["git", "push"], cwd=repo, capture_output=True, encoding="utf-8", errors="replace")
    if pushed.returncode != 0:
        print("Не удалось отправить на GitHub:\n" + (pushed.stderr or pushed.stdout).strip()
              + f"\nИзменения сохранены в {repo}. Повторите: git -C \"{repo}\" push")
        return 1
    print("Отправлено на GitHub.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
