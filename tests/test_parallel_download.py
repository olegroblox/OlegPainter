# -*- coding: utf-8 -*-
"""Segmented (multi-connection) download: several parallel HTTP Range requests
assemble one correct file and verify its checksum. This is the fix for "slow on
a fast link" — GitHub/S3 CDNs throttle per connection. Falls back to a single
stream when the server has no Range support or the file is small.

Uses a local ThreadingHTTPServer (SimpleHTTPRequestHandler honours Range)."""
from __future__ import annotations

import hashlib
import http.server
import os
import sys
import threading
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


def _make_range_handler(directory: Path):
    class _RangeHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            try:
                data = (directory / self.path.lstrip("/")).read_bytes()
            except Exception:
                self.send_error(404)
                return
            total = len(data)
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                s, _, e = rng[len("bytes="):].partition("-")
                start = int(s) if s else 0
                end = int(e) if e else total - 1
                end = min(end, total - 1)
                body = data[start:end + 1]
                self.send_response(206)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(total))
                self.end_headers()
                self.wfile.write(data)
    return _RangeHandler


class _RangeServer:
    def __init__(self, directory: Path):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_range_handler(directory))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _worker(tmp: Path) -> DownloadWorker:
    reg = AiModelRegistry(models_root=tmp / "m", manifest_path=tmp / "m" / "manifest.json")
    desc = AiModelDescriptor(id="x", task=ModelTask.BACKGROUND, provider="t",
                             title={}, description={}, install_type="download", runtime={})
    return DownloadWorker(reg, desc, chunk_size=256 * 1024)


class ParallelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="pdl_"))
        self.srv = _RangeServer(self.tmp)

    def tearDown(self):
        self.srv.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_payload(self, name: str, size: int) -> tuple[str, str]:
        data = os.urandom(size)
        (self.tmp / name).write_bytes(data)
        return hashlib.sha256(data).hexdigest(), hashlib.md5(data).hexdigest()

    def test_parallel_assembles_correct_file_and_verifies_sha(self):
        sha, _ = self._make_payload("big.bin", 20 * 1024 * 1024)   # 20 MB -> parallel
        dest = self.tmp / "out.bin"
        w = _worker(self.tmp)
        ok = w._parallel_download(self.srv.url("big.bin"), dest,
                                  expected_hash=sha, expected_md5=None, expected_size=20 * 1024 * 1024)
        self.assertTrue(ok, "20 MB with Range support must use the parallel path")
        self.assertEqual(dest.stat().st_size, 20 * 1024 * 1024)
        self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), sha,
                         "assembled bytes must match the source exactly")

    def test_parallel_md5_path(self):
        _, md5 = self._make_payload("big.bin", 18 * 1024 * 1024)
        dest = self.tmp / "out.bin"
        w = _worker(self.tmp)
        ok = w._parallel_download(self.srv.url("big.bin"), dest,
                                  expected_hash=None, expected_md5=md5, expected_size=18 * 1024 * 1024)
        self.assertTrue(ok)
        self.assertEqual(hashlib.md5(dest.read_bytes()).hexdigest(), md5)

    def test_bad_checksum_raises(self):
        self._make_payload("big.bin", 17 * 1024 * 1024)
        dest = self.tmp / "out.bin"
        w = _worker(self.tmp)
        with self.assertRaises(RuntimeError):
            w._parallel_download(self.srv.url("big.bin"), dest,
                                 expected_hash="0" * 64, expected_md5=None, expected_size=17 * 1024 * 1024)

    def test_small_file_declines_parallel(self):
        sha, _ = self._make_payload("small.bin", 1 * 1024 * 1024)   # 1 MB < 16 MB threshold
        dest = self.tmp / "out.bin"
        w = _worker(self.tmp)
        self.assertFalse(
            w._parallel_download(self.srv.url("small.bin"), dest,
                                 expected_hash=sha, expected_md5=None, expected_size=1 * 1024 * 1024),
            "small files should fall back to single stream")

    def test_probe_reports_range_support_and_size(self):
        self._make_payload("big.bin", 20 * 1024 * 1024)
        w = _worker(self.tmp)
        total, supports = w._probe_range(self.srv.url("big.bin"))
        self.assertTrue(supports)
        self.assertEqual(total, 20 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
