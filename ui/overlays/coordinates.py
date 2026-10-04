"""Physical screen pixels <-> logical Qt pixels, with monitor origins preserved."""
from __future__ import annotations

from ui.helpers.viewport_mapper import collect_monitor_snapshots


class DesktopCoordinates:
    def __init__(self, snapshots=None):
        self.snapshots = tuple(collect_monitor_snapshots() if snapshots is None else snapshots)

    def _monitor(self, x, y, space):
        if not self.snapshots:
            raise RuntimeError("No monitor geometry available for screen coordinates")

        def distance(snapshot):
            left, top, width, height = getattr(snapshot, space)
            dx = max(left - x, 0, x - (left + width - 1))
            dy = max(top - y, 0, y - (top + height - 1))
            return dx * dx + dy * dy

        return min(self.snapshots, key=distance)

    def to_logical(self, x, y):
        s = self._monitor(x, y, "desktop_rect")
        return (s.logical_rect[0] + (x - s.desktop_rect[0]) / s.scale_x,
                s.logical_rect[1] + (y - s.desktop_rect[1]) / s.scale_y)

    def to_physical(self, x, y):
        s = self._monitor(x, y, "logical_rect")
        return (round(s.desktop_rect[0] + (x - s.logical_rect[0]) * s.scale_x),
                round(s.desktop_rect[1] + (y - s.logical_rect[1]) * s.scale_y))

    def logical_items(self, items):
        result = []
        for original in items:
            item = dict(original)
            kind = item.get("type", "point")
            if kind == "line":
                for suffix in ("1", "2"):
                    x, y = self.to_logical(item["x" + suffix], item["y" + suffix])
                    item["x" + suffix], item["y" + suffix] = round(x), round(y)
            else:
                x, y = item["x"], item["y"]
                s = self._monitor(x, y, "desktop_rect")
                lx, ly = self.to_logical(x, y)
                item["x"], item["y"] = round(lx), round(ly)
                if kind == "rect":
                    item["w"], item["h"] = round(item["w"] / s.scale_x), round(item["h"] / s.scale_y)
                elif kind == "circle":
                    item["r"] = round(item.get("r", 20) / s.scale_x)
            result.append(item)
        return result
