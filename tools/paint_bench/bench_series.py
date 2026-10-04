"""Run several bench cases, clearing the Paint canvas between them with the
engine's own keyboard input (Ctrl+A, Delete) through the same transport."""
import json, subprocess, sys, time, ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)
sys.path.insert(0, '.')
from engine.olegpainter.core import OlegPainter

SP = 'tools/paint_bench'
PY = sys.executable
cases = [tuple(c.split(':')) for c in sys.argv[1:]]


def canvas_blank():
    from PIL import ImageGrab
    import numpy as np
    a = np.asarray(ImageGrab.grab(bbox=(80, 290, 660, 910), all_screens=True).convert('L'))
    return float((a < 245).mean()) < 0.001


def clear_canvas():
    for _attempt in range(3):
        _clear_once()
        if canvas_blank():
            return
    raise SystemExit('canvas could not be cleared')


def _clear_once():
    e = OlegPainter(status_callback=lambda m: None)
    region = (80, 290, 400, 300)
    with e._owned_automation_input('bench clear', target_region=region):
        e._click_abs(700, 600)
        time.sleep(0.2)
        e._input.send_keys('ctrl+a'); time.sleep(0.3)
        e._input.send_keys('delete'); time.sleep(0.3)
        e._input.send_keys('esc'); time.sleep(0.3)
        e._ui_click_at(408, 132, clicks=1)  # back to the Brushes tool (Ctrl+A selects the selection tool)
        time.sleep(0.3)


for image, mode, scale in cases:
    clear_canvas()
    out = subprocess.run([PY, SP + '/bench.py', 'draw', f'{SP}/images/{image}.png', mode, scale],
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
    line = next((l for l in out.stdout.splitlines() if l.startswith('{')), None)
    print(line or ('FAILED ' + image + ' ' + mode + ' ' + out.stderr[-500:]), flush=True)
    if line:
        open('test-results/paint_bench_results.jsonl', 'a', encoding='utf-8').write(line + '\n')
