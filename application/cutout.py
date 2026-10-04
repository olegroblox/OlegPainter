"""Cut a framed object out of its background without AI (CUTOUT-001).

For screenshots where the user framed a character or an object: GrabCut
(OpenCV) learns the background from a thin band along the frame and the object
from the rest. Holes enclosed by the object are filled unless they look like
the background (sky between antlers); small fragments and whatever lies under
the object's feet (a platform) are dropped. The result
is the same picture with a transparent background — transparency is always
background for the engine, so nothing else needs to know about the cutout.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

WORK_SIDE = 400          # GrabCut runs on a copy this size: ~0.5 s, enough for the outline
BAND = 0.03              # share of the frame treated as sure background
MIN_PART = 0.02          # object parts smaller than this share of the largest one are dropped
HOLE_BACKGROUND_DE = 12  # a small hole this close in colour to the surroundings stays transparent
LARGE_HOLE = 0.03        # holes this share of the object (a face) are always the object


def _lab(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(rgb.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)


def _palette(lab: np.ndarray, k: int = 8) -> np.ndarray:
    """Main colours of a pixel set (k-means centres in Lab)."""
    import cv2
    if len(lab) > 20000:
        lab = lab[np.random.default_rng(0).choice(len(lab), 20000, replace=False)]
    if len(lab) <= k:
        return lab
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    cv2.setRNGSeed(0)
    _, _, centres = cv2.kmeans(np.ascontiguousarray(lab), k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    return centres


def object_mask(rgb: np.ndarray) -> np.ndarray:
    """Boolean object mask (True = keep) for an RGB array."""
    import cv2
    from scipy import ndimage
    h, w = rgb.shape[:2]
    if min(h, w) < 16:
        raise ValueError("Картинка слишком маленькая, чтобы отделить объект от фона.")
    scale = min(1.0, WORK_SIDE / max(h, w))
    small = cv2.resize(rgb, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    margin = max(2, int(BAND * min(sh, sw)))
    mask = np.zeros((sh, sw), np.uint8)
    background_model = np.zeros((1, 65), np.float64)
    object_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), mask, (margin, margin, sw - 2 * margin, sh - 2 * margin),
                background_model, object_model, 5, cv2.GC_INIT_WITH_RECT)
    keep = (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD)

    # A part lying wholly below the main one is what it stands on (a platform,
    # a floor decal, a shadow); parts beside or above it (a halo, a pet) stay.
    labels, count = ndimage.label(keep)
    if count:
        sizes = ndimage.sum(keep, labels, range(1, count + 1))
        boxes = ndimage.find_objects(labels)
        main = int(np.argmax(sizes))
        main_bottom = boxes[main][0].stop
        keep = np.isin(labels, [i + 1 for i, size in enumerate(sizes)
                                if size >= MIN_PART * sizes.max() and boxes[i][0].start < main_bottom])

    # Enclosed holes: a white face inside the head is the object, the sky seen
    # between two antlers is not. Large holes are parts of the object; a small one
    # stays transparent when it has the colour found right outside the silhouette.
    filled = ndimage.binary_fill_holes(keep)
    holes, hole_count = ndimage.label(filled & ~keep)
    if hole_count:
        outside = ndimage.binary_dilation(filled, iterations=6) & ~filled
        surroundings = _palette(_lab(small[outside])) if outside.any() else np.zeros((0, 3), np.float32)
        small_lab = _lab(small).reshape(sh, sw, 3)
        large = LARGE_HOLE * keep.sum()
        for index in range(1, hole_count + 1):
            region = holes == index
            if region.sum() >= large:
                keep |= region
                continue
            mean = small_lab[region].mean(axis=0)
            nearest = float(np.min(np.linalg.norm(surroundings - mean, axis=1))) if len(surroundings) else np.inf
            if nearest >= HOLE_BACKGROUND_DE:
                keep |= region

    full = cv2.resize(keep.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_LINEAR) > 127
    share = float(full.mean())
    if share < 0.02 or share > 0.98:
        raise ValueError("Не удалось отделить объект от фона: обведите его рамкой поплотнее.")
    return full


def cut_out(image: Image.Image) -> Image.Image:
    """The picture with a transparent background (RGBA); already transparent pixels stay so."""
    rgba = image.convert("RGBA")
    array = np.asarray(rgba)
    keep = object_mask(np.ascontiguousarray(array[..., :3]))
    alpha = np.where(keep, array[..., 3], 0).astype(np.uint8)
    return Image.fromarray(np.dstack([array[..., :3], alpha]), "RGBA")
