"""Straight segments route: the fewest horizontal/vertical strokes that cover a colour.

Every cell lies on exactly one maximal horizontal and one maximal vertical run of
its colour. Choosing the fewest runs that contain every cell is a minimum vertex
cover of the bipartite graph "horizontal runs - vertical runs" (a cell is an
edge), which by Konig's theorem equals a maximum matching and is found exactly.

Where every stroke costs a press and a pause (browser games, Gartic Phone) this
drew Spongebob with 1845 strokes instead of 6111 for the DFS route: 53 s instead
of 120 s at the same accuracy (live Gartic Phone measurement).
"""
from __future__ import annotations

import numpy as np


def _runs(mask: np.ndarray, horizontal: bool):
    """Label maximal runs along rows (horizontal) or columns; labels start at 1."""
    m = mask if horizontal else mask.T
    starts = m & ~np.pad(m, ((0, 0), (1, 0)))[:, :-1]
    ids = np.cumsum(starts.ravel()).reshape(m.shape)
    labels = np.where(m, ids, 0)
    return (labels if horizontal else labels.T), int(starts.sum())


def cover_segments(mask) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Minimum set of straight runs ((r0, c0), (r1, c1)) covering every True cell."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import maximum_bipartite_matching

    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return []
    h_lab, nh = _runs(mask, True)
    v_lab, nv = _runs(mask, False)
    rows, cols = np.nonzero(mask)
    left, right = h_lab[rows, cols] - 1, v_lab[rows, cols] - 1
    graph = csr_matrix((np.ones(len(rows), np.int8), (left, right)), shape=(nh, nv))
    match = maximum_bipartite_matching(graph, perm_type="column")  # left -> right or -1
    matched_right = np.full(nv, -1, np.int64)
    matched = match >= 0
    matched_right[match[matched]] = np.flatnonzero(matched)
    # Konig: walk alternating paths from unmatched left vertices;
    # cover = unvisited left + visited right.
    seen_left, seen_right = np.zeros(nh, bool), np.zeros(nv, bool)
    stack = list(np.flatnonzero(~matched))
    seen_left[stack] = True
    indptr, indices = graph.indptr, graph.indices
    while stack:
        i = stack.pop()
        for v in indices[indptr[i]:indptr[i + 1]]:
            if not seen_right[v]:
                seen_right[v] = True
                j = matched_right[v]
                if j >= 0 and not seen_left[j]:
                    seen_left[j] = True
                    stack.append(j)
    segments = []
    for labels, keep, horizontal in ((h_lab, ~seen_left, True), (v_lab, seen_right, False)):
        wanted = np.zeros(max(nh, nv) + 1, bool)
        wanted[np.flatnonzero(keep) + 1] = True
        rr, cc = np.nonzero(wanted[labels] & mask)
        if rr.size == 0:
            continue  # e.g. one vertical line: no horizontal run is needed
        ids = labels[rr, cc]
        order = np.argsort(ids, kind="stable")
        rr, cc, ids = rr[order], cc[order], ids[order]
        bounds = np.flatnonzero(np.diff(ids)) + 1
        for a, b in zip(np.r_[0, bounds], np.r_[bounds, len(ids)]):
            if horizontal:
                segments.append(((int(rr[a]), int(cc[a:b].min())), (int(rr[a]), int(cc[a:b].max()))))
            else:
                segments.append(((int(rr[a:b].min()), int(cc[a])), (int(rr[a:b].max()), int(cc[a]))))
    return segments


def order_segments(segments, start=(0, 0)):
    """Nearest next segment (either end first); pen-up flights are cheap but not free."""
    if not segments:
        return []
    ends = np.array([[a[0], a[1], b[0], b[1]] for a, b in segments], dtype=np.int64)
    left = np.ones(len(segments), bool)
    pos = np.array(start, dtype=np.int64)
    out = []
    for _ in range(len(segments)):
        idx = np.flatnonzero(left)
        d_a = np.abs(ends[idx, 0] - pos[0]) + np.abs(ends[idx, 1] - pos[1])
        d_b = np.abs(ends[idx, 2] - pos[0]) + np.abs(ends[idx, 3] - pos[1])
        k = int(np.argmin(np.minimum(d_a, d_b)))
        i = int(idx[k])
        a, b = segments[i]
        if d_b[k] < d_a[k]:
            a, b = b, a
        out.append((a, b))
        left[i] = False
        pos = np.array(b, dtype=np.int64)
    return out


def segment_cells(segments):
    """Cell path for the shared renderer: each segment walked end to end."""
    cells = []
    for (r0, c0), (r1, c1) in segments:
        if cells and cells[-1] == (r0, c0):
            r0, c0 = (r0, c0 + (1 if c1 > c0 else -1)) if r0 == r1 and c0 != c1 else                      ((r0 + (1 if r1 > r0 else -1), c0) if r0 != r1 else (r0, c0))
            if cells[-1] == (r0, c0):
                continue  # a one-cell segment on the previous end

        if r0 == r1:
            step = 1 if c1 >= c0 else -1
            cells.extend((r0, c) for c in range(c0, c1 + step, step))
        else:
            step = 1 if r1 >= r0 else -1
            cells.extend((r, c0) for r in range(r0, r1 + step, step))
    return cells
