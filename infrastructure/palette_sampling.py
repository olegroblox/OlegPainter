"""Bounded colour-grid sampling from one cursor-free target-window frame."""
from dataclasses import asdict, dataclass
import math

import numpy as np

from infrastructure.window_sampling import WindowSampleRequest, WindowSampleError, sample_window_json


@dataclass(frozen=True)
class PaletteSampleRequest(WindowSampleRequest):
    palette: tuple
    slider: tuple | None = None

    def validate_regions(self):
        left, top, right, bottom = self.frame_bounds
        for region in (self.palette, self.slider):
            if region is None:
                continue
            if len(region) != 4 or any(type(v) is not int for v in region):
                raise WindowSampleError("Некорректная рамка палитры.")
            x, y, w, h = region
            if (w < 4 or h < 4 or w * h > 64_000_000
                    or x < left or y < top or x + w > right or y + h > bottom):
                raise WindowSampleError("Рамка должна целиком находиться внутри одного окна.")

    def validate(self, api):
        super().validate(api)
        self.validate_regions()

    @classmethod
    def from_regions(cls, palette, slider=None, *, api=None):
        x, y, w, h = palette
        sample = WindowSampleRequest.at(x + w // 2, y + h // 2, api=api)
        if sample is None:
            raise WindowSampleError("Выберите палитру в другой программе, а не в OlegPainter или на рабочем столе.")
        request = cls(sample.identity, sample.bounds, sample.frame_bounds, sample.point,
                      tuple(palette), tuple(slider) if slider is not None else None)
        request.validate_regions()
        if slider is not None:
            sx, sy, sw, sh = slider
            other = WindowSampleRequest.at(sx + sw // 2, sy + sh // 2, api=api)
            if other is None or other.identity != request.identity:
                raise WindowSampleError("Палитра и яркость должны находиться в одном окне.")
        return request

    @classmethod
    def from_payload(cls, payload):
        sample = WindowSampleRequest.from_payload(payload)
        return cls(sample.identity, sample.bounds, sample.frame_bounds, sample.point,
                   tuple(payload["palette"]), tuple(payload["slider"]) if payload.get("slider") else None)


def sample_grid(region):
    x, y, w, h = region
    step = max(1, math.ceil(math.sqrt(w * h / 3000)))
    while math.ceil(w / step) * math.ceil(h / step) > 3000:
        step += 1
    xx, yy = np.meshgrid(np.arange(x, x + w, step), np.arange(y, y + h, step))
    return np.column_stack((xx.ravel(), yy.ravel())).astype(np.int32)


def _colour_keys(rgb):
    """Colours with a small tolerance for compression and antialiasing noise."""
    q = np.asarray(rgb, dtype=np.int32) // 4
    return q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]


def without_panel_background(rgb, coords):
    """Mask of samples that are colours, not the panel around and between them.

    Clicking the panel selects nothing. Panel colours touch the edge of the user's
    frame and cover a noticeable share of it (live Paint 2026-10-01: two dark tones,
    1242 of 2020 samples); swatches and gradient wheels do not.
    """
    keys = _colour_keys(rgb)
    coords = np.asarray(coords)
    xs, ys = coords[:, 0], coords[:, 1]
    border = (xs == xs.min()) | (xs == xs.max()) | (ys == ys.min()) | (ys == ys.max())
    values, counts = np.unique(keys, return_counts=True)
    frequent = values[counts >= 0.05 * len(keys)]
    edge_values, edge_counts = np.unique(keys[border], return_counts=True)
    # A swatch cut by a tight frame touches the edge too, but the panel runs along it.
    along_edge = edge_values[edge_counts >= 0.25 * border.sum()]
    background = np.intersect1d(frequent, along_edge)
    panel = np.isin(keys, background)
    if panel.all():
        return np.ones(len(keys), dtype=bool)
    return ~panel


def palette_from_frame(buffer, request):
    request.validate_regions()
    left, top, right, bottom = request.frame_bounds
    if buffer.shape != (bottom - top, right - left, 4):
        raise WindowSampleError("Размер кадра не совпал с физическими границами окна.")
    coords = sample_grid(request.palette)
    pixels = buffer[coords[:, 1] - top, coords[:, 0] - left]
    if np.any(pixels[:, 3] != 255):
        raise WindowSampleError("Палитра содержит прозрачные образцы. Выделите непрозрачную область.")
    slider = None
    if request.slider is not None:
        x, y, w, h = request.slider
        crop = buffer[y - top:y - top + h, x - left:x - left + w]
        if np.any(crop[:, :, 3] != 255):
            raise WindowSampleError("Ползунок содержит прозрачные пиксели.")
        gray = crop[:, :, :3].astype(np.float32).mean(axis=2)
        if h >= w:
            first, last = float(gray[:max(1, h // 4)].mean()), float(gray[-max(1, h // 4):].mean())
            ends = (y, y + h - 1) if first >= last else (y + h - 1, y)
            slider = ["vertical", x + w // 2, *ends]
        else:
            first, last = float(gray[:, :max(1, w // 4)].mean()), float(gray[:, -max(1, w // 4):].mean())
            ends = (x, x + w - 1) if first >= last else (x + w - 1, x)
            slider = ["horizontal", y + h // 2, *ends]
        if abs(first - last) < 2:
            raise WindowSampleError("Не удалось определить светлый конец ползунка. Выделите его градиент точнее.")
    return dict(rgb=pixels[:, :3][:, ::-1].tolist(), coords=coords.tolist(), slider=slider)


def sample_window_palette(request, cancelled, *, command=None, timeout=3.0):
    request.validate_regions()
    payload = asdict(request)
    payload["kind"] = "palette"
    result = sample_window_json(payload, cancelled, command=command, timeout=timeout, max_response=512_000)
    expected = sample_grid(request.palette).tolist()
    rgb, coords, slider = result.get("rgb"), result.get("coords"), result.get("slider")
    if (coords != expected or not isinstance(rgb, list) or len(rgb) != len(expected)
            or any(not isinstance(row, list) or len(row) != 3
                   or any(type(c) is not int or not 0 <= c <= 255 for c in row) for row in rgb)):
        raise WindowSampleError("Измеритель передал некорректные образцы палитры.")
    if request.slider is None:
        valid_slider = slider is None
    else:
        x, y, w, h = request.slider
        valid = (["vertical", x + w // 2, y, y + h - 1] if h >= w
                 else ["horizontal", y + h // 2, x, x + w - 1])
        valid_slider = (isinstance(slider, list) and len(slider) == 4
                        and all(type(v) is int for v in slider[1:])
                        and slider in (valid, [valid[0], valid[1], valid[3], valid[2]]))
    if not valid_slider:
        raise WindowSampleError("Измеритель передал некорректный ползунок яркости.")
    # The worker reports the whole grid (checked above); the panel is dropped here.
    keep = without_panel_background(np.asarray(rgb), np.asarray(coords))
    return dict(result, rgb=[row for row, k in zip(rgb, keep) if k],
                coords=[point for point, k in zip(coords, keep) if k])
