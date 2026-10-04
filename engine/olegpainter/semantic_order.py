# -*- coding: utf-8 -*-
"""semantic_order.py — «рисуй как человек»: фон раньше объектов.

Phase-5 / WS8-2 (docs/plans/WS8_painterly_semantic.md, Intelli-Paint heuristic):
palette COLORS are grouped into TWO painter's planes — backdrop and object
(details belong to the object; user decision: two zones are simpler and
sturdier than three). Plane sources, best first: u2netp saliency (AI object
probability), then cheap per-cluster geometric features (one NumPy pass):

  * border_ratio     — share of the cluster inside a frame-border band (the
                       backdrop almost always touches the picture frame);
  * touches_bg_ratio — share of the cluster 4-adjacent to the REMOVED
                       background (cluster_map == -1): what surrounds the
                       cut-out is the back plane;
  * area_ratio       — large clusters are objects, small ones are details.

Within a plane the user's tone order (light_to_dark / dark_to_light) is fully
preserved — the mode only regroups WHEN a color is painted, never what or how.
Soft degradation by design: a picture with no recognisable backdrop yields one
plane => the order stays exactly as before.

The future "depth" mode (WS8-4, Depth Anything planes) plugs into the same
`_semantic_cluster_planes` contract via `_semantic_depth_planes_cache`.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger("olegpainter.engine.semantic_order")


class SemanticOrderMixin:
    def _normalize_semantic_order_mode(self, value) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"off", "bg_first", "objects_first", "depth"}:
            return "off"
        return normalized

    @staticmethod
    def _semantic_plane_rank(plane: int, mode: str) -> int:
        """Draw priority of a plane under the chosen mode. TWO planes only
        (user decision: details are part of the object): 0=backdrop, 1=object.
        bg_first/depth paint the backdrop first, objects_first flips it."""
        plane = 0 if int(plane) == 0 else 1
        if mode == "objects_first":
            return 1 - plane
        return plane

    def _semantic_saliency_map(self):
        """Object likelihood (0..1) at cluster_map resolution from the local AI
        (AI-001): depth nearness when depth order is on, otherwise the chosen
        background model. One inference per prepared image, cached. None when
        AI is off or unusable — the caller falls back to the geometric heuristic."""
        cm = getattr(self, "cluster_map", None)
        if cm is None or not hasattr(cm, "shape"):
            return None
        model_id = self._semantic_model_id_value()
        cache = getattr(self, "_semantic_saliency_cache", None)
        if cache is not None:
            # key is (id(cm), model_id); a bare id(cm) key (tests inject the
            # saliency directly) matches regardless of the selected model
            if cache[0] == (id(cm), model_id) or cache[0] == id(cm):
                return cache[1]
        saliency = None
        backend = getattr(self, "ai_backend", None)
        if backend is None or not backend.is_enabled():
            # AI is off: never load a model behind the user's back.
            self._semantic_saliency_cache = ((id(cm), model_id), None)
            return None
        try:
            from PIL import Image as _Image

            src = None
            image_rgb = getattr(self, "image_rgb", None)
            if image_rgb is not None:
                src = _Image.fromarray(np.asarray(image_rgb, dtype=np.uint8))
            elif getattr(self, "source_pil_image", None) is not None:
                src = self.source_pil_image
            if src is not None:
                mask = None
                depth = getattr(backend, "depth", None)
                if callable(depth):
                    mask = depth(src)
                if mask is None and callable(getattr(backend, "foreground", None)):
                    mask = backend.foreground(src)
                if mask is not None:
                    saliency = cv2.resize(
                        np.asarray(mask, dtype=np.float32),
                        (int(cm.shape[1]), int(cm.shape[0])),
                        interpolation=cv2.INTER_AREA,
                    )
        except Exception:
            log.debug("semantic saliency unavailable; geometric heuristic", exc_info=True)
            saliency = None
        self._semantic_saliency_cache = ((id(cm), model_id), saliency)
        return saliency

    def _semantic_threshold_value(self) -> float:
        """User-tunable backdrop/object cut on the saliency scale (0..1),
        clamped to a sane band so neither plane can be configured away."""
        try:
            thr = float(getattr(self, "semantic_threshold", 0.35))
        except (TypeError, ValueError):
            thr = 0.35
        return min(0.95, max(0.05, thr))

    def _semantic_model_id_value(self) -> str:
        mid = str(getattr(self, "semantic_model_id", "") or "").strip()
        return mid or "bg.u2netp"

    def _semantic_cluster_planes(self) -> dict[int, int] | None:
        """Plane index per cluster_id (0 = farthest back). None = unavailable or
        degenerate (single plane) -> the caller keeps the legacy order.

        Plane sources, best first: explicit depth cache (future WS8-4) ->
        u2netp saliency (AI: object probability per cluster) -> geometric
        heuristic (frame band / background adjacency / area / centrality)."""
        depth_cache = getattr(self, "_semantic_depth_planes_cache", None)
        if isinstance(depth_cache, dict) and depth_cache:
            # collapse to the two-plane world: 0 stays backdrop, the rest = object
            return {int(k): (0 if int(v) == 0 else 1) for k, v in depth_cache.items()}
        cluster_map = getattr(self, "cluster_map", None)
        if cluster_map is None:
            return None
        cm = np.asarray(cluster_map)
        if cm.ndim != 2 or cm.size == 0:
            return None
        ids = [int(v) for v in np.unique(cm) if int(v) != -1]
        if len(ids) <= 1:
            return None

        saliency = self._semantic_saliency_map()
        if saliency is not None and saliency.shape == cm.shape:
            sal_planes = self._planes_from_saliency(cm, ids, saliency)
            if sal_planes is not None:
                self._semantic_log_once(cm, "Semantic planes source: AI (u2netp saliency).")
                return sal_planes

        h, w = cm.shape
        border = max(1, int(round(0.08 * min(h, w))))
        border_mask = np.zeros((h, w), dtype=bool)
        border_mask[:border, :] = True
        border_mask[-border:, :] = True
        border_mask[:, :border] = True
        border_mask[:, -border:] = True

        bg = cm == -1
        bg_dilated = None
        if bg.any():
            bg_dilated = cv2.dilate(bg.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0

        total = float(cm.size)
        cy = (h - 1) / 2.0
        cx = (w - 1) / 2.0
        max_center_dist = max(1.0, float(np.hypot(cy, cx)))
        feats: dict[int, tuple[float, float, float, float]] = {}
        for cid in ids:
            mask = cm == cid
            area = float(mask.sum())
            if area <= 0:
                continue
            border_ratio = float((mask & border_mask).sum()) / area
            touches = float((mask & bg_dilated).sum()) / area if bg_dilated is not None else 0.0
            ys, xs = np.nonzero(mask)
            centrality = 1.0 - float(np.hypot(ys - cy, xs - cx).mean()) / max_center_dist
            feats[cid] = (area / total, border_ratio, touches, centrality)
        if len(feats) <= 1:
            return None

        max_border = max(b for _a, b, _t, _c in feats.values())
        areas_sorted = sorted(a for a, _b, _t, _c in feats.values())
        median_area = areas_sorted[len(areas_sorted) // 2]

        planes: dict[int, int] = {}
        for cid, (area_ratio, border_ratio, touches, centrality) in feats.items():
            is_backdrop = (
                (max_border > 0.0 and border_ratio >= 0.5 * max_border and border_ratio > 0.02)
                or (touches > 0.3 and area_ratio >= median_area)
            )
            # Centrality guard (live feedback «работает плохо»): a LARGE cluster
            # whose mass sits near the image centre is the SUBJECT even when the
            # crop makes it touch the frame (classic portrait) — never backdrop.
            if is_backdrop and centrality > 0.55 and area_ratio >= median_area:
                is_backdrop = False
            # TWO planes only (user decision): everything that is not the
            # backdrop IS the object — details belong to it.
            planes[cid] = 0 if is_backdrop else 1
        if len(set(planes.values())) <= 1:
            return None  # nothing recognisable: keep the legacy order untouched
        self._semantic_log_once(cm, "Semantic planes source: geometric heuristic (no AI model).")
        return planes

    def _semantic_region_planes_mask(self):
        """Per-REGION plane map at cluster_map resolution (int8): -1 = outside /
        unavailable, 0 = backdrop, 1 = object. A color whose connected regions
        sit in BOTH zones gets split between the two drawing phases.

        With AI saliency each 4-connected region of every cluster is classified
        by its own mean saliency vs the user threshold. Without saliency the
        split honestly degrades to the per-COLOR plane (every region inherits
        its cluster's plane — same order as without the split)."""
        cluster_map = getattr(self, "cluster_map", None)
        if cluster_map is None or not hasattr(cluster_map, "shape"):
            return None
        cm = np.asarray(cluster_map)
        if cm.ndim != 2 or cm.size == 0:
            return None
        threshold = self._semantic_threshold_value()
        model_id = self._semantic_model_id_value()
        key = (id(cluster_map), round(threshold, 3), model_id)
        cache = getattr(self, "_semantic_region_mask_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]

        mask = self._build_semantic_region_planes_mask(cm, threshold)
        self._semantic_region_mask_cache = (key, mask)
        return mask

    def _build_semantic_region_planes_mask(self, cm, threshold):
        ids = [int(v) for v in np.unique(cm) if int(v) != -1]
        if len(ids) <= 1:
            return None
        mask = np.full(cm.shape, -1, dtype=np.int8)
        saliency = self._semantic_saliency_map()
        if saliency is not None and saliency.shape == cm.shape:
            for cid in ids:
                cmask = (cm == cid).astype(np.uint8)
                n_labels, labels = cv2.connectedComponents(cmask, connectivity=4)
                for lbl in range(1, n_labels):
                    rmask = labels == lbl
                    mask[rmask] = 0 if float(saliency[rmask].mean()) < threshold else 1
            self._semantic_log_once(cm, "Semantic planes source: AI (u2netp saliency).")
        else:
            planes = self._semantic_cluster_planes()
            if planes is None:
                return None
            for cid, plane in planes.items():
                mask[cm == int(cid)] = 0 if int(plane) == 0 else 1
        if len(np.unique(mask[mask >= 0])) <= 1:
            return None      # one plane only: splitting would change nothing
        return mask

    def _normalize_semantic_background_drop(self, value) -> str:
        v = str(value or "").strip().lower()
        return v if v in {"off", "backdrop", "object"} else "off"

    def _apply_semantic_background_removal(self, background_cluster_id: int) -> int:
        """Drop one semantic plane from the drawing: 'backdrop' = don't draw the
        background plane (draw the object only), 'object' = the inverse. A
        post-quantization classification, so it runs AFTER cluster_map is final.
        Returns cells dropped; opt-in ('off' = no change). Composes with the
        pixel-based background removal (corner/alpha/picked) — those cells are
        already -1 and excluded from the planes."""
        mode = self._normalize_semantic_background_drop(getattr(self, "semantic_background_drop", "off"))
        if mode == "off":
            return 0
        cluster_map = getattr(self, "cluster_map", None)
        if cluster_map is None or not hasattr(cluster_map, "shape"):
            return 0
        try:
            region_mask = self._semantic_region_planes_mask()
        except Exception:
            log.debug("semantic bg removal: region mask failed", exc_info=True)
            return 0
        if region_mask is None or region_mask.shape != cluster_map.shape:
            return 0
        target_plane = 0 if mode == "backdrop" else 1
        drop = (region_mask == target_plane) & (cluster_map != background_cluster_id)
        dropped = int(np.count_nonzero(drop))
        if dropped == 0:
            return 0
        cluster_map[drop] = background_cluster_id
        self.cluster_map = cluster_map
        self._semantic_region_mask_cache = None      # cluster_map mutated in place
        try:
            self.background_mask = self.cluster_map == background_cluster_id
        except Exception:
            log.debug("semantic bg removal: background_mask refresh failed", exc_info=True)
        try:
            palette = getattr(self, "color_palette", None)
            if palette:
                self.color_palette = [e for e in palette if bool(np.any(self.cluster_map == e[1]))]
        except Exception:
            log.debug("semantic bg removal: palette filter failed", exc_info=True)
        self._semantic_log_once(cluster_map, f"Semantic background removal: dropped {dropped} {mode}-plane cell(s).")
        return dropped

    def _semantic_split_phases(self):
        """Phase order for the two-pass draw, or None when the split is off /
        unavailable (no planes). bg_first/depth: backdrop regions first."""
        if not bool(getattr(self, "semantic_region_split_enabled", False)):
            return None
        mode = self._normalize_semantic_order_mode(getattr(self, "semantic_order_mode", "off"))
        if mode == "off":
            return None
        try:
            if self._semantic_region_planes_mask() is None:
                return None
        except Exception:
            log.debug("semantic region mask failed; split disabled", exc_info=True)
            return None
        return [1, 0] if mode == "objects_first" else [0, 1]

    # Plane zone tints for the stencil preview (RGBA, translucent):
    # backdrop = blue, object (details included) = orange.
    SEMANTIC_ZONE_COLORS = {
        0: (60, 120, 255, 110),
        1: (255, 150, 40, 120),
    }

    def build_semantic_planes_preview(self):
        """PIL RGBA image with translucent plane zones at the prepared-image
        resolution (cluster cell -> brush block), geometry-compatible with
        quantized_preview_image. Drawn over the stencil it shows WHAT the
        semantic order recognised as backdrop / objects / details BEFORE
        drawing starts (Alt+F2 -> «Планы»). None when planes are unavailable
        (no cluster_map / degenerate picture) — the toggle then has nothing
        to show. Works regardless of semantic_order_mode so the user can
        preview the classification before enabling the mode. With the region
        split enabled the zones follow the per-REGION mask — the preview shows
        exactly what the two-pass draw will do."""
        cluster_map = getattr(self, "cluster_map", None)
        if cluster_map is None or not hasattr(cluster_map, "shape"):
            return None
        from PIL import Image as _Image

        cm = np.asarray(cluster_map)
        rgba = None
        if bool(getattr(self, "semantic_region_split_enabled", False)):
            try:
                region_mask = self._semantic_region_planes_mask()
            except Exception:
                log.debug("semantic region preview failed", exc_info=True)
                region_mask = None
            if region_mask is not None:
                rgba = np.zeros((cm.shape[0], cm.shape[1], 4), dtype=np.uint8)
                for plane, color in self.SEMANTIC_ZONE_COLORS.items():
                    rgba[region_mask == int(plane)] = color
        if rgba is None:
            try:
                planes = self._semantic_cluster_planes()
            except Exception:
                log.debug("semantic planes preview failed", exc_info=True)
                return None
            if planes is None:
                return None
            rgba = np.zeros((cm.shape[0], cm.shape[1], 4), dtype=np.uint8)
            for cid, plane in planes.items():
                color = self.SEMANTIC_ZONE_COLORS.get(int(plane), self.SEMANTIC_ZONE_COLORS[1])
                rgba[cm == int(cid)] = color
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        if brush > 1:
            rgba = rgba.repeat(brush, axis=0).repeat(brush, axis=1)
        return _Image.fromarray(rgba, "RGBA")

    def _semantic_log_once(self, cm, message: str) -> None:
        try:
            token = id(cm)
            if getattr(self, "_semantic_log_token", None) != token:
                self._semantic_log_token = token
                self._log(message)
        except Exception:
            pass

    def _planes_from_saliency(self, cm, ids, saliency) -> dict[int, int] | None:
        """Two planes from the AI object-probability map: low-saliency clusters
        are the backdrop, everything else is the object (details included —
        user decision: two zones are simpler and sturdier than three). None
        when degenerate (one plane) -> the geometric heuristic takes over."""
        means: dict[int, float] = {}
        for cid in ids:
            mask = cm == cid
            if not mask.any():
                continue
            means[cid] = float(saliency[mask].mean())
        if len(means) <= 1:
            return None
        threshold = self._semantic_threshold_value()
        planes = {cid: (0 if means[cid] < threshold else 1) for cid in means}
        if len(set(planes.values())) <= 1:
            return None
        return planes


__all__ = ["SemanticOrderMixin"]
