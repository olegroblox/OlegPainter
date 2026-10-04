from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger("olegpainter.engine.prep_pipeline")
from sklearn.cluster import KMeans, MiniBatchKMeans

from .color_spaces import (
    rgb_to_cielab_numpy,
    rgb_to_oklab_numpy,
    cielab_to_rgb_numpy,
    oklab_to_rgb_numpy,
)

try:  # optional: JIT for the sequential error-diffusion loop (10-100x); absence is fine
    from numba import njit as _numba_njit
except Exception:  # pragma: no cover - environment without numba
    _numba_njit = None

_FS_JIT = None  # compiled lazily on first dither so import/startup never pays for it


def _floyd_steinberg_kernel(buf, cen, valid, labels):
    """Sequential Floyd-Steinberg core. Must stay arithmetically IDENTICAL to the
    pure-NumPy fallback in floyd_steinberg_labels (same op order, same 7/16ths)
    so enabling numba never changes the picture - only the speed. nopython-safe:
    plain loops, no allocations."""
    h, w = buf.shape[0], buf.shape[1]
    k = cen.shape[0]
    for y in range(h):
        for x in range(w):
            if not valid[y, x]:
                continue
            b0 = buf[y, x, 0]
            b1 = buf[y, x, 1]
            b2 = buf[y, x, 2]
            best = 0
            best_d = np.inf
            for i in range(k):
                d0 = cen[i, 0] - b0
                d1 = cen[i, 1] - b1
                d2 = cen[i, 2] - b2
                d = d0 * d0 + d1 * d1 + d2 * d2
                if d < best_d:
                    best_d = d
                    best = i
            labels[y, x] = best
            e0 = b0 - cen[best, 0]
            e1 = b1 - cen[best, 1]
            e2 = b2 - cen[best, 2]
            if x + 1 < w and valid[y, x + 1]:
                buf[y, x + 1, 0] += e0 * (7.0 / 16.0)
                buf[y, x + 1, 1] += e1 * (7.0 / 16.0)
                buf[y, x + 1, 2] += e2 * (7.0 / 16.0)
            if y + 1 < h:
                if x - 1 >= 0 and valid[y + 1, x - 1]:
                    buf[y + 1, x - 1, 0] += e0 * (3.0 / 16.0)
                    buf[y + 1, x - 1, 1] += e1 * (3.0 / 16.0)
                    buf[y + 1, x - 1, 2] += e2 * (3.0 / 16.0)
                if valid[y + 1, x]:
                    buf[y + 1, x, 0] += e0 * (5.0 / 16.0)
                    buf[y + 1, x, 1] += e1 * (5.0 / 16.0)
                    buf[y + 1, x, 2] += e2 * (5.0 / 16.0)
                if x + 1 < w and valid[y + 1, x + 1]:
                    buf[y + 1, x + 1, 0] += e0 * (1.0 / 16.0)
                    buf[y + 1, x + 1, 1] += e1 * (1.0 / 16.0)
                    buf[y + 1, x + 1, 2] += e2 * (1.0 / 16.0)


def _get_fs_jit():
    global _FS_JIT
    if _FS_JIT is None and _numba_njit is not None:
        try:
            _FS_JIT = _numba_njit(cache=True)(_floyd_steinberg_kernel)
        except Exception:
            _FS_JIT = False  # broken numba install: remember and never retry
    return _FS_JIT or None


def alpha_aware_normalize(
    image_rgba: np.ndarray,
    *,
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    alpha_threshold: int = 10,
) -> tuple[np.ndarray, np.ndarray]:
    rgba = np.asarray(image_rgba)
    if rgba.ndim != 3 or rgba.shape[-1] not in (3, 4):
        raise ValueError("Expected HxWx3 or HxWx4 image")
    if rgba.shape[-1] == 3:
        rgb = rgba.astype(np.uint8, copy=True)
        alpha_mask = np.ones(rgb.shape[:2], dtype=bool)
        return rgb, alpha_mask

    rgb = rgba[..., :3].astype(np.uint8, copy=True)
    alpha = rgba[..., 3].astype(np.uint8, copy=False)
    alpha_mask = alpha > int(alpha_threshold)
    if np.any(~alpha_mask):
        rgb = rgb.copy()
        rgb[~alpha_mask] = np.asarray(background_rgb, dtype=np.uint8)
    return rgb, alpha_mask


def bilateral_preprocess(
    image_rgb: np.ndarray,
    *,
    d: int = 7,
    sigma_color: float = 45.0,
    sigma_space: float = 45.0,
) -> np.ndarray:
    rgb = np.asarray(image_rgb, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("Expected HxWx3 image")
    return cv2.bilateralFilter(rgb, d=d, sigmaColor=sigma_color, sigmaSpace=sigma_space)


def majority_vote_brush_grid(label_map: np.ndarray, brush_size: int, background: int = -1) -> np.ndarray:
    labels = np.asarray(label_map)
    if labels.ndim != 2:
        raise ValueError("label_map must be 2D")
    if brush_size <= 1:
        return labels.copy()
    h, w = labels.shape
    out_h = (h + brush_size - 1) // brush_size
    out_w = (w + brush_size - 1) // brush_size
    result = np.empty((out_h, out_w), dtype=labels.dtype)
    for oy in range(out_h):
        y0 = oy * brush_size
        y1 = min(h, y0 + brush_size)
        for ox in range(out_w):
            x0 = ox * brush_size
            x1 = min(w, x0 + brush_size)
            block = labels[y0:y1, x0:x1].ravel()
            unique, counts = np.unique(block, return_counts=True)
            # np.argmax picks the first max and np.unique is ascending, so ties
            # would resolve to the smallest label — the background sentinel
            # (background < every real cluster id), eroding object edges by one
            # brush cell on every tied boundary. Prefer a non-background winner.
            max_count = counts.max()
            winners = unique[counts == max_count]
            non_bg = winners[winners != background]
            result[oy, ox] = non_bg[0] if non_bg.size else winners[0]
    return result


def floyd_steinberg_labels(
    space_img: np.ndarray,
    centers: np.ndarray,
    valid_mask: np.ndarray,
) -> np.ndarray:
    """Assign each valid cell to the nearest palette center using Floyd–Steinberg
    error diffusion, IN the given (perceptual) color space.

    space_img : HxWx3 float, image in the cluster color space (lab/oklab/rgb).
    centers   : kx3 float, palette centers in the SAME space.
    valid_mask: HxW bool, True where a label should be assigned (drawable).

    Returns HxW int32 labels: cluster index where valid, -1 elsewhere. Error is
    diffused only to valid forward neighbours, so background never bleeds in.
    Sequential by nature (error propagation) — runs at the cluster-map / brush
    resolution, which keeps it cheap.
    """
    h, w = space_img.shape[:2]
    labels = np.full((h, w), -1, dtype=np.int32)
    cen = np.asarray(centers, dtype=np.float64)
    if cen.ndim != 2 or cen.shape[0] == 0:
        return labels
    buf = np.array(space_img, dtype=np.float64, copy=True)
    valid = np.asarray(valid_mask, dtype=bool)
    jit = _get_fs_jit()
    if jit is not None:
        try:
            jit(np.ascontiguousarray(buf), np.ascontiguousarray(cen), np.ascontiguousarray(valid), labels)
            return labels
        except Exception:
            labels.fill(-1)  # fall through to the pure-Python path untouched
    for y in range(h):
        row_valid = valid[y]
        for x in range(w):
            if not row_valid[x]:
                continue
            old = buf[y, x]
            idx = int(np.argmin(np.sum((cen - old) ** 2, axis=1)))
            labels[y, x] = idx
            err = old - cen[idx]
            if x + 1 < w and valid[y, x + 1]:
                buf[y, x + 1] += err * (7.0 / 16.0)
            if y + 1 < h:
                if x - 1 >= 0 and valid[y + 1, x - 1]:
                    buf[y + 1, x - 1] += err * (3.0 / 16.0)
                if valid[y + 1, x]:
                    buf[y + 1, x] += err * (5.0 / 16.0)
                if x + 1 < w and valid[y + 1, x + 1]:
                    buf[y + 1, x + 1] += err * (1.0 / 16.0)
    return labels


def _to_cluster_space(image_rgb: np.ndarray, color_space: str) -> np.ndarray:
    color_space = color_space.lower()
    if color_space == "oklab":
        return rgb_to_oklab_numpy(image_rgb)
    if color_space == "cielab" or color_space == "lab":
        return rgb_to_cielab_numpy(image_rgb)
    return np.asarray(image_rgb, dtype=np.float64)


def _from_cluster_space(centers: np.ndarray, color_space: str) -> np.ndarray:
    color_space = color_space.lower()
    if color_space == "oklab":
        return oklab_to_rgb_numpy(centers)
    if color_space == "cielab" or color_space == "lab":
        return cielab_to_rgb_numpy(centers)
    return np.clip(np.rint(np.asarray(centers)), 0, 255).astype(np.uint8)


def _chroma_sample_weights(fit_data: np.ndarray, color_space: str, strength: float) -> np.ndarray | None:
    """Per-sample k-means weight = 1 + strength·(chroma / p95(chroma)). Pulls
    centroids toward SATURATED pixels so rare vivid accents (eyes, highlights,
    neon marks) survive the MSE-driven clustering that would otherwise drop a
    few hundred high-chroma pixels into a dull background centroid. None for
    non-perceptual spaces (no meaningful chroma axis)."""
    cs = str(color_space or "").lower()
    if cs not in {"oklab", "cielab", "lab"} or strength <= 0.0:
        return None
    ab = np.asarray(fit_data, dtype=np.float64)[:, 1:3]      # a, b channels
    chroma = np.sqrt((ab * ab).sum(axis=1))
    scale = float(np.percentile(chroma, 95.0))
    if not np.isfinite(scale) or scale <= 1e-9:
        return None
    return (1.0 + float(strength) * np.clip(chroma / scale, 0.0, 1.0)).astype(np.float64)


def _imagequant_palette(rgb: np.ndarray, k: int):
    """Gold-standard pngquant/libimagequant palette (median-cut + Voronoi
    refinement + adaptive weighting), DITHER OFF. Tries PIL's libimagequant
    backend, then the `quantizr` wheel. Returns palette_rgb (m,3 uint8) or None
    when neither backend is available / errors — the caller falls back to
    k-means. Best-effort: any failure is swallowed into the fallback."""
    h, w = rgb.shape[:2]
    # 1) Pillow built with libimagequant (Image.quantize LIBIMAGEQUANT).
    try:
        from PIL import Image as _Image, features as _features
        if _features.check_feature("libimagequant"):
            im = _Image.fromarray(np.ascontiguousarray(rgb), "RGB")
            pq = im.quantize(colors=int(max(1, min(256, k))),
                             method=_Image.Quantize.LIBIMAGEQUANT,
                             dither=_Image.Dither.NONE)
            pal = pq.getpalette() or []
            used = int(np.asarray(pq).max()) + 1 if pq.size[0] * pq.size[1] else 0
            arr = np.asarray(pal, dtype=np.uint8).reshape(-1, 3)
            if used > 0 and arr.shape[0] >= used:
                return arr[:used]
    except Exception:
        log.debug("PIL libimagequant unavailable", exc_info=True)
    # 2) The standalone `quantizr` wheel (pip install quantizr).
    try:
        import quantizr  # type: ignore
        rgba = np.dstack([rgb, np.full((h, w), 255, np.uint8)]).tobytes()
        img = quantizr.Image.new(rgba, w, h)
        res = quantizr.quantize(img, int(max(1, min(256, k))))
        pal = np.asarray(res.get_palette(), dtype=np.uint8).reshape(-1, 4)[:, :3]
        if pal.shape[0] > 0:
            return pal
    except Exception:
        log.debug("quantizr unavailable", exc_info=True)
    return None


def perceptual_kmeans_quantize(
    image_rgb: np.ndarray,
    *,
    k: int,
    color_space: str = "lab",
    mode: str = "kmeans",
    sample_cap: int | None = None,
    random_state: int = 42,
    preserve_accents: bool = False,
    accent_strength: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rgb = np.asarray(image_rgb, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("Expected HxWx3 image")
    h, w = rgb.shape[:2]
    mode = str(mode or "kmeans").lower()

    if mode == "imagequant":
        # Gold-standard palette CHOICE; centres are converted into the active
        # cluster space so downstream (merge / dither / assignment) is unchanged.
        pal_rgb = _imagequant_palette(rgb, int(k))
        if pal_rgb is not None and pal_rgb.shape[0] >= 1:
            centers = _to_cluster_space(pal_rgb.reshape(-1, 1, 3), color_space).reshape(-1, 3).astype(np.float64)
            flat = _to_cluster_space(rgb, color_space).reshape(-1, 3)
            labels = _assign_nearest_center(flat, centers).reshape(h, w)
            return labels, centers, _from_cluster_space(centers, color_space)
        log.info("imagequant backend unavailable; falling back to k-means")
        mode = "kmeans"

    work = _to_cluster_space(rgb, color_space)
    flat = work.reshape(-1, 3)

    fit_data = flat
    if sample_cap is not None and len(flat) > sample_cap:
        rng = np.random.default_rng(random_state)
        fit_data = flat[rng.choice(len(flat), size=sample_cap, replace=False)]

    sample_weight = None
    if preserve_accents:
        sample_weight = _chroma_sample_weights(fit_data, color_space, accent_strength)

    mode = mode.lower()
    if mode == "minibatch":
        model = MiniBatchKMeans(n_clusters=int(k), random_state=random_state, batch_size=min(4096, len(fit_data)))
    else:
        model = KMeans(n_clusters=int(k), random_state=random_state, init="k-means++", n_init="auto")
    model.fit(fit_data, sample_weight=sample_weight)
    labels = _assign_nearest_center(flat, np.asarray(model.cluster_centers_), model).reshape(h, w)
    centers = np.asarray(model.cluster_centers_, dtype=np.float64)
    palette_rgb = _from_cluster_space(centers, color_space)
    return labels, centers, palette_rgb


def _assign_nearest_center(flat: np.ndarray, centers: np.ndarray, model=None) -> np.ndarray:
    """Nearest-center assignment for the full-resolution pixel array.

    cKDTree(workers=-1) is ~40x faster than sklearn's predict on the typical
    900k x 25 case (36 ms vs 1450 ms, measured) and returns IDENTICAL labels
    (argmin euclidean; k-means float centers make exact ties practically
    impossible). Falls back to model.predict / brute force when scipy is
    unavailable or the query fails for any reason.
    """
    try:
        from scipy.spatial import cKDTree
        _, labels = cKDTree(centers).query(flat, k=1, workers=-1)
        return np.asarray(labels, dtype=np.int64)
    except Exception:
        if model is not None:
            return model.predict(flat)
        d = ((flat[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        return np.argmin(d, axis=1)


def merge_similar_clusters(
    labels: np.ndarray,
    centers: np.ndarray,
    threshold: float,
    color_space: str,
):
    """Collapse k-means clusters whose centers are perceptually near-identical, so
    a user-set color count acts as a MAXIMUM rather than a forced count (an image
    that is really ~2 colors won't be split into 10 near-duplicate shades).

    Greedy agglomerative merge in the cluster color space (centers are already in
    it — OKLab/CIELAB give Euclidean ~ perceptual distance): process clusters
    dominant-first (by pixel count) so big regions become the survivors; any
    cluster within `threshold` of an existing survivor is merged into it. Returns
    (new_labels, new_centers, new_palette_rgb) renumbered to a contiguous range.

    labels: flat int array of per-pixel cluster ids (0..k-1).
    centers: (k,3) in the cluster space. threshold: distance in that space.
    """
    centers = np.asarray(centers, dtype=np.float64)
    k = centers.shape[0]
    if k <= 1 or threshold is None or float(threshold) <= 0.0:
        return labels, centers, _from_cluster_space(centers, color_space)
    counts = np.bincount(np.asarray(labels).ravel(), minlength=k).astype(np.float64)
    survivors: list[int] = []
    remap = np.full(k, -1, dtype=np.int64)
    for ci in np.argsort(-counts):           # dominant clusters first -> survivors
        ci = int(ci)
        chosen = -1
        for s in survivors:
            if float(np.linalg.norm(centers[ci] - centers[s])) < float(threshold):
                chosen = s
                break
        if chosen < 0:
            survivors.append(ci)
            remap[ci] = ci
        else:
            remap[ci] = chosen
    new_index = {s: i for i, s in enumerate(survivors)}
    old_to_new = np.array([new_index[remap[old]] for old in range(k)], dtype=np.int32)
    new_labels = old_to_new[np.asarray(labels)]
    m = len(survivors)
    acc = np.zeros((m, 3), dtype=np.float64)
    wsum = np.zeros(m, dtype=np.float64)
    for old in range(k):                     # count-weighted mean of merged members
        ni = int(old_to_new[old])
        acc[ni] += centers[old] * counts[old]
        wsum[ni] += counts[old]
    wsum = np.maximum(wsum, 1.0)
    new_centers = acc / wsum[:, None]
    return new_labels, new_centers, _from_cluster_space(new_centers, color_space)


def build_perceptual_cluster_map(
    image_rgba: np.ndarray,
    *,
    brush_size: int,
    k_clusters: int,
    color_space: str = "lab",
    quantization_mode: str = "kmeans",
    fit_sample_cap: int | None = None,
    alpha_threshold: int = 10,
    background_mask: np.ndarray | None = None,
    background_cluster_id: int = -1,
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    bilateral_d: int = 7,
    bilateral_sigma_color: float = 45.0,
    bilateral_sigma_space: float = 45.0,
    random_state: int = 42,
    dither: bool = False,
    merge_threshold: float = 0.0,
    preserve_accents: bool = False,
    accent_strength: float = 3.0,
) -> dict[str, np.ndarray]:
    rgba = np.asarray(image_rgba)
    if rgba.ndim != 3 or rgba.shape[-1] not in (3, 4):
        raise ValueError("Expected HxWx3 or HxWx4 image")

    normalized_rgb, alpha_keep_mask = alpha_aware_normalize(
        rgba,
        background_rgb=background_rgb,
        alpha_threshold=alpha_threshold,
    )
    filtered_rgb = bilateral_preprocess(
        normalized_rgb,
        d=bilateral_d,
        sigma_color=bilateral_sigma_color,
        sigma_space=bilateral_sigma_space,
    )

    active_mask = alpha_keep_mask.astype(bool, copy=True)
    if background_mask is not None:
        bg_mask = np.asarray(background_mask).astype(bool, copy=False)
        if bg_mask.shape != active_mask.shape:
            bg_mask = cv2.resize(
                bg_mask.astype(np.uint8),
                (active_mask.shape[1], active_mask.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
        active_mask &= ~bg_mask

    full_label_map = np.full(active_mask.shape, background_cluster_id, dtype=np.int32)
    if not np.any(active_mask):
        cluster_map = majority_vote_brush_grid(full_label_map, max(1, int(brush_size)), background=background_cluster_id)
        return {
            "cluster_map": cluster_map.astype(np.int32, copy=False),
            "full_label_map": full_label_map,
            "palette_rgb": np.zeros((0, 3), dtype=np.uint8),
            "centers": np.zeros((0, 3), dtype=np.float64),
            "filtered_rgb": filtered_rgb,
            "active_mask": active_mask,
        }

    pixels = filtered_rgb[active_mask]
    unique_pixels = np.unique(pixels.reshape(-1, 3), axis=0)
    k_actual = min(max(1, int(k_clusters)), len(unique_pixels))
    labels_flat = np.zeros((pixels.shape[0],), dtype=np.int32)
    centers = np.zeros((0, 3), dtype=np.float64)
    palette_rgb = np.zeros((0, 3), dtype=np.uint8)
    if k_actual > 0:
        pixels_image = pixels.reshape((-1, 1, 3))
        quant_labels, centers, palette_rgb = perceptual_kmeans_quantize(
            pixels_image,
            k=k_actual,
            color_space=color_space,
            mode=quantization_mode,
            sample_cap=fit_sample_cap,
            random_state=random_state,
            preserve_accents=preserve_accents,
            accent_strength=accent_strength,
        )
        labels_flat = quant_labels.reshape(-1).astype(np.int32, copy=False)
        if merge_threshold and float(merge_threshold) > 0.0 and centers.shape[0] > 1:
            # Collapse perceptually near-identical clusters so the requested color
            # count behaves as a MAX (a ~2-color image is not split into 10 shades).
            labels_flat, centers, palette_rgb = merge_similar_clusters(
                labels_flat, centers, float(merge_threshold), color_space
            )
            labels_flat = np.asarray(labels_flat, dtype=np.int32)
    full_label_map[active_mask] = labels_flat
    brush = max(1, int(brush_size))
    cluster_map = majority_vote_brush_grid(full_label_map, brush, background=background_cluster_id)
    if dither and k_actual > 0 and centers.shape[0] > 0:
        # Re-derive the cluster_map by Floyd-Steinberg dithering the per-cell
        # average color against the palette (in the cluster color space), at the
        # SAME resolution majority-vote produced. Gradients reproduce with the
        # limited palette instead of banding. Background cells stay background.
        out_h, out_w = cluster_map.shape[:2]
        cell_rgb = cv2.resize(filtered_rgb, (out_w, out_h), interpolation=cv2.INTER_AREA)
        cell_valid = cv2.resize(
            active_mask.astype(np.uint8), (out_w, out_h), interpolation=cv2.INTER_NEAREST
        ).astype(bool)
        cell_space = _to_cluster_space(cell_rgb, color_space)
        dith_labels = floyd_steinberg_labels(cell_space, centers, cell_valid)
        cluster_map = np.where(dith_labels >= 0, dith_labels, background_cluster_id).astype(np.int32)
    return {
        "cluster_map": cluster_map.astype(np.int32, copy=False),
        "full_label_map": full_label_map,
        "palette_rgb": palette_rgb,
        "centers": centers,
        "filtered_rgb": filtered_rgb,
        "active_mask": active_mask,
    }


__all__ = [
    "alpha_aware_normalize",
    "bilateral_preprocess",
    "majority_vote_brush_grid",
    "floyd_steinberg_labels",
    "merge_similar_clusters",
    "perceptual_kmeans_quantize",
    "build_perceptual_cluster_map",
]
