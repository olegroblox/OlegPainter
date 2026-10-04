from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.helpers.config_migration import CONFIG_SCHEMA_VERSION, sanitize_config_document
from ui.helpers.config_store import ConfigStore


def _write(path: Path, updated_at: str) -> None:
    doc = {
        "meta": {
            "name": path.stem,
            "slug": path.stem,
            "categories": ["drawing"],
            "created_at": updated_at,
            "updated_at": updated_at,
            "version": CONFIG_SCHEMA_VERSION,
        },
        "payload": {"drawing": {}},
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


class ConfigStoreTzAwareTests(unittest.TestCase):
    """Regression: ConfigStore.list() must not crash when configs mix
    timezone-aware and naive timestamps (an imported config with a '+00:00'
    offset otherwise makes the descriptor sort raise TypeError)."""

    def test_list_handles_mixed_tz_awareness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write(base / "naive.json", "2026-01-01T10:00:00")
            _write(base / "aware.json", "2026-02-01T10:00:00+00:00")
            store = ConfigStore(base_dir=base)
            descriptors = store.list()  # must not raise TypeError
            self.assertEqual(len(descriptors), 2)
            # Newest first; the Feb (aware) entry normalized to naive UTC wins.
            self.assertEqual(descriptors[0].slug, "aware")


class ConfigMigrationVersionTests(unittest.TestCase):
    """Regression: migration must not downgrade a config written by a newer
    build (version compare was != instead of <)."""

    def test_newer_version_not_downgraded(self) -> None:
        doc = {"meta": {"name": "x", "version": CONFIG_SCHEMA_VERSION + 5}, "payload": {}}
        cleaned, _changed = sanitize_config_document(doc)
        self.assertEqual(cleaned["meta"]["version"], CONFIG_SCHEMA_VERSION + 5)

    def test_older_version_upgraded(self) -> None:
        doc = {"meta": {"name": "x", "version": 1}, "payload": {}}
        cleaned, _changed = sanitize_config_document(doc)
        self.assertEqual(cleaned["meta"]["version"], CONFIG_SCHEMA_VERSION)

    def test_missing_version_set(self) -> None:
        doc = {"meta": {"name": "x"}, "payload": {}}
        cleaned, _changed = sanitize_config_document(doc)
        self.assertEqual(cleaned["meta"]["version"], CONFIG_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
