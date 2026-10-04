# -*- coding: utf-8 -*-
"""fermat_spiral.py — contour-parallel ("onion") fill order with connected
descents, the discrete adaptation of the Connected Fermat Spirals idea
(docs/plans/WS1_fermat_routes.md; Zhao et al., SIGGRAPH 2016) for cell grids.

The region is peeled into rings (equal Chebyshev distance to the region
border), rings are organised into a containment tree, and the order walks each
ring as a loop, descending into a child ring at an adjacent cell — so convex
regions are covered by ONE continuous spiral-like stroke with zero pen lifts,
and progress is near-linear in area. Concave/noisy regions fragment into
branching rings; the engine-side selector compares the result against the
greedy snake and keeps whichever needs fewer lifts, so the mode can never be
worse than the shipped behaviour.

Pure NumPy + cv2, no engine/Qt imports; every cell is visited exactly once
(no overdraw), jumps are rendered by the caller (teleport — never a stray
line), matching the user's fill laws.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - cv2 is a hard dep of the app
    cv2 = None

_NB8 = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
_NB4_FIRST = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))


def peel_layers(mask_bool: np.ndarray) -> np.ndarray:
    """Per-cell peel depth: 0 = the region's border ring, 1 = next ring inward,
    ... ; -1 outside the region. Chebyshev distance (DIST_C) = iterative 3x3
    erosion, so consecutive layers are 8-adjacent rings."""
    mask = np.asarray(mask_bool, dtype=bool)
    out = np.full(mask.shape, -1, dtype=np.int32)
    if not mask.any() or cv2 is None:
        return out
    padded = np.pad(mask.astype(np.uint8), 1)
    dist = cv2.distanceTransform(padded, cv2.DIST_C, 3)[1:-1, 1:-1]
    out[mask] = dist[mask].astype(np.int32) - 1
    return out


def fermat_cell_order(comp_mask: np.ndarray, start_rc, *, max_cells: int = 150_000):
    """Visit order of ALL cells of the (single, 4-connected) component mask:
    ring by ring, descending into adjacent child rings. Returns [] when the
    input is unusable (caller falls back to the snake)."""
    comp = np.asarray(comp_mask, dtype=bool)
    total = int(comp.sum())
    if total == 0 or total > max_cells or cv2 is None:
        return []
    sr, sc = int(start_rc[0]), int(start_rc[1])
    if not (0 <= sr < comp.shape[0] and 0 <= sc < comp.shape[1]) or not comp[sr, sc]:
        return []

    layers = peel_layers(comp)
    max_layer = int(layers.max())

    # Rings: 8-connected components of each layer. ring_map[cell] = global ring id.
    ring_map = np.full(comp.shape, -1, dtype=np.int32)
    ring_cells: list[np.ndarray] = []   # ring id -> (n, 2) array of (r, c)
    ring_layer: list[int] = []
    for k in range(max_layer + 1):
        layer_mask = (layers == k).astype(np.uint8)
        n, lbl = cv2.connectedComponents(layer_mask, connectivity=8)
        for i in range(1, n):
            cells = np.argwhere(lbl == i)
            rid = len(ring_cells)
            ring_map[cells[:, 0], cells[:, 1]] = rid
            ring_cells.append(cells)
            ring_layer.append(k)
    if not ring_cells:
        return []

    # Children: ring at layer k+1 whose cells touch (8-adjacency) this ring.
    n_rings = len(ring_cells)
    children: list[set] = [set() for _ in range(n_rings)]
    has_parent = [False] * n_rings
    h, w = comp.shape
    for rid, cells in enumerate(ring_cells):
        k = ring_layer[rid]
        if k == 0:
            continue
        # find ANY adjacent ring of layer k-1 -> parent (first found wins; other
        # touching parents simply never adopt it, which is fine for a tree walk)
        parent = -1
        for r, c in cells:
            for dr, dc in _NB8:
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w:
                    nid = ring_map[nr, nc]
                    if nid >= 0 and ring_layer[nid] == k - 1:
                        parent = nid
                        break
            if parent >= 0:
                break
        if parent >= 0:
            children[parent].add(rid)
            has_parent[rid] = True

    visited_ring = [False] * n_rings
    visited = np.zeros(comp.shape, dtype=bool)
    order: list[tuple[int, int]] = []

    def _seq_jumps(seq) -> int:
        jumps = 0
        for a, b in zip(seq, seq[1:]):
            if max(abs(a[0] - b[0]), abs(a[1] - b[1])) > 1:
                jumps += 1
        return jumps

    def _greedy_sequence(cells: np.ndarray, start: tuple[int, int]):
        remaining = {(int(r), int(c)) for r, c in cells}
        cur = start
        seq = []
        last_dir = None
        while True:
            remaining.discard(cur)
            seq.append(cur)
            if not remaining:
                return seq
            nxt = None
            cand_dirs = _NB4_FIRST if last_dir is None else \
                (last_dir,) + tuple(d for d in _NB4_FIRST if d != last_dir)
            for dr, dc in cand_dirs:
                cell2 = (cur[0] + dr, cur[1] + dc)
                if cell2 in remaining:
                    nxt = cell2
                    last_dir = (dr, dc)
                    break
            if nxt is None:
                rem = np.array(sorted(remaining))
                d = (rem[:, 0] - cur[0]) ** 2 + (rem[:, 1] - cur[1]) ** 2
                nxt = tuple(int(v) for v in rem[int(np.argmin(d))])
                last_dir = None
            cur = nxt

    def _angle_sequence(cells: np.ndarray, start: tuple[int, int]):
        """Closed-loop walk by polar angle around the ring centroid: immune to
        the octant thickness clumps that dead-end the greedy walk on round
        rings (same-angle cells sort radially adjacent)."""
        cy = float(cells[:, 0].mean())
        cx = float(cells[:, 1].mean())
        ang = np.arctan2(cells[:, 0] - cy, cells[:, 1] - cx)
        idx = np.lexsort((np.hypot(cells[:, 0] - cy, cells[:, 1] - cx), ang))
        seq = [(int(cells[i][0]), int(cells[i][1])) for i in idx]
        k = seq.index(start) if start in seq else 0
        return seq[k:] + seq[:k]

    def walk_ring(rid: int, entry: tuple[int, int]):
        """Walk one ring as a loop (better of greedy / polar-angle order), then
        DFS into child rings at the nearest point. Leftover jumps inside ugly
        branching rings are teleported by the renderer."""
        stack = [(rid, entry)]
        while stack:
            cur_rid, cell = stack.pop()
            if visited_ring[cur_rid]:
                continue
            visited_ring[cur_rid] = True
            cells = ring_cells[cur_rid]
            cur = (int(cell[0]), int(cell[1]))
            cell_set = {(int(r), int(c)) for r, c in cells}
            if cur not in cell_set:  # entry hint off-ring: snap to nearest
                d = (cells[:, 0] - cell[0]) ** 2 + (cells[:, 1] - cell[1]) ** 2
                cur = tuple(int(v) for v in cells[int(np.argmin(d))])
            seq = _greedy_sequence(cells, cur)
            if _seq_jumps(seq) > 0 and len(cells) > 4:
                alt = _angle_sequence(cells, cur)
                if _seq_jumps(alt) < _seq_jumps(seq):
                    seq = alt
            for r, c in seq:
                visited[r, c] = True
            order.extend(seq)
            cur = seq[-1]
            # descend into children, nearest-entry-first relative to where we are
            kids = [kid for kid in children[cur_rid] if not visited_ring[kid]]
            entries = []
            for kid in kids:
                kc = ring_cells[kid]
                d = (kc[:, 0] - cur[0]) ** 2 + (kc[:, 1] - cur[1]) ** 2
                j = int(np.argmin(d))
                entries.append((float(d[j]), kid, (int(kc[j][0]), int(kc[j][1]))))
            # LIFO stack: push the farthest first so the NEAREST child is walked next
            for _dist, kid, kid_entry in sorted(entries, reverse=True):
                stack.append((kid, kid_entry))

    start_ring = int(ring_map[sr, sc])
    walk_ring(start_ring, (sr, sc))
    # Orphan rings (parent never found / separate branches): nearest-first sweep
    while True:
        left = [rid for rid in range(n_rings) if not visited_ring[rid]]
        if not left:
            break
        cur = order[-1] if order else (sr, sc)
        best = None
        for rid in left:
            kc = ring_cells[rid]
            d = (kc[:, 0] - cur[0]) ** 2 + (kc[:, 1] - cur[1]) ** 2
            j = int(np.argmin(d))
            cand = (float(d[j]), rid, (int(kc[j][0]), int(kc[j][1])))
            if best is None or cand[0] < best[0]:
                best = cand
        walk_ring(best[1], best[2])
    if len(order) != total:
        return []  # safety: never hand an incomplete order to the renderer
    return order


__all__ = ["peel_layers", "fermat_cell_order"]
