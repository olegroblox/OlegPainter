# -*- coding: utf-8 -*-
"""Phase-5 / WS8-2: semantic color order («сначала фон») — the Intelli-Paint
plane heuristic over cluster features, its primary-key integration into the
palette sort, and the off = bit-identical guarantee."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter  # noqa: E402


def _engine():
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    return e


def _frame_circle_specks_map(h=40, w=40):
    """Frame-touching cluster 0 (backdrop), central blob cluster 1 (object),
    scattered tiny cluster 2 (details)."""
    cm = np.zeros((h, w), dtype=int)          # cluster 0 everywhere = backdrop
    yy, xx = np.mgrid[0:h, 0:w]
    blob = ((xx - w // 2) ** 2 + (yy - h // 2) ** 2) <= 100
    cm[blob] = 1                               # central object
    for r, c in ((6, 30), (30, 6), (33, 33)):
        cm[r, c] = 2                           # tiny details
    return cm


def test_planes_classify_backdrop_vs_object():
    e = _engine()
    e.cluster_map = _frame_circle_specks_map()
    e.semantic_order_mode = "bg_first"
    planes = e._semantic_cluster_planes()
    assert planes is not None
    assert planes[0] == 0          # frame-touching backdrop
    assert planes[1] == 1          # central object
    assert planes[2] == 1          # specks belong to the object plane (2-zone model)


def test_planes_degenerate_cases_return_none():
    e = _engine()
    e.cluster_map = None
    assert e._semantic_cluster_planes() is None
    e.cluster_map = np.zeros((10, 10), dtype=int)      # single cluster
    assert e._semantic_cluster_planes() is None
    e.cluster_map = np.full((10, 10), -1, dtype=int)   # background only
    assert e._semantic_cluster_planes() is None


def test_palette_sort_puts_backdrop_first_and_off_is_legacy():
    e = _engine()
    e.cluster_map = _frame_circle_specks_map()
    # color_palette tuples: (hex, cluster_id, count, rgb). Pick lightnesses so
    # the LEGACY tone order (light first) would put the OBJECT before the
    # backdrop — the semantic mode must override that grouping.
    e.color_palette = [
        ("#202020", 0, 1200, (32, 32, 32)),    # dark backdrop
        ("#f0f0f0", 1, 300, (240, 240, 240)),  # light central object
        ("#ff0000", 2, 3, (255, 0, 0)),        # detail specks
    ]
    e.semantic_order_mode = "off"
    e._apply_palette_sorting()
    legacy_order = [entry[1] for entry in e.color_palette]
    assert legacy_order[0] == 1                # light object first in tone order

    e.semantic_order_mode = "bg_first"
    e._apply_palette_sorting()
    semantic_order = [entry[1] for entry in e.color_palette]
    assert semantic_order[0] == 0              # backdrop promoted to the front
    # object plane (object + specks) follows, tone order inside the plane
    assert set(semantic_order[1:]) == {1, 2}

    # the user's choice: object plane FIRST, backdrop LAST
    e.semantic_order_mode = "objects_first"
    e._apply_palette_sorting()
    objects_order = [entry[1] for entry in e.color_palette]
    assert objects_order[-1] == 0              # backdrop last
    assert set(objects_order[:2]) == {1, 2}    # object + its details first

    # back off -> bit-identical legacy order again
    e.semantic_order_mode = "off"
    e._apply_palette_sorting()
    assert [entry[1] for entry in e.color_palette] == legacy_order


def test_sort_survives_broken_planes():
    e = _engine()
    e.cluster_map = "garbage"                  # planes computation must not raise
    e.color_palette = [("#111111", 0, 10, (17, 17, 17)),
                       ("#eeeeee", 1, 10, (238, 238, 238))]
    e.semantic_order_mode = "bg_first"
    e._apply_palette_sorting()                 # no exception, legacy order kept
    assert len(e.color_palette) == 2


def test_centrality_guard_keeps_cropped_subject_off_backdrop():
    """A LARGE central cluster cropped by the frame (classic portrait) must not
    be classified as backdrop just because it touches the border band."""
    h, w = 40, 40
    cm = np.zeros((h, w), dtype=int)            # cluster 0 = true backdrop
    cm[12:40, 10:30] = 1                        # cluster 1 = big central subject touching bottom
    cm[3, 35] = 2                               # detail speck
    e = _engine()
    e.cluster_map = cm
    e.semantic_order_mode = "bg_first"
    planes = e._semantic_cluster_planes()
    assert planes is not None
    assert planes[0] == 0
    assert planes[1] >= 1, "cropped central subject misread as backdrop"


def test_saliency_planes_override_heuristic():
    """When the AI saliency map is available, planes follow the net: low
    saliency = backdrop even if the geometry said otherwise."""
    e = _engine()
    cm = _frame_circle_specks_map()
    e.cluster_map = cm
    e.semantic_order_mode = "bg_first"
    sal = np.zeros(cm.shape, dtype=np.float32)
    sal[cm == 1] = 0.95                          # the net is confident: circle = object
    sal[cm == 2] = 0.80                          # specks salient too
    e._semantic_saliency_cache = (id(cm), sal)   # injected as if computed
    planes = e._semantic_cluster_planes()
    assert planes == {0: 0, 1: 1, 2: 1}          # two zones: backdrop / object


def test_saliency_all_foreground_falls_back_to_heuristic():
    """When the net sees no background at all (close-up), the two-zone AI
    split is degenerate -> the geometric heuristic takes over."""
    e = _engine()
    cm = _frame_circle_specks_map()
    e.cluster_map = cm
    e.semantic_order_mode = "bg_first"
    e._semantic_saliency_cache = (id(cm), np.full(cm.shape, 0.9, dtype=np.float32))
    planes = e._semantic_cluster_planes()
    assert planes is not None                    # heuristic still classifies
    assert planes[0] == 0                        # frame cluster = backdrop (geometry)
    assert planes[1] == 1


@pytest.mark.skipif(
    not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "models", "bg", "u2netp.onnx")),
    reason="u2netp model not installed")
def test_real_u2netp_inference_produces_aligned_saliency():
    from types import SimpleNamespace
    e = _engine()
    from engine.ai import vision
    from engine.ai.models import ModelStore
    from engine.ai.runtime import SessionPool
    store, pool = ModelStore(), SessionPool()
    e.ai_backend = SimpleNamespace(
        is_enabled=lambda: True, depth=lambda _img: None,
        foreground=lambda img: vision.foreground_mask(pool, store, "cpu", "bg.u2netp", img))
    rgb = np.full((64, 64, 3), 235, dtype=np.uint8)        # light backdrop
    rgb[16:48, 20:44] = (180, 30, 30)                      # dark red subject
    e.image_rgb = rgb
    e.cluster_map = np.where(
        (np.mgrid[0:64, 0:64][0] >= 16) & (np.mgrid[0:64, 0:64][0] < 48)
        & (np.mgrid[0:64, 0:64][1] >= 20) & (np.mgrid[0:64, 0:64][1] < 44), 1, 0)
    sal = e._semantic_saliency_map()
    assert sal is not None
    assert sal.shape == e.cluster_map.shape
    obj_mean = float(sal[e.cluster_map == 1].mean())
    bg_mean = float(sal[e.cluster_map == 0].mean())
    assert obj_mean > bg_mean, (obj_mean, bg_mean)


def test_disabled_ai_never_loads_a_saliency_model(monkeypatch):
    """AI is switched off by decision: semantic order must use the geometric
    heuristic without touching ONNX models, even with a picture present."""
    import engine.ai.vision as vision

    def forbidden(*_a, **_k):
        raise AssertionError("ONNX model loaded while AI is disabled")

    monkeypatch.setattr(vision, "foreground_mask", forbidden)
    monkeypatch.setattr(vision, "depth_map", forbidden)
    e = _engine()
    e.image_rgb = np.full((40, 40, 3), 200, dtype=np.uint8)
    e.cluster_map = _frame_circle_specks_map()
    e.semantic_order_mode = "bg_first"
    assert e._semantic_saliency_map() is None
    assert e._semantic_cluster_planes() is not None      # heuristic still works


def test_depth_cache_overrides_heuristic():
    e = _engine()
    e.cluster_map = _frame_circle_specks_map()
    e.semantic_order_mode = "bg_first"
    e._semantic_depth_planes_cache = {0: 2, 1: 0, 2: 1}   # future depth mode
    planes = e._semantic_cluster_planes()
    assert planes == {0: 1, 1: 0, 2: 1}          # collapsed to the 2-zone model


def test_threshold_slider_re_ranks_planes_without_new_inference():
    """The threshold is applied at READ time: the same cached saliency yields
    different planes when the user moves the slider (0.2 vs 0.8)."""
    e = _engine()
    cm = _frame_circle_specks_map()
    e.cluster_map = cm
    e.semantic_order_mode = "bg_first"
    sal = np.zeros(cm.shape, dtype=np.float32)
    sal[cm == 0] = 0.10                          # backdrop: clearly background
    sal[cm == 1] = 0.50                          # circle: borderline
    sal[cm == 2] = 0.90                          # specks: clearly object
    e._semantic_saliency_cache = (id(cm), sal)   # injected as if computed

    e.semantic_threshold = 0.2
    assert e._semantic_cluster_planes() == {0: 0, 1: 1, 2: 1}
    e.semantic_threshold = 0.8                   # borderline circle flips to backdrop
    assert e._semantic_cluster_planes() == {0: 0, 1: 0, 2: 1}


def test_threshold_is_clamped_and_survives_garbage():
    e = _engine()
    e.semantic_threshold = 5.0
    assert e._semantic_threshold_value() == 0.95
    e.semantic_threshold = -1
    assert e._semantic_threshold_value() == 0.05
    e.semantic_threshold = "garbage"
    assert e._semantic_threshold_value() == 0.35


def test_model_change_invalidates_saliency_cache():
    """The cache key includes the model id: switching the model must drop the
    old saliency (a bare id(cm) key — test injection — matches any model)."""
    e = _engine()
    cm = _frame_circle_specks_map()
    e.cluster_map = cm
    sal = np.full(cm.shape, 0.7, dtype=np.float32)
    e._semantic_saliency_cache = ((id(cm), "bg.u2netp"), sal)
    assert e._semantic_saliency_map() is sal     # default model: cache hit
    e.semantic_model_id = "bg.u2net"             # switch -> key mismatch
    # no image/source on the engine -> recompute yields None, not the stale map
    assert e._semantic_saliency_map() is None


def test_semantic_config_roundtrip_new_keys():
    e = _engine()
    e.semantic_threshold = 0.6
    e.semantic_model_id = "bg.u2net"
    e.semantic_region_split_enabled = True
    cfg = e.get_config()
    assert cfg["semantic_threshold"] == 0.6
    assert cfg["semantic_model_id"] == "bg.u2net"
    assert cfg["semantic_region_split_enabled"] is True
    fresh = _engine()
    fresh.load_config(cfg)
    assert fresh.semantic_threshold == 0.6
    assert fresh.semantic_model_id == "bg.u2net"
    assert fresh.semantic_region_split_enabled is True
    # clamp on load
    fresh.load_config({"semantic_threshold": 99})
    assert fresh.semantic_threshold == 0.95
    fresh.load_config({"semantic_threshold": "junk"})
    assert fresh.semantic_threshold == 0.35
    # defaults stay opt-in
    d = _engine()
    assert d.semantic_threshold == 0.35
    assert d.semantic_model_id == "bg.u2netp"
    assert d.semantic_region_split_enabled is False


def test_config_roundtrip_and_normalizer():
    e = _engine()
    assert e.semantic_order_mode == "off"
    assert e._normalize_semantic_order_mode("BG_FIRST") == "bg_first"
    assert e._normalize_semantic_order_mode("OBJECTS_FIRST") == "objects_first"
    assert e._normalize_semantic_order_mode("nonsense") == "off"
    e.semantic_order_mode = "objects_first"
    cfg = e.get_config()
    assert cfg["semantic_order_mode"] == "objects_first"
    fresh = _engine()
    fresh.load_config(cfg)
    assert fresh.semantic_order_mode == "objects_first"
