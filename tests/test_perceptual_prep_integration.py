from __future__ import annotations

import os
import unittest

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter


class PerceptualPrepIntegrationTests(unittest.TestCase):
    def _make_engine(self) -> OlegPainter:
        engine = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        engine.draw_region = (0, 0, 8, 8)
        engine.mode = "color"
        engine.k_clusters = 3
        engine.brush_size = 2
        engine.fast_min_region_area = 2
        engine.prep_color_space = "oklab"
        engine.prep_quantization_mode = "minibatch"
        engine.prep_cleanup_mode = "vectorized_plus_stray_merge"
        engine.background_removal_enabled = True
        engine.remove_background = True
        engine.background_removal_mode = "alpha"
        engine.background_alpha_threshold = 10
        return engine

    def test_prepare_image_and_palette_perceptual_pipeline_respects_alpha_background(self) -> None:
        engine = self._make_engine()
        rgba = np.zeros((8, 8, 4), dtype=np.uint8)
        rgba[:4, :4] = (255, 0, 0, 255)
        rgba[4:, :4] = (0, 255, 0, 255)
        rgba[4:, 4:] = (0, 0, 255, 255)
        engine.source_pil_image = Image.fromarray(rgba)

        prepared = engine.prepare_image_and_palette()

        self.assertTrue(prepared)
        self.assertIsNotNone(engine.cluster_map)
        assert engine.cluster_map is not None
        self.assertEqual(engine.cluster_map.shape, (4, 4))
        background_cells = int(np.count_nonzero(engine.cluster_map == -1))
        self.assertGreater(background_cells, 0)
        self.assertEqual(len(engine.color_palette), 3)
        self.assertEqual(sum(int(entry[2]) for entry in engine.color_palette) + background_cells, 16)
        dominant_channels = {int(np.argmax(np.asarray(entry[3]))) for entry in engine.color_palette}
        self.assertEqual(dominant_channels, {0, 1, 2})
        self.assertFalse(any(str(entry[0]).upper() == "#FFFFFF" for entry in engine.color_palette))

    def test_prep_settings_round_trip_through_engine_config(self) -> None:
        engine = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        engine.prep_color_space = "cielab"
        engine.prep_quantization_mode = "minibatch"
        engine.prep_cleanup_mode = "vectorized"

        config = engine.get_config()

        restored = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        restored.load_config(config)

        self.assertEqual(restored.prep_color_space, "cielab")
        self.assertEqual(restored.prep_quantization_mode, "minibatch")
        self.assertEqual(restored.prep_cleanup_mode, "vectorized")

    def test_perceptual_pipeline_preserves_legacy_small_region_merge_semantics(self) -> None:
        engine = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        engine.draw_region = (0, 0, 5, 4)
        engine.mode = "color"
        engine.k_clusters = 3
        engine.brush_size = 1
        engine.fast_min_region_area = 5
        engine.prep_color_space = "oklab"
        engine.prep_quantization_mode = "kmeans"
        engine.prep_cleanup_mode = "vectorized_plus_stray_merge"
        rgba = np.zeros((4, 5, 4), dtype=np.uint8)
        rgba[:, :3] = (255, 0, 0, 255)
        rgba[:, 3:] = (250, 0, 0, 255)
        rgba[1:3, 1:3] = (252, 0, 0, 255)
        engine.source_pil_image = Image.fromarray(rgba)

        prepared = engine.prepare_image_and_palette()

        self.assertTrue(prepared)
        assert engine.cluster_map is not None
        highlight = engine._compute_small_component_mask(engine.fast_min_region_area, -1)
        self.assertIsNone(highlight)
        self.assertFalse(np.any(engine.cluster_map == -1))

    def test_perceptual_pipeline_uses_large_area_optimization_and_keeps_full_shape(self) -> None:
        engine = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        engine.draw_region = (0, 0, 128, 128)
        engine.mode = "color"
        engine.k_clusters = 4
        engine.brush_size = 1
        engine.fast_min_region_area = 0
        engine.prep_color_space = "oklab"
        engine.prep_quantization_mode = "minibatch"
        engine.prep_cleanup_mode = "off"
        engine.kmeans_predict_max_pixels = 10000
        logs: list[str] = []
        engine._log = lambda message, *_args, **_kwargs: logs.append(str(message))

        rgba = np.zeros((128, 128, 4), dtype=np.uint8)
        rgba[:64, :64] = (255, 0, 0, 255)
        rgba[:64, 64:] = (0, 255, 0, 255)
        rgba[64:, :64] = (0, 0, 255, 255)
        rgba[64:, 64:] = (255, 255, 0, 255)
        engine.source_pil_image = Image.fromarray(rgba)

        prepared = engine.prepare_image_and_palette()

        self.assertTrue(prepared)
        assert engine.cluster_map is not None
        self.assertEqual(engine.cluster_map.shape, (128, 128))
        self.assertGreaterEqual(len(engine.color_palette), 3)
        self.assertTrue(any("Perceptual large area optimization" in entry for entry in logs))

    def _speckled_engine(self, mode: str, min_area: int) -> OlegPainter:
        engine = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        engine.draw_region = (0, 0, 16, 16)
        engine.mode = "color"
        engine.k_clusters = 2
        engine.brush_size = 1
        engine.fast_min_region_area = min_area
        engine.prep_color_space = "oklab"
        engine.prep_quantization_mode = "kmeans"
        engine.prep_cleanup_mode = mode
        rgba = np.zeros((16, 16, 4), dtype=np.uint8)
        rgba[:, :] = (255, 0, 0, 255)
        for r, c in ((3, 3), (3, 12), (8, 6), (12, 2), (12, 13)):
            rgba[r, c] = (0, 0, 255, 255)
        engine.source_pil_image = Image.fromarray(rgba)
        return engine

    def test_cleanup_mode_no_longer_requires_min_region_area_field(self) -> None:
        """Regression (live report «переключаю Очистку — ничего не меняется»):
        with «min region area» at 0 the cleanup combo was a silent no-op even in
        the perceptual space. An explicit cleanup mode must now run with its own
        default speck size instead of being disabled by the unrelated field."""
        import engine.olegpainter.core as core_mod

        calls: list[dict] = []
        original = core_mod.apply_cleanup_mode

        def spy(cluster_map, rgb, **kwargs):
            calls.append(dict(kwargs))
            return original(cluster_map, rgb, **kwargs)

        core_mod.apply_cleanup_mode = spy
        try:
            engine = self._speckled_engine("vectorized", min_area=0)
            self.assertTrue(engine.prepare_image_and_palette())
        finally:
            core_mod.apply_cleanup_mode = original
        self.assertEqual(len(calls), 1, "cleanup must run even with the field at 0")
        self.assertEqual(calls[0]["mode"], "vectorized")
        self.assertEqual(calls[0]["min_area"], 4)  # the self-sufficient default

    def test_cleanup_mode_respects_explicit_min_region_area(self) -> None:
        import engine.olegpainter.core as core_mod

        calls: list[dict] = []
        original = core_mod.apply_cleanup_mode

        def spy(cluster_map, rgb, **kwargs):
            calls.append(dict(kwargs))
            return original(cluster_map, rgb, **kwargs)

        core_mod.apply_cleanup_mode = spy
        try:
            engine = self._speckled_engine("vectorized_plus_stray_merge", min_area=7)
            self.assertTrue(engine.prepare_image_and_palette())
        finally:
            core_mod.apply_cleanup_mode = original
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["min_area"], 7)

    def test_cleanup_off_never_calls_cleanup(self) -> None:
        import engine.olegpainter.core as core_mod

        calls: list[dict] = []
        original = core_mod.apply_cleanup_mode

        def spy(cluster_map, rgb, **kwargs):
            calls.append(dict(kwargs))
            return original(cluster_map, rgb, **kwargs)

        core_mod.apply_cleanup_mode = spy
        try:
            engine = self._speckled_engine("off", min_area=0)
            self.assertTrue(engine.prepare_image_and_palette())
        finally:
            core_mod.apply_cleanup_mode = original
        self.assertEqual(calls, [])

    def test_manual_palette_mode_projects_preview_palette_to_captured_colors(self) -> None:
        engine = OlegPainter(status_callback=lambda *_args, **_kwargs: None)
        engine.draw_region = (0, 0, 4, 4)
        engine.mode = "color"
        engine.k_clusters = 2
        engine.brush_size = 1
        engine.color_picking_method = "manual_palette"
        engine.manual_palette_mix_enabled = False
        engine.manual_palette_coords = [
            {"x": 0, "y": 0, "rgb": (0, 255, 0)},
            {"x": 1, "y": 1, "rgb": (255, 255, 0)},
        ]

        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        rgba[:, :2] = (255, 0, 0, 255)
        rgba[:, 2:] = (0, 0, 255, 255)
        engine.source_pil_image = Image.fromarray(rgba)

        prepared = engine.prepare_image_and_palette()

        self.assertTrue(prepared)
        self.assertTrue(engine._cluster_uses_manual_palette)
        palette_rgbs = {tuple(int(v) for v in entry[3]) for entry in engine.color_palette}
        self.assertTrue(palette_rgbs)
        self.assertTrue(palette_rgbs.issubset({(0, 255, 0), (255, 255, 0)}))
        self.assertEqual(len(engine._manual_palette_cluster_plan), len(engine.color_palette))


if __name__ == "__main__":
    unittest.main()
