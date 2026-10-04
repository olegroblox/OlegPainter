"""Cleanup keeps contrasting details, merges noise (DETAILS-001).

Owner 2026-10-01: with «минимальная площадь» and despeckle the small details
evaporated — a black pupil merged into the white of the eye like noise."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

WHITE, BLACK, NEAR_WHITE = (250, 250, 250), (15, 15, 20), (232, 232, 236)


@pytest.fixture
def engine():
    from engine.olegpainter.core import OlegPainter
    eng = OlegPainter(status_callback=lambda _m: None)
    eng.fast_min_region_area = 4
    cluster_map = np.zeros((12, 12), int)          # white face
    cluster_map[3:5, 3] = 1                         # a 2-cell pupil
    cluster_map[8, 7:9] = 2                         # 2 cells of almost the same white: noise
    eng.cluster_map = cluster_map
    eng.color_palette = [("#FAFAFA", 0, 140, WHITE), ("#0F0F14", 1, 2, BLACK), ("#E8E8EC", 2, 2, NEAR_WHITE)]
    return eng


def centres():
    return {0: WHITE, 1: BLACK, 2: NEAR_WHITE}


def test_merge_small_keeps_the_pupil_and_merges_the_noise(engine):
    engine._merge_small_components_into_neighbors(centres(), -1)
    assert (engine.cluster_map == 1).sum() == 2
    assert (engine.cluster_map == 2).sum() == 0


def test_without_the_option_everything_small_merges(engine):
    engine.prep_keep_details = False
    engine._merge_small_components_into_neighbors(centres(), -1)
    assert (engine.cluster_map == 1).sum() == 0 and (engine.cluster_map == 2).sum() == 0


def test_despeckle_puts_the_pupil_back(engine):
    engine.aggressive_despeckle_enabled = True
    engine._apply_aggressive_despeckle(-1)
    assert (engine.cluster_map == 1).sum() == 2
    assert (engine.cluster_map == 2).sum() == 0
    engine.cluster_map[3:5, 3] = 1
    engine.prep_keep_details = False
    engine._apply_aggressive_despeckle(-1)
    assert (engine.cluster_map == 1).sum() == 0


def test_the_option_is_saved_with_the_settings():
    from engine.olegpainter.core import OlegPainter
    from application.settings import SETTINGS
    eng = OlegPainter(status_callback=lambda _m: None)
    assert eng.get_config()["prep_keep_details"] is True
    eng.load_config({"prep_keep_details": False})
    assert eng.prep_keep_details is False
    assert SETTINGS["prep_keep_details"].kind == "bool"


def test_a_kept_detail_is_in_the_preview_not_a_hole(tmp_path):
    # The preview used to show regions under «минимальная площадь» as holes,
    # although the drawing paints them: kept pupils looked like white dots.
    from PIL import Image, ImageDraw
    from engine.olegpainter.core import OlegPainter
    picture = Image.new("RGBA", (40, 40), (235, 205, 170, 255))          # skin
    ImageDraw.Draw(picture).rectangle((18, 18, 19, 19), (10, 10, 10, 255))  # a 2x2 pupil
    eng = OlegPainter(status_callback=lambda _m: None)
    eng.brush_size, eng.k_clusters, eng.fast_min_region_area = 1, 4, 6
    eng.draw_region = (0, 0, 40, 40)
    eng.source_pil_image, eng.IMAGE_PATH = picture, "test"
    assert eng.prepare_image_and_palette() is not False
    preview = np.asarray(eng.quantized_preview_image.convert("RGBA"))
    assert preview[18, 18, 3] == 255 and preview[18, 18, :3].max() < 60
    assert preview[5, 5, 3] == 255
