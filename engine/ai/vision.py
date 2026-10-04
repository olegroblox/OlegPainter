"""The AI image tools themselves (AI-001). Pure functions over PIL images.

Every function takes a SessionPool, the ModelStore and the device string, so
it runs the same way in the app worker and in tests. Results are PIL images
or float masks at the source resolution; applying them to the drawing is the
caller's job.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .models import ModelStore
from .runtime import SessionPool


def _flat(image: Image.Image) -> Image.Image:
    """RGB for the networks. Transparent pixels usually store black, which the
    models would read as dark background, so they are laid on white paper."""
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        paper = Image.new("RGBA", image.size, (255, 255, 255, 255))
        paper.alpha_composite(image.convert("RGBA"))
        return paper.convert("RGB")
    return image.convert("RGB")


def _rgb(image: Image.Image) -> np.ndarray:
    return np.asarray(_flat(image), dtype=np.float32) / 255.0


def _normalize(rgb: np.ndarray, mean, std) -> np.ndarray:
    arr = (rgb - np.asarray(mean, np.float32)) / np.asarray(std, np.float32)
    return arr.transpose(2, 0, 1)[None].astype(np.float32)


def _resize(arr: np.ndarray, width: int, height: int, *, nearest: bool = False) -> np.ndarray:
    return cv2.resize(arr, (int(width), int(height)),
                      interpolation=cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR)


def _run_first(pool: SessionPool, path: Path, device: str, pixels: np.ndarray) -> np.ndarray:
    """First output of a single-input model, with the pool's CPU retry."""
    name = pool.inputs(path, device)[0]
    return pool.run(path, device, [pool.outputs(path, device)[0]], {name: pixels})[0]


# ----- background -------------------------------------------------------------
def foreground_mask(pool: SessionPool, store: ModelStore, device: str, model_id: str,
                    image: Image.Image) -> np.ndarray:
    """Object probability 0..1 (1 = keep) at the image size."""
    info = store.get(model_id)
    size = int(info.runtime.get("input_size", 320))
    rgb = _resize(_rgb(image), size, size)
    pixels = _normalize(rgb, info.runtime["mean"], info.runtime["std"])
    pred = np.asarray(_run_first(pool, store.path(model_id), device, pixels), dtype=np.float32).reshape(size, size)
    if info.runtime.get("output") == "sigmoid":
        pred = 1.0 / (1.0 + np.exp(-pred))
    else:
        low, high = float(pred.min()), float(pred.max())
        pred = (pred - low) / (high - low) if high > low else np.zeros_like(pred)
    return np.clip(_resize(pred, image.width, image.height), 0.0, 1.0)


def apply_mask(image: Image.Image, mask: np.ndarray, *, threshold: float = 0.5, feather: int = 1) -> Image.Image:
    """RGBA with the background transparent. The drawing engine already treats
    transparent pixels as background, so this plugs into every draw mode."""
    rgba = np.array(image.convert("RGBA"))
    keep = (mask >= threshold).astype(np.uint8) * 255
    if feather > 0:
        keep = cv2.GaussianBlur(keep, (feather * 2 + 1, feather * 2 + 1), 0)
    rgba[..., 3] = np.minimum(rgba[..., 3], keep)
    return Image.fromarray(rgba, "RGBA")


# ----- depth ------------------------------------------------------------------
def depth_map(pool: SessionPool, store: ModelStore, device: str, model_id: str, image: Image.Image) -> np.ndarray:
    """Relative nearness 0..1 (1 = closest to the viewer) at the image size."""
    info = store.get(model_id)
    side = int(info.runtime.get("input_size", 518))
    scale = side / max(image.width, image.height)
    width = max(14, int(round(image.width * scale / 14)) * 14)
    height = max(14, int(round(image.height * scale / 14)) * 14)
    pixels = _normalize(_resize(_rgb(image), width, height), info.runtime["mean"], info.runtime["std"])
    pred = np.asarray(_run_first(pool, store.path(model_id), device, pixels), dtype=np.float32).reshape(height, width)
    low, high = float(pred.min()), float(pred.max())
    pred = (pred - low) / (high - low) if high > low else np.zeros_like(pred)
    return _resize(pred, image.width, image.height)


# ----- line art ---------------------------------------------------------------
def line_art(pool: SessionPool, store: ModelStore, device: str, model_id: str, image: Image.Image) -> Image.Image:
    """Black lines on a transparent sheet, same size as the source: only the
    lines are drawn, the paper stays background."""
    info = store.get(model_id)
    max_side = int(info.runtime.get("max_side", 1024))
    scale = min(1.0, max_side / max(image.width, image.height))
    width = max(8, int(round(image.width * scale / 8)) * 8)
    height = max(8, int(round(image.height * scale / 8)) * 8)
    rgb = _resize(_rgb(image), width, height)
    pred = np.asarray(_run_first(pool, store.path(model_id), device, rgb.transpose(2, 0, 1)[None].astype(np.float32)),
                      dtype=np.float32).reshape(height, width)
    gray = np.clip(pred * 255.0, 0, 255).astype(np.uint8)
    gray = _resize(gray, image.width, image.height)
    # Faint strokes are paper; real lines keep their darkness as opacity.
    ink = np.where(gray > 200, 0, 255 - gray).astype(np.uint8)
    ink = np.where(ink > 0, np.maximum(ink, 160), 0).astype(np.uint8)
    rgba = np.zeros((image.height, image.width, 4), dtype=np.uint8)
    rgba[..., 3] = ink
    return Image.fromarray(rgba, "RGBA")


# ----- flat "cartoon" look (no model) -------------------------------------------
def flatten(image: Image.Image, *, strength: int = 2) -> Image.Image:
    """Edge-preserving smoothing: large flat patches instead of photo noise, so
    a small palette draws faster and cleaner. Classic filters, no network."""
    rgba = np.array(image.convert("RGBA"))
    bgr = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2BGR)
    strength = max(1, min(4, int(strength)))
    scale = min(1.0, 1200.0 / max(bgr.shape[:2]))
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else bgr
    for _ in range(strength * 2):
        small = cv2.bilateralFilter(small, 9, 75, 9)
    small = cv2.pyrMeanShiftFiltering(small, 8 + 6 * strength, 24 + 14 * strength)
    if scale < 1.0:
        small = cv2.resize(small, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_LINEAR)
    rgba[..., :3] = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgba, "RGBA")


# ----- upscale -------------------------------------------------------------------
def upscale(store: ModelStore, model_id: str, image: Image.Image, *, scale: int = 2, style: str = "photo",
            cancelled=lambda: False) -> Image.Image:
    """Real-ESRGAN (ncnn/Vulkan, any GPU; its own CPU fallback is slow).
    The network is x4; x2 is the x4 result downsampled with Lanczos."""
    info = store.get(model_id)
    folder = store.path(model_id)
    exe = folder / info.runtime["binary"]
    model = info.runtime["models"].get(style) or info.runtime["models"]["photo"]
    alpha = image.getchannel("A") if image.mode in ("RGBA", "LA") else None
    with tempfile.TemporaryDirectory(prefix="olegpainter-upscale-") as tmp:
        src, dst = Path(tmp) / "in.png", Path(tmp) / "out.png"
        image.convert("RGB").save(src)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen([str(exe), "-i", str(src), "-o", str(dst), "-n", model, "-s", "4",
                                    "-m", str(folder / "models")],
                                   cwd=str(folder), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   creationflags=flags)
        while process.poll() is None:
            if cancelled():
                process.kill()
                process.wait()
                raise RuntimeError("Отменено.")
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        if process.returncode != 0 or not dst.is_file():
            detail = (process.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError("Увеличение не удалось" + (f": {detail[-1]}" if detail else "."))
        result = Image.open(dst).convert("RGB")
        result.load()
    target = (image.width * scale, image.height * scale)
    if result.size != target:
        result = result.resize(target, Image.Resampling.LANCZOS)
    if alpha is not None:
        result.putalpha(alpha.resize(target, Image.Resampling.LANCZOS))
    return result


# ----- click to select (SAM 2.1) ---------------------------------------------------
class ObjectSelector:
    """Encodes one picture once; every click then runs only the small decoder.
    Points are in source pixels; positive points add, negative points remove."""

    def __init__(self, pool: SessionPool, store: ModelStore, device: str, model_id: str, image: Image.Image):
        info = store.get(model_id)
        self._pool, self._device = pool, device
        # The small decoder uses operators DirectML cannot run; it is fast on the CPU.
        self._decoder_device = info.runtime.get("decoder_device", device)
        self._decoder = store.path(model_id, "prompt_encoder_mask_decoder.onnx")
        encoder = store.path(model_id, "vision_encoder.onnx")
        self.size = image.size
        side = int(info.runtime.get("input_size", 1024))
        self._side = side
        pixels = _normalize(_resize(_rgb(image), side, side), info.runtime["mean"], info.runtime["std"])
        names = pool.outputs(encoder, device)
        self._embeddings = dict(zip(names, pool.run(encoder, device, names, {pool.inputs(encoder, device)[0]: pixels})))

    def mask(self, points: list[tuple[float, float, bool]]) -> np.ndarray:
        """Boolean mask at the source size for the given (x, y, positive) clicks."""
        if not points:
            return np.zeros((self.size[1], self.size[0]), dtype=bool)
        sx, sy = self._side / self.size[0], self._side / self.size[1]
        coords = np.array([[[[x * sx, y * sy] for x, y, _ in points]]], dtype=np.float32)
        labels = np.array([[[1 if positive else 0 for _, _, positive in points]]], dtype=np.int64)
        feed = {"input_points": coords, "input_labels": labels,
                "input_boxes": np.zeros((1, 0, 4), dtype=np.float32), **self._embeddings}
        wanted = set(self._pool.inputs(self._decoder, self._decoder_device))
        iou, masks = self._pool.run(self._decoder, self._decoder_device, ["iou_scores", "pred_masks"],
                                    {k: v for k, v in feed.items() if k in wanted})
        best = int(np.argmax(iou[0, 0]))
        logits = np.asarray(masks[0, 0, best], dtype=np.float32)
        return _resize(logits, self.size[0], self.size[1]) > 0.0


# ----- erase (LaMa) ------------------------------------------------------------------
def erase(pool: SessionPool, store: ModelStore, device: str, model_id: str, image: Image.Image,
          mask: np.ndarray, *, grow: int = 6) -> Image.Image:
    """Fill the masked area with plausible background. Works on a crop around
    the mask at the model's 512 px so large pictures keep their detail."""
    info = store.get(model_id)
    if info.runtime.get("cpu_only"):
        device = "cpu"  # uses operators DirectML cannot run (FFT)
    side = int(info.runtime.get("input_size", 512))
    hole = mask.astype(np.uint8)
    if grow > 0:
        hole = cv2.dilate(hole, np.ones((grow * 2 + 1, grow * 2 + 1), np.uint8))
    ys, xs = np.nonzero(hole)
    if xs.size == 0:
        return image.copy()
    rgba = np.array(image.convert("RGBA"))
    flat = np.asarray(_flat(image))
    height, width = hole.shape
    # Square context around the hole, at least twice its size.
    span = int(max(xs.max() - xs.min(), ys.max() - ys.min()) * 2 + 32)
    span = min(max(span, 64), max(width, height))
    cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
    x0 = int(min(max(0, cx - span // 2), max(0, width - span)))
    y0 = int(min(max(0, cy - span // 2), max(0, height - span)))
    x1, y1 = min(width, x0 + span), min(height, y0 + span)
    crop_rgb = flat[y0:y1, x0:x1].astype(np.float32) / 255.0
    crop_hole = hole[y0:y1, x0:x1].astype(np.float32)
    image_in = _resize(crop_rgb, side, side).transpose(2, 0, 1)[None].astype(np.float32)
    mask_in = (_resize(crop_hole, side, side, nearest=True) > 0.5).astype(np.float32)[None, None]
    path = store.path(model_id)
    output = pool.outputs(path, device)[0]
    out = np.asarray(pool.run(path, device, [output], {"image": image_in, "mask": mask_in})[0][0],
                     dtype=np.float32).transpose(1, 2, 0)
    if out.max() <= 1.5:  # some exports return 0..1
        out = out * 255.0
    filled = _resize(np.clip(out, 0, 255), x1 - x0, y1 - y0)
    region = rgba[y0:y1, x0:x1, :3]
    keep = crop_hole[..., None] > 0.5
    rgba[y0:y1, x0:x1, :3] = np.where(keep, filled, region).astype(np.uint8)
    # Transparency inside the hole follows its surroundings: an object erased
    # from a cut-out picture leaves transparent background, not a patch.
    if rgba[..., 3].min() < 255:
        ring = cv2.dilate(hole, np.ones((9, 9), np.uint8)).astype(bool) & ~hole.astype(bool)
        if ring.any() and float((rgba[..., 3][ring] < 128).mean()) > 0.5:
            rgba[..., 3][hole.astype(bool)] = 0
        else:
            rgba[..., 3] = cv2.inpaint(rgba[..., 3], hole, 5, cv2.INPAINT_TELEA)
    return Image.fromarray(rgba, "RGBA")
