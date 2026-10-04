"""Interface language for the QML shell.

Russian is the source language: QML wraps its texts in qsTr() and Python view
texts pass through `text()`. English comes from ui/quick/i18n/en.json, keyed by
the Russian source, so a missing entry simply stays Russian.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QTranslator

LANGUAGES = ("ru", "en")
_DIR = Path(__file__).with_name("i18n")
_table: dict[str, str] = {}
# Keys that start a longer message ("Неизвестная команда: <code>").
_prefixes: list[tuple[str, str]] = []


# Messages with numbers inside ("пауза на повороте 0.1 мс"): regex, replacement.
_patterns: list[tuple[re.Pattern, str]] = []


def load(language: str) -> dict:
    if language == "ru":
        return {}
    return json.loads((_DIR / f"{language}.json").read_text(encoding="utf-8"))


def _engine_pairs(language: str) -> dict[str, str]:
    """Engine status texts already exist in both languages, keyed by id."""
    from engine.olegpainter.translations import LANGUAGES as engine_texts
    source, target = engine_texts.get("ru", {}), engine_texts.get(language, {})
    return {text: target[key] for key, text in source.items()
            if key in target and isinstance(text, str) and "{" not in text}


def activate(language: str) -> None:
    global _table, _prefixes, _patterns
    data = load(language)
    patterns = data.pop("__patterns__", [])
    _table = {**(_engine_pairs(language) if data else {}), **data}
    _prefixes = sorted(((k, v) for k, v in _table.items() if k.endswith((": ", ":  "))),
                       key=lambda item: -len(item[0]))
    _patterns = [(re.compile(pattern), replacement) for pattern, replacement in patterns]


def text(source):
    """Translate one view string; unknown strings stay as they are."""
    if not _table or not isinstance(source, str) or not source:
        return source
    found = _table.get(source)
    if found is not None:
        return found
    if "\n" in source:  # the start refusal lists every missing step on its own line
        return "\n".join(text(line) for line in source.split("\n"))
    if source.startswith("• ") and len(source) > 2:
        return "• " + text(source[2:])
    if source.endswith(" ✓") and source[:-2] in _table:  # a done step keeps its mark
        return _table[source[:-2]] + " ✓"
    for key, value in _prefixes:
        if source.startswith(key):
            return value + source[len(key):]
    result = source
    for pattern, replacement in _patterns:
        result = pattern.sub(replacement, result)
    return result


def view(data):
    """Translate every string inside a view payload (dicts and lists)."""
    if not _table:
        return data
    if isinstance(data, dict):
        return {key: view(value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [view(value) for value in data]
    return text(data)


class DictTranslator(QTranslator):
    """qsTr() lookups served from the same table as the Python texts."""

    def translate(self, context, source, disambiguation=None, n=-1):
        # None is a null QString: Qt then keeps the source text. An empty
        # string would count as a translation and blank the label.
        return _table.get(source)

    def isEmpty(self):
        return not _table
