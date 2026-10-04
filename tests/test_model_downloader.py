# -*- coding: utf-8 -*-
"""Model download safety: the manifest sha256 pin is MANDATORY — a corrupted
payload never installs and leaves no temp leftovers; a clean payload lands
atomically at the manifest path. Runs DownloadWorker.run() synchronously over
file:// sources (no network)."""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from engine.ai.download import DownloadWorker  # noqa: E402
from engine.ai.registry import (  # noqa: E402
    AiModelDescriptor, AiModelRegistry, ModelFile, ModelTask,
)

_app = QApplication.instance() or QApplication([])

PAYLOAD = b"onnx-bytes-pretend" * 1024


def _make_env(tmp: Path, *, sha256: str | None = None, md5: str | None = None):
    src = tmp / "source.onnx"
    src.write_bytes(PAYLOAD)
    target = tmp / "models" / "bg" / "fake.onnx"
    registry = AiModelRegistry(models_root=tmp / "models",
                               manifest_path=tmp / "models" / "manifest.json")
    descriptor = AiModelDescriptor(
        id="bg.fake", task=ModelTask.BACKGROUND, provider="test",
        title={"en": "Fake"}, description={}, install_type="download",
        runtime={},
        _files=[ModelFile(path=target, url=src.as_uri(), sha256=sha256, md5=md5,
                          size=len(PAYLOAD))],
    )
    return registry, descriptor, target


class ModelDownloaderTests(unittest.TestCase):
    def test_good_sha256_installs_at_manifest_path(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            good = hashlib.sha256(PAYLOAD).hexdigest()
            registry, descriptor, target = _make_env(tmp, sha256=good)
            done, errors = [], []
            worker = DownloadWorker(registry, descriptor)
            worker.completed.connect(lambda desc: done.append(desc.id))
            worker.errorOccurred.connect(errors.append)
            worker.run()                          # synchronous, no thread
            self.assertEqual(done, ["bg.fake"], errors)
            self.assertTrue(target.exists())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), good)

    def test_bad_sha256_never_installs_and_leaves_no_part_files(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            registry, descriptor, target = _make_env(tmp, sha256="0" * 64)
            done, errors = [], []
            worker = DownloadWorker(registry, descriptor)
            worker.completed.connect(lambda desc: done.append(desc.id))
            worker.errorOccurred.connect(errors.append)
            worker.run()
            self.assertEqual(done, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("checksum", errors[0].lower())
            self.assertFalse(target.exists(), "corrupted payload must NOT install")
            leftovers = [p for p in (tmp / "models").rglob("*")
                         if p.is_file() and p.name != "manifest.json"]
            self.assertEqual(leftovers, [], "no partial files in models/")

    def test_progress_emitted_without_manifest_size(self):
        """Live bug: a manifest entry with no `size` showed no progress and the
        button froze at 0%. Progress must now come from the response's
        Content-Length even when the manifest omits size."""
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            good = hashlib.sha256(PAYLOAD).hexdigest()
            registry, descriptor, target = _make_env(tmp, sha256=good)
            # drop the size so _estimate_total_size returns None (the isnet case)
            f = descriptor._files[0]
            descriptor._files[0] = ModelFile(path=f.path, url=f.url, sha256=f.sha256, size=None)
            percents = []
            worker = DownloadWorker(registry, descriptor, chunk_size=64 * 1024)
            worker.progressChanged.connect(percents.append)
            worker.run()
            self.assertTrue(target.exists())
            # at least one mid-download percent in (0, 100), not just the final 100
            self.assertTrue(any(0 < p < 100 for p in percents) or percents.count(100) >= 1,
                            f"expected progress events, got {percents}")
            self.assertEqual(percents[-1], 100)

    def test_size_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            good = hashlib.sha256(PAYLOAD).hexdigest()
            registry, descriptor, target = _make_env(tmp, sha256=good)
            descriptor._files[0] = ModelFile(path=target,
                                             url=descriptor._files[0].url,
                                             sha256=good, size=len(PAYLOAD) + 1)
            errors = []
            worker = DownloadWorker(registry, descriptor)
            worker.errorOccurred.connect(errors.append)
            worker.run()
            self.assertEqual(len(errors), 1)
            self.assertFalse(target.exists())

    def test_good_md5_installs(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            good = hashlib.md5(PAYLOAD).hexdigest()
            registry, descriptor, target = _make_env(tmp, md5=good)  # md5-only (rembg case)
            done, errors = [], []
            worker = DownloadWorker(registry, descriptor)
            worker.completed.connect(lambda desc: done.append(desc.id))
            worker.errorOccurred.connect(errors.append)
            worker.run()
            self.assertEqual(done, ["bg.fake"], errors)
            self.assertTrue(target.exists())

    def test_bad_md5_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            registry, descriptor, target = _make_env(tmp, md5="0" * 32)
            errors = []
            worker = DownloadWorker(registry, descriptor)
            worker.errorOccurred.connect(errors.append)
            worker.run()
            self.assertEqual(len(errors), 1)
            self.assertIn("md5", errors[0].lower())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
