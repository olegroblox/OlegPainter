"""Run every installed AI tool on one picture and save the results.

Usage: python tools/ai_smoke.py [image] [output-dir] [--device auto|gpu|cpu]
No UI, no input devices. Times include the first (cold) model load.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from engine.ai import vision  # noqa: E402
from engine.ai.models import ModelStore  # noqa: E402
from engine.ai.runtime import SessionPool, resolve_device  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?", default="tools/paint_bench/images/05_photo_chick.png")
    parser.add_argument("output", nargs="?", default="test-results/ai-smoke")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    image = Image.open(args.image).convert("RGBA")
    store, pool, device = ModelStore(), SessionPool(), resolve_device(args.device)
    report = {"device": device, "image": args.image, "size": image.size, "results": {}}

    def timed(name, fn):
        start = time.perf_counter()
        try:
            result = fn()
        except Exception as exc:  # report and continue with the other tools
            report["results"][name] = {"error": str(exc)}
            return None
        report["results"][name] = {"seconds": round(time.perf_counter() - start, 3)}
        return result

    for model_id in ("bg.u2netp", "bg.isnet", "bg.birefnet"):
        if store.is_installed(model_id):
            mask = timed(model_id, lambda m=model_id: vision.foreground_mask(pool, store, device, m, image))
            if mask is not None:
                vision.apply_mask(image, mask).save(out / f"{model_id}.png")
    if store.is_installed("depth.dav2-small"):
        depth = timed("depth", lambda: vision.depth_map(pool, store, device, "depth.dav2-small", image))
        if depth is not None:
            Image.fromarray((depth * 255).astype("uint8")).save(out / "depth.png")
    if store.is_installed("lineart.informative"):
        lines = timed("lineart", lambda: vision.line_art(pool, store, device, "lineart.informative", image))
        if lines is not None:
            lines.save(out / "lineart.png")
    if store.is_installed("segment.sam21-tiny"):
        point = (image.width * 0.5, image.height * 0.5, True)
        selector = timed("select.encode", lambda: vision.ObjectSelector(pool, store, device, "segment.sam21-tiny", image))
        mask = timed("select.click", lambda: selector.mask([point])) if selector else None
        if mask is not None:
            Image.fromarray((mask * 255).astype("uint8")).save(out / "select.png")
            if store.is_installed("inpaint.lama"):
                erased = timed("erase", lambda: vision.erase(pool, store, device, "inpaint.lama", image, mask))
                if erased is not None:
                    erased.save(out / "erase.png")
    flat = timed("flatten", lambda: vision.flatten(image))
    if flat is not None:
        flat.save(out / "flatten.png")
    if store.is_installed("upscale.realesrgan"):
        big = timed("upscale", lambda: vision.upscale(store, "upscale.realesrgan", image.resize((image.width // 2, image.height // 2))))
        if big is not None:
            big.save(out / "upscale.png")
    report["devices_used"] = pool.last_device
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
