"""Space-filling curve cell orders.

gilbert2d is a port of Jakub Cerveny's generalized Hilbert curve
(github.com/jakubcerveny/gilbert, BSD-2): a Hilbert-like traversal of an
ARBITRARY w x h rectangle, not just 2^n squares. Consecutive points are
4-adjacent everywhere except single diagonal steps that odd dimensions force.

Why it earns a place next to the snake/DFS orders: consecutive curve points
are almost always neighbours, so sorting a region's cells by curve index
yields long locality-preserving strokes whose progress is ~linear in area.
"""
from __future__ import annotations


def _sgn(x: int) -> int:
    return -1 if x < 0 else (1 if x > 0 else 0)


def _generate2d(x: int, y: int, ax: int, ay: int, bx: int, by: int):
    w = abs(ax + ay)
    h = abs(bx + by)
    dax, day = _sgn(ax), _sgn(ay)  # unit step along the major axis
    dbx, dby = _sgn(bx), _sgn(by)  # unit step along the orthogonal axis

    if h == 1:
        for _ in range(w):
            yield (x, y)
            x, y = x + dax, y + day
        return
    if w == 1:
        for _ in range(h):
            yield (x, y)
            x, y = x + dbx, y + dby
        return

    ax2, ay2 = ax // 2, ay // 2
    bx2, by2 = bx // 2, by // 2
    w2 = abs(ax2 + ay2)
    h2 = abs(bx2 + by2)

    if 2 * w > 3 * h:
        if (w2 % 2) and (w > 2):
            ax2, ay2 = ax2 + dax, ay2 + day
        # long case: split the rectangle in two along the major axis
        yield from _generate2d(x, y, ax2, ay2, bx, by)
        yield from _generate2d(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by)
    else:
        if (h2 % 2) and (h > 2):
            bx2, by2 = bx2 + dbx, by2 + dby
        # standard case: one step up, one long horizontal, one step down
        yield from _generate2d(x, y, bx2, by2, ax2, ay2)
        yield from _generate2d(x + bx2, y + by2, ax, ay, bx - bx2, by - by2)
        yield from _generate2d(
            x + (ax - dax) + (bx2 - dbx),
            y + (ay - day) + (by2 - dby),
            -bx2, -by2, -(ax - ax2), -(ay - ay2),
        )


def gilbert2d(width: int, height: int):
    """Yield every (x, y) of a width x height rectangle in generalized-Hilbert
    order. Yields nothing for empty rectangles."""
    if width <= 0 or height <= 0:
        return
    if width >= height:
        yield from _generate2d(0, 0, width, 0, 0, height)
    else:
        yield from _generate2d(0, 0, 0, height, width, 0)


def gilbert_order_for_cells(cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort (row, col) cells by their generalized-Hilbert index over the cells'
    bounding box. The result visits all cells exactly once; consecutive cells
    are usually neighbours, with jumps only where the region is absent from the
    curve (the caller's renderer must already handle non-adjacent steps)."""
    if not cells:
        return []
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    min_r, min_c = min(rows), min(cols)
    width = max(cols) - min_c + 1
    height = max(rows) - min_r + 1
    rank: dict[tuple[int, int], int] = {}
    for idx, (gx, gy) in enumerate(gilbert2d(width, height)):
        rank[(gy + min_r, gx + min_c)] = idx
    return sorted(cells, key=lambda cell: rank[cell])


__all__ = ["gilbert2d", "gilbert_order_for_cells"]
