from __future__ import annotations

from collections import Counter

import cv2
import numpy as np

from .color_spaces import perceptual_distance


def distance_relabel_specks(
    label_map: np.ndarray,
    *,
    min_area: int = 6,
    background: int = -1,
    max_bridge: float | None = None,
) -> np.ndarray:
    """Aggressive despeckle (research-backed, opt-in): relabel EVERY connected
    component smaller than `min_area` to the label of the spatially NEAREST
    surviving (large) component, via a distance transform — one pass, no holes
    (every speck cell keeps a label), and large regions are never merged into
    one another (so it cannot collapse the picture to one colour). A speck whose
    nearest large region is farther than `max_bridge` (isolated noise floating in
    the removed background) is dropped to the background instead of bridging a
    stray dot across the gap. Falls back to the input unchanged when SciPy is
    unavailable.

    This is the correct alternative to `remove_small_components` (which deletes
    specks -> holes) and complements the vote-based merge (which skips speck
    chains and background-only specks)."""
    labels = np.asarray(label_map)
    if labels.ndim != 2 or min_area <= 1:
        return labels.copy()
    try:
        from scipy.ndimage import distance_transform_edt
    except Exception:
        return labels.copy()

    result = labels.copy()
    small = np.zeros(labels.shape, dtype=bool)
    for lab in np.unique(labels):
        if int(lab) == background:
            continue
        mask = (labels == lab).astype(np.uint8)
        num, cc, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
        for idx in range(1, num):
            if int(stats[idx, cv2.CC_STAT_AREA]) < min_area:
                small |= (cc == idx)
    if not small.any():
        return result

    seed_valid = (labels != background) & (~small)   # large foreground regions
    if not seed_valid.any():
        result[small] = background                    # nothing to attach to
        return result

    dist, (iy, ix) = distance_transform_edt(~seed_valid, return_indices=True)
    if max_bridge is None:
        max_bridge = max(2.0, float(min_area) ** 0.5 * 2.0)
    near = small & (dist <= max_bridge)
    far = small & (dist > max_bridge)
    result[near] = result[iy[near], ix[near]]         # attach to nearest big region
    result[far] = background                           # isolated noise -> dropped
    return result


def remove_small_components(label_map: np.ndarray, *, min_area: int = 4, background: int = 0) -> np.ndarray:
    labels = np.asarray(label_map).copy()
    if labels.ndim != 2:
        raise ValueError("label_map must be a 2D array")
    if min_area <= 1:
        return labels

    result = labels.copy()
    for label in np.unique(labels):
        if int(label) == background:
            continue
        mask = labels == label
        if not np.any(mask):
            continue
        num, cc, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
        for idx in range(1, num):
            if int(stats[idx, cv2.CC_STAT_AREA]) < min_area:
                result[cc == idx] = background
    return result


def fill_label_holes(
    label_map: np.ndarray,
    *,
    labels_to_process: np.ndarray | None = None,
    background: int = 0,
    max_hole_area: int | None = None,
) -> np.ndarray:
    """Fill enclosed holes of each label. `max_hole_area` limits filling to specks no larger
    than that area: without it any object surrounded by one colour (a disk
    on a sky) is a «hole» and the whole picture collapsed into the surrounding colour."""
    labels = np.asarray(label_map).copy()
    if labels.ndim != 2:
        raise ValueError("label_map must be a 2D array")
    result = labels.copy()
    targets = np.unique(labels) if labels_to_process is None else np.asarray(labels_to_process).ravel()
    for label in targets:
        if int(label) == background:
            continue
        mask = (result == label).astype(np.uint8)
        if not np.any(mask):
            continue
        flood = np.pad(mask, 1, mode="constant", constant_values=0)
        h, w = flood.shape
        fill = np.zeros((h + 2, w + 2), dtype=np.uint8)
        cv2.floodFill(flood, fill, (0, 0), 1)
        inner = flood[1:-1, 1:-1]
        holes = (inner == 0) & (mask == 0)
        if max_hole_area is not None and np.any(holes):
            num, cc, stats, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), connectivity=4)
            small = np.zeros(num, dtype=bool)
            small[1:] = stats[1:, cv2.CC_STAT_AREA] <= int(max_hole_area)
            holes = small[cc]
        result[holes] = label
    return result


def _normalize_color_lookup(center_colors: dict | np.ndarray | list | tuple, labels: np.ndarray, space: str) -> dict[int, np.ndarray]:
    if isinstance(center_colors, dict):
        return {int(k): np.asarray(v, dtype=np.float64) for k, v in center_colors.items()}
    arr = np.asarray(center_colors, dtype=np.float64)
    unique_labels = [int(v) for v in np.unique(labels) if int(v) >= 0]
    lookup: dict[int, np.ndarray] = {}
    for idx, label in enumerate(unique_labels):
        if idx < len(arr):
            lookup[label] = np.asarray(arr[idx], dtype=np.float64)
    return lookup


def smart_stray_merge(
    label_map: np.ndarray,
    center_colors: dict | np.ndarray | list | tuple | None,
    *,
    min_area: int = 4,
    background: int = 0,
    space: str = "lab",
    max_merge_distance: float | None = None,
) -> np.ndarray:
    """Relabel small connected components into their best (perceptually closest)
    4-neighbour. `max_merge_distance` is the Area-criterion guard: a component is
    merged ONLY when that neighbour's colour is within the cap, so DISTINCT-colour
    regions can never absorb one another — even when `min_area` is set large (it
    is shared with the aggressive cell-merge). Without it, a big `min_area` made
    every band "small" and cascaded a gradient into one colour (live report:
    «векторная + слияние мусора … всё заливается в один цвет»)."""
    labels = np.asarray(label_map).copy()
    if labels.ndim != 2:
        raise ValueError("label_map must be a 2D array")
    if min_area <= 1 or center_colors is None:
        return labels

    color_lookup = _normalize_color_lookup(center_colors, labels, space)
    result = labels.copy()
    for label in np.unique(labels):
        label = int(label)
        if label == background:
            continue
        mask = labels == label
        if not np.any(mask):
            continue
        num, cc, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=4)
        for comp_idx in range(1, num):
            area = int(stats[comp_idx, cv2.CC_STAT_AREA])
            if area >= min_area:
                continue
            comp_mask = cc == comp_idx
            ys, xs = np.where(comp_mask)
            neighbor_counts: Counter[int] = Counter()
            for y, x in zip(ys.tolist(), xs.tolist()):
                if y > 0:
                    neighbor_counts[int(labels[y - 1, x])] += 1
                if y + 1 < labels.shape[0]:
                    neighbor_counts[int(labels[y + 1, x])] += 1
                if x > 0:
                    neighbor_counts[int(labels[y, x - 1])] += 1
                if x + 1 < labels.shape[1]:
                    neighbor_counts[int(labels[y, x + 1])] += 1
            neighbor_counts.pop(label, None)
            neighbor_counts.pop(background, None)
            if not neighbor_counts:
                continue
            target = None
            target_score = None
            target_distance = None
            source_color = color_lookup.get(label)
            for neighbor_label, count in neighbor_counts.items():
                neighbor_color = color_lookup.get(neighbor_label)
                if source_color is not None and neighbor_color is not None:
                    distance = float(
                        perceptual_distance(
                            source_color,
                            neighbor_color,
                            space=space,
                            input_space=space,
                        )
                    )
                else:
                    distance = float(count)
                score = distance - count * 1e-3
                if target_score is None or score < target_score:
                    target = neighbor_label
                    target_score = score
                    target_distance = distance
            # Area-criterion guard: refuse the merge when the closest neighbour is
            # still perceptually far (distinct colour). Only meaningful when both
            # colours were known (else distance is a raw count and the cap is N/A).
            if (
                target is not None
                and max_merge_distance is not None
                and source_color is not None
                and color_lookup.get(target) is not None
                and target_distance is not None
                and target_distance > max_merge_distance
            ):
                target = None
            if target is not None:
                result[comp_mask] = target
    return result


def vectorized_cleanup(
    label_map: np.ndarray,
    *,
    min_area: int = 4,
    fill_holes: bool = True,
    center_colors: dict | np.ndarray | list | tuple | None = None,
    merge_strays: bool = False,
    remove_small: bool = True,
    background: int = 0,
    space: str = "lab",
    max_merge_distance: float | None = None,
) -> np.ndarray:
    cleaned = np.asarray(label_map).copy()
    if remove_small:
        cleaned = remove_small_components(cleaned, min_area=min_area, background=background)
    if fill_holes:
        cleaned = fill_label_holes(cleaned, background=background, max_hole_area=min_area)
    if merge_strays and center_colors is not None:
        cleaned = smart_stray_merge(cleaned, center_colors, min_area=min_area,
                                    background=background, space=space,
                                    max_merge_distance=max_merge_distance)
    return cleaned


__all__ = [
    "distance_relabel_specks",
    "remove_small_components",
    "fill_label_holes",
    "smart_stray_merge",
    "vectorized_cleanup",
    "apply_cleanup_mode",
]


def apply_cleanup_mode(
    label_map: np.ndarray,
    rgb_map: np.ndarray | None = None,
    *,
    mode: str = "off",
    min_area: int = 4,
    background: int = 0,
    space: str = "lab",
    max_merge_distance: float | None = None,
) -> tuple[np.ndarray, dict[str, int | str]]:
    labels = np.asarray(label_map)
    normalized_mode = str(mode or "off").strip().lower()
    if labels.ndim != 2:
        raise ValueError("label_map must be a 2D array")
    if normalized_mode == "off" or min_area <= 1:
        return labels.copy(), {"mode": normalized_mode, "changed_cells": 0}

    center_colors = None
    if rgb_map is not None:
        rgb_arr = np.asarray(rgb_map)
        if rgb_arr.ndim == 3 and rgb_arr.shape[:2] == labels.shape and rgb_arr.shape[-1] == 3:
            center_colors = {}
            for label in np.unique(labels):
                label_int = int(label)
                if label_int == background:
                    continue
                mask = labels == label_int
                if np.any(mask):
                    center_colors[label_int] = rgb_arr[mask].reshape(-1, 3).mean(axis=0)

    cleaned = labels.copy()
    if normalized_mode in {"vectorized", "vectorized_plus_stray_merge"}:
        cleaned = vectorized_cleanup(
            cleaned,
            min_area=min_area,
            fill_holes=True,
            center_colors=center_colors,
            merge_strays=False,
            remove_small=False,
            background=background,
            space=space,
        )
    if normalized_mode == "vectorized_plus_stray_merge":
        cleaned = vectorized_cleanup(
            cleaned,
            min_area=min_area,
            fill_holes=False,
            center_colors=center_colors,
            merge_strays=True,
            remove_small=False,
            background=background,
            space=space,
            max_merge_distance=max_merge_distance,
        )

    changed_cells = int(np.count_nonzero(cleaned != labels))
    return cleaned, {"mode": normalized_mode, "changed_cells": changed_cells}
