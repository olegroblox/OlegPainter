"""Package-1 breakthrough tests: numba-accelerated dithering equivalence and
OKLab manual-palette matching."""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import engine.olegpainter.prep_pipeline as pp
from engine.olegpainter.color_spaces import rgb_to_cielab_numpy, rgb_to_oklab_numpy
from engine.olegpainter.core import OlegPainter


# --- Floyd-Steinberg: JIT path must be pixel-identical to the pure-Python path ---

def _run_fs_pure_python(img, centers, valid):
    saved = pp._FS_JIT
    try:
        pp._FS_JIT = False  # _get_fs_jit() treats False as "unavailable"
        return pp.floyd_steinberg_labels(img, centers, valid)
    finally:
        pp._FS_JIT = saved


def test_fs_dither_jit_matches_pure_python_exactly():
    rng = np.random.default_rng(42)
    h, w, k = 48, 37, 7
    img = rng.uniform(0.0, 100.0, size=(h, w, 3))
    centers = rng.uniform(0.0, 100.0, size=(k, 3))
    valid = rng.random((h, w)) > 0.2

    fast = pp.floyd_steinberg_labels(img, centers, valid)
    slow = _run_fs_pure_python(img, centers, valid)

    assert np.array_equal(fast, slow)
    assert np.all(fast[~valid] == -1)
    assert np.all(fast[valid] >= 0)


def test_fs_dither_uniform_image_single_center_label():
    img = np.full((6, 5, 3), 10.0)
    centers = np.array([[10.0, 10.0, 10.0], [90.0, 90.0, 90.0]])
    valid = np.ones((6, 5), dtype=bool)
    labels = pp.floyd_steinberg_labels(img, centers, valid)
    assert np.all(labels == 0)


def test_fs_jit_is_active_when_numba_installed():
    try:
        import numba  # noqa: F401
    except ImportError:
        return  # environment without numba: fallback path is the expected mode
    assert pp._get_fs_jit() is not None


# --- Manual palette: OKLab matching ---

def _make_engine_with_palette(rgbs):
    engine = OlegPainter()
    engine.status_callback = lambda *_a, **_k: None
    engine.color_picking_method = "manual_palette"
    engine.manual_palette_coords = [
        {"x": 10 * (i + 1), "y": 20, "rgb": list(rgb)} for i, rgb in enumerate(rgbs)
    ]
    engine._manual_palette_cache = None
    return engine


def test_sanitized_entries_carry_both_match_vectors():
    engine = _make_engine_with_palette([(255, 0, 0), (0, 0, 255)])
    cache = engine._get_manual_palette_cache()
    for entry in cache["entries"]:
        assert entry["lab"].shape == (3,)
        assert entry["okl"].shape == (3,)
        expected = rgb_to_oklab_numpy(np.asarray([entry["rgb"]], dtype=np.float64))[0] * 100.0
        assert np.allclose(entry["okl"], expected, atol=1e-3)


def test_manual_match_space_config_roundtrip_and_normalizer():
    engine = OlegPainter()
    assert engine.manual_match_space == "oklab"
    assert engine._normalize_manual_match_space("LAB") == "lab"
    assert engine._normalize_manual_match_space("garbage") == "oklab"
    assert engine._normalize_manual_match_space(None) == "oklab"

    cfg = engine.get_config()
    assert cfg["manual_match_space"] == "oklab"

    engine.manual_match_space = "lab"
    cfg = engine.get_config()
    assert cfg["manual_match_space"] == "lab"

    fresh = OlegPainter()
    fresh.status_callback = lambda *_a, **_k: None
    fresh.load_config(cfg)
    assert fresh.manual_match_space == "lab"


def _nearest_idx(target_rgb, palette_rgbs, space):
    convert = rgb_to_oklab_numpy if space == "oklab" else rgb_to_cielab_numpy
    t = convert(np.asarray([target_rgb], dtype=np.float64))[0]
    pal = convert(np.asarray(palette_rgbs, dtype=np.float64))
    return int(np.argmin(np.sum((pal - t) ** 2, axis=1)))


def _find_disagreeing_case(rng):
    """Search for a target + palette pair where LAB and OKLab pick different
    entries, so the test below actually proves the space switch matters."""
    for _ in range(3000):
        target = tuple(int(v) for v in rng.integers(0, 256, size=3))
        palette = [tuple(int(v) for v in rng.integers(0, 256, size=3)) for _ in range(4)]
        if _nearest_idx(target, palette, "lab") != _nearest_idx(target, palette, "oklab"):
            return target, palette
    raise AssertionError("no LAB/OKLab disagreement found in 3000 random draws")


def test_pick_color_respects_match_space():
    rng = np.random.default_rng(7)
    target, palette = _find_disagreeing_case(rng)
    hex_str = "#{:02X}{:02X}{:02X}".format(*target)

    for space in ("oklab", "lab"):
        engine = _make_engine_with_palette(palette)
        engine.manual_match_space = space
        # This test verifies the Euclidean match SPACE drives the pick; CIEDE2000
        # (now the default) deliberately ignores match_space (it always matches in
        # CIELAB by true perceived difference), so disable it here.
        engine.palette_match_metric = "euclidean"
        engine._click_abs = lambda *_a, **_k: None
        engine._automation_cancelled = lambda: False
        engine._sleep_with_abort = lambda *_a, **_k: True

        clicked = []
        engine._click_abs = lambda x, y: clicked.append((x, y))
        engine._pick_color_manual_palette(hex_str)

        expected_idx = _nearest_idx(target, palette, space)
        expected_xy = (10 * (expected_idx + 1), 20)
        assert clicked, f"no click registered for space={space}"
        assert clicked[0] == expected_xy, (
            f"space={space}: clicked {clicked[0]}, expected {expected_xy} "
            f"(target={target}, palette={palette})"
        )
