"""Mixin extracted from engine/olegpainter/core.py.

Outline-fill drawing strategy: seed selection, fill tool integration, DFS fill.
"""
from __future__ import annotations

import logging

import math
from collections import deque
from pathlib import Path  # noqa: F401  used by some method annotations

from .common import *  # noqa: F401,F403  shared numpy/Pillow/etc.
from .translations import tr, LANGUAGES, current_lang  # noqa: F401
from .calibration import (  # noqa: F401
    calib_grab_screen,
    calib_show_fullscreen_top,
    calib_select_two_points,
    calib_select_region,
)
from .cluster_cleanup import apply_cleanup_mode  # noqa: F401
from .color_spaces import perceptual_lightness_from_rgb  # noqa: F401
from .prep_pipeline import build_perceptual_cluster_map  # noqa: F401
from .viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    full_area_rect,
    normalize_area_rect,
    normalize_desktop_rect,
)


log = logging.getLogger("olegpainter.engine.olegpainter.outline_fill")


class OutlineFillMixin:
    """Outline-fill drawing strategy: seed selection, fill tool integration, DFS fill."""

    def _outline_fill_settings_snapshot(self) -> dict:
        return {
            "mode": self.outline_fill_tool_mode,
            "brush_key": self.outline_fill_brush_key,
            "fill_key": self.outline_fill_fill_key,
            "brush_coord": tuple(self.outline_fill_brush_coord) if isinstance(self.outline_fill_brush_coord, (list, tuple)) else None,
            "fill_coord": tuple(self.outline_fill_fill_coord) if isinstance(self.outline_fill_fill_coord, (list, tuple)) else None,
        }

    def _outline_fill_requires_fill_tool(self) -> bool:
        algo = str(getattr(self, "drawing_algorithm", "") or "").strip().lower()
        return algo == "outline_and_fill"

    def _outline_fill_tool_label(self, tool: str) -> str:
        if tool == "fill":
            return tr("outline_fill_tool_fill")
        return tr("outline_fill_tool_brush")

    def _outline_fill_select_tool(self, tool: str) -> bool:
        try:
            self._mouse_up_with_settle()
        except Exception:
            log.debug('ignored exception in self._mouse_up_with_settle()', exc_info=True)
        preference = getattr(self, "outline_fill_tool_mode", "keys")
        coord_attr = "outline_fill_brush_coord" if tool == "brush" else "outline_fill_fill_coord"
        key_attr = "outline_fill_brush_key" if tool == "brush" else "outline_fill_fill_key"
        coords = getattr(self, coord_attr, None)
        tool_label = self._outline_fill_tool_label(tool)
        if preference == "coords":
            if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                try:
                    self._click_abs(int(coords[0]), int(coords[1]))
                    time.sleep(0.05)
                    self._input_click(button="left")
                    time.sleep(0.05)
                    return True
                except Exception as exc:
                    self._log(f"Outline & Fill tool selection via coords failed: {exc}", True)
            else:
                if self.status_callback:
                    self.status_callback(tr("status_outline_fill_coords_missing", tool=tool_label))
        sequence = str(getattr(self, key_attr, "") or "").strip()
        if sequence:
            try:
                self._input.send_keys(sequence)
                time.sleep(0.05)
                return True
            except Exception as exc:
                self._log(f"Outline & Fill tool selection via key '{sequence}' failed: {exc}", True)
        else:
            if self.status_callback:
                self.status_callback(tr("status_outline_fill_key_missing", tool=tool_label))
        self.stop_flag = True
        self.drawing_enabled = False
        return False

    def _outline_fill_safety_radius(self, brush: int) -> float:
        """A bucket-fill click must land at least this many cells deep inside the
        region (distance-to-nearest-edge) so it can NEVER land on / over the
        drawn outline. Tied to the brush, with a user-tunable floor."""
        try:
            floor = float(getattr(self, "outline_fill_min_interior_cells", 2.0))
        except (TypeError, ValueError):
            floor = 2.0
        floor = max(1.0, min(8.0, floor))
        return max(floor, float(max(1, int(brush))))

    def _outline_fill_seed_points(
        self,
        component_mask_bool: np.ndarray | None,
        limit: int = 5,
        dist_map: np.ndarray | None = None,
    ) -> list[tuple[int, int]]:
        """Bucket-click seeds GUARANTEED deep inside the component: each is a
        local maximum of the distance transform whose distance-to-edge is >= the
        safety radius, so the click never lands on the outline. One seed per safe
        "lobe" (handles concave / C-shaped / multi-pocket regions — the old
        centroid+edge anchors fell OUTSIDE those and snapped to a boundary cell,
        which is exactly the off-region misclick the user reported), then extra
        spaced maxima if more clicks are requested.

        Empty list => NO point is safely interior (thin / tiny / complex) =>
        the caller brush-fills the component via the main DFS instead of risking
        a missed bucket click."""
        if component_mask_bool is None or not component_mask_bool.any():
            return []
        if dist_map is not None:
            dist = np.asarray(dist_map, dtype=np.float32)
        else:
            try:
                dist = cv2.distanceTransform(
                    component_mask_bool.astype(np.uint8) * 255, cv2.DIST_L2, 3
                ).astype(np.float32)
            except Exception:
                return []
        if dist.shape != component_mask_bool.shape:
            return []

        brush = int(getattr(self, "brush_size", 1) or 1)
        safety = self._outline_fill_safety_radius(brush)
        max_clicks = max(1, int(getattr(self, "outline_fill_bucket_clicks", 5) or 1))
        limit = max(1, min(int(limit), max_clicks))

        safe = (dist >= safety) & component_mask_bool
        if not np.any(safe):
            return []   # nothing is safely interior -> caller uses the brush

        # One deepest seed per connected "lobe" of the safe core (8-connected so a
        # pinched waist still splits into separate pockets the app must each fill).
        try:
            n_lobes, lobe_labels = cv2.connectedComponents(safe.astype(np.uint8), connectivity=8)
        except Exception:
            n_lobes, lobe_labels = 0, None

        seeds: list[tuple[int, int]] = []
        if lobe_labels is not None and n_lobes > 1:
            lobe_ids = sorted(
                range(1, n_lobes),
                key=lambda i: int(np.count_nonzero(lobe_labels == i)),
                reverse=True,
            )
            for lid in lobe_ids:
                if len(seeds) >= limit:
                    break
                d = np.where(lobe_labels == lid, dist, -1.0)
                r, c = np.unravel_index(int(np.argmax(d)), d.shape)
                r, c = int(r), int(c)
                if component_mask_bool[r, c] and (r, c) not in seeds:
                    seeds.append((r, c))
        else:
            d = np.where(safe, dist, -1.0)
            r, c = np.unravel_index(int(np.argmax(d)), d.shape)
            if component_mask_bool[int(r), int(c)]:
                seeds.append((int(r), int(c)))

        # Extra spaced clicks (only if the user asked for more than one): biggest
        # remaining-distance cells, kept apart so they don't pile onto one spot.
        if len(seeds) < limit:
            safe_coords = np.argwhere(safe)
            if safe_coords.size:
                d_at = dist[safe_coords[:, 0], safe_coords[:, 1]]
                spacing = max(2.0, math.sqrt(max(1.0, float(safe_coords.shape[0])) / float(limit)))
                spacing_sq = spacing * spacing
                for i in np.argsort(d_at)[::-1]:
                    if len(seeds) >= limit:
                        break
                    r, c = int(safe_coords[i][0]), int(safe_coords[i][1])
                    if (r, c) in seeds:
                        continue
                    if any((r - sr) ** 2 + (c - sc) ** 2 < spacing_sq for sr, sc in seeds):
                        continue
                    seeds.append((r, c))
        return seeds[:limit]

    # ---- «Контур и заливка» / «Только контур» (GARTIC-003) -------------------

    @staticmethod
    def _outline_ring(mask_bool: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Edge cells (an 8-neighbour lies outside) and the inside they enclose.
        The ring is closed for a 4-connected bucket fill: it cannot leak out."""
        inner = cv2.erode(mask_bool.astype(np.uint8), np.ones((3, 3), np.uint8),
                          borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
        return mask_bool & ~inner, inner

    def _mark_done(self, cells_bool: np.ndarray) -> None:
        if self.drawn_mask is not None and cells_bool.any():
            self.drawn_mask[cells_bool] = True
            if self.pixel_update_callback:
                try:
                    self.pixel_update_callback()
                except Exception:
                    log.debug("progress callback failed", exc_info=True)

    def outline_only_current_color(self, mask_bool: np.ndarray, x0: int, y0: int, cluster_id: int) -> None:
        """«Только контур»: the edge of every region in its own colour, DFS route;
        the inside is left blank on purpose (counted as done)."""
        ring, inner = self._outline_ring(mask_bool)
        if ring.any():
            self.dfs_4dir_fill_current_color(ring.astype(np.uint8) * 255, x0, y0, cluster_id)
        if not self._automation_cancelled():
            self._mark_done(inner)

    def outline_and_fill_current_color(self, mask_bool: np.ndarray, x0: int, y0: int, cluster_id: int) -> None:
        """«Контур и заливка»: big regions get their edge drawn with the usual DFS
        and one bucket click inside; small/thin ones are simply drawn. After the
        clicks a screenshot checks what the bucket really filled and the rest is
        drawn with the brush (antialiased edges, a fill that leaked or failed)."""
        brush = max(1, int(self.brush_size))
        min_cells = max(24, int(getattr(self, "outline_fill_min_bucket_cells", 24) or 24))
        brush_plan = np.zeros_like(mask_bool)
        buckets: list[tuple[tuple[int, int], np.ndarray]] = []
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask_bool.astype(np.uint8), connectivity=4)
        for label in range(1, count):
            if self._automation_cancelled():
                return
            component = labels == label
            if int(stats[label, cv2.CC_STAT_AREA]) < min_cells * 2:
                brush_plan |= component
                continue
            ring, inner = self._outline_ring(component)
            pieces, piece_labels, piece_stats, _ = cv2.connectedComponentsWithStats(inner.astype(np.uint8), connectivity=4)
            found = False
            for piece in range(1, pieces):
                piece_mask = piece_labels == piece
                seeds = (self._outline_fill_seed_points(piece_mask, limit=1)
                         if int(piece_stats[piece, cv2.CC_STAT_AREA]) >= min_cells else [])
                if seeds:
                    buckets.append((seeds[0], piece_mask))
                    found = True
                else:
                    brush_plan |= piece_mask
            brush_plan |= ring if found else component
        if brush_plan.any():
            if not self._outline_fill_select_tool("brush"):
                return
            self.dfs_4dir_fill_current_color(brush_plan.astype(np.uint8) * 255, x0, y0, cluster_id)
        if not buckets or self._automation_cancelled():
            return
        if not self._outline_fill_select_tool("fill"):
            return
        region_w, region_h = self._region_dimensions(fallback_cols=mask_bool.shape[1],
                                                     fallback_rows=mask_bool.shape[0], brush=brush)
        for (row, col), _piece in buckets:
            if self._automation_cancelled():
                break
            self._click_abs(self._cell_center_axis(x0, region_w, col, brush),
                            self._cell_center_axis(y0, region_h, row, brush))
            time.sleep(0.02)
            self._input_click(button="left")
            time.sleep(max(0.03, float(getattr(self, "pen_stroke_gap", 0.0) or 0.0)))
        if self._automation_cancelled():
            return
        filled = np.zeros_like(mask_bool)
        for _seed, piece in buckets:
            filled |= piece
        missed = self._bucket_missed_cells(filled, cluster_id, x0, y0, region_w, region_h, brush)
        self._mark_done(filled & ~missed)
        if not self._outline_fill_select_tool("brush"):
            return
        if missed.any():
            self._log(f"Outline+Fill: the bucket missed {int(missed.sum())} cell(s); drawing them with the brush.")
            self.dfs_4dir_fill_current_color(missed.astype(np.uint8) * 255, x0, y0, cluster_id)

    def _bucket_missed_cells(self, filled, cluster_id, x0, y0, region_w, region_h, brush) -> np.ndarray:
        """Cells the bucket should have painted whose screen colour is still wrong
        (checked at cell centres). Unreadable screen: trust the bucket."""
        target = next((rgb for hx, cid, _n, rgb in self.color_palette if cid == cluster_id), None)
        if target is None:
            return np.zeros_like(filled)
        time.sleep(0.25)  # the program shows its fill on the next frames
        try:
            from infrastructure.screen_capture import capture_screen
            rows, cols = np.nonzero(filled)
            xs = np.array([self._cell_center_axis(x0, region_w, c, brush) for c in cols], dtype=np.int64)
            ys = np.array([self._cell_center_axis(y0, region_h, r, brush) for r in rows], dtype=np.int64)
            left, top = int(xs.min()), int(ys.min())
            shot = np.asarray(capture_screen(bbox=(left, top, int(xs.max()) + 1, int(ys.max()) + 1)).convert("RGB"),
                              dtype=np.int16)
            seen = shot[ys - top, xs - left]
        except Exception:
            log.debug("bucket check: screen unreadable", exc_info=True)
            return np.zeros_like(filled)
        wrong = np.abs(seen - np.asarray(target[:3], dtype=np.int16)).sum(axis=1) > 60
        missed = np.zeros_like(filled)
        missed[rows[wrong], cols[wrong]] = True
        return missed
