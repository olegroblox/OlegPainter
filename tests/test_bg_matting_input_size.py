# -*- coding: utf-8 -*-
"""OnnxBgMatting input-size resolution: a FIXED ONNX input wins; a DYNAMIC
input falls back to the manifest's runtime.input_size (so heavy exports like
BiRefNet run at 1024, not the 320 default); nothing → 320."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.ai.bg_matting import OnnxBgMatting


class InputSizeResolutionTests(unittest.TestCase):
    def test_fixed_onnx_dims_are_used_verbatim(self):
        # shape is [N, C, H, W]; returns (w, h)
        self.assertEqual(OnnxBgMatting._extract_input_size([1, 3, 320, 320]), (320, 320))
        self.assertEqual(OnnxBgMatting._extract_input_size([1, 3, 1024, 768]), (768, 1024))

    def test_dynamic_dims_return_none(self):
        self.assertIsNone(OnnxBgMatting._extract_input_size([1, 3, "height", "width"]))
        self.assertIsNone(OnnxBgMatting._extract_input_size([1, 3, -1, -1]))
        self.assertIsNone(OnnxBgMatting._extract_input_size([1, 3]))

    def test_runtime_input_size_parsed_h_w(self):
        self.assertEqual(OnnxBgMatting._runtime_input_size({"input_size": [1024, 1024]}), (1024, 1024))
        self.assertEqual(OnnxBgMatting._runtime_input_size({"input_size": [512, 256]}), (256, 512))
        self.assertIsNone(OnnxBgMatting._runtime_input_size({}))
        self.assertIsNone(OnnxBgMatting._runtime_input_size({"input_size": [1024]}))
        self.assertIsNone(OnnxBgMatting._runtime_input_size({"input_size": "big"}))

    def test_resolution_priority_fixed_then_runtime_then_default(self):
        """Mirrors _load_session: fixed meta -> runtime -> 320."""
        def resolve(meta_shape, runtime_cfg):
            return (OnnxBgMatting._extract_input_size(meta_shape)
                    or OnnxBgMatting._runtime_input_size(runtime_cfg)
                    or (320, 320))

        # fixed meta wins even if runtime disagrees
        self.assertEqual(resolve([1, 3, 320, 320], {"input_size": [1024, 1024]}), (320, 320))
        # dynamic meta -> manifest size (BiRefNet case)
        self.assertEqual(resolve([1, 3, "h", "w"], {"input_size": [1024, 1024]}), (1024, 1024))
        # nothing -> default
        self.assertEqual(resolve([1, 3, "h", "w"], {}), (320, 320))


if __name__ == "__main__":
    unittest.main()
