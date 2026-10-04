"""Re-learn only the input pacing (turn dwell + lift pause) and store it in bench/learned.json."""
import json, sys, time, ctypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)
sys.path.insert(0, '.')
SP = 'tools/paint_bench'
sys.path.insert(0, SP)
import bench
from engine.olegpainter import input_timing
e = bench.engine()
zone = tuple(e.dynamic_brush_scratch_zone)
with e._owned_automation_input('bench timing', target_region=zone):
    table = e._dynamic_brush_v2_radius_table()
    e._apply_dynamic_brush_value(float(table[1][0]), force=True)
    e._dynamic_brush_apply_calibration_color(color_hex='#121212')
    t = input_timing.learn(e, zone, brush_radius_px=float(table[0][0]), status=print)
print(json.dumps(t))
if t:
    d = json.load(open(bench.LEARNED, encoding='utf-8'))
    d.update(draw_delay=t['draw_delay'], pen_max_step=t['pen_max_step'])
    if 'lift_pause' in t:
        d.update(pen_settle_delay=t['lift_pause'], mouse_release_settle=t['lift_pause'], pen_button_delay=0.0)
    json.dump(d, open(bench.LEARNED, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
