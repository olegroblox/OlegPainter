"""Validate local Markdown links in active docs; archived text is not guidance."""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    # Public documents always; AGENTS.md and internal/ exist only in the local working copy.
    top = ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md", "AGENTS.md")
    documents = [ROOT / name for name in top if (ROOT / name).is_file()]
    documents += sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / "internal" / "docs").glob("*.md"))
    missing = []
    for document in documents:
        for target in LINK.findall(document.read_text(encoding="utf-8")):
            target = target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = (document.parent / unquote(parsed.path)).resolve()
            if not path.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    if missing:
        print("Broken local documentation links:\n" + "\n".join(missing))
        return 1
    print(f"OK local links in {len(documents)} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
