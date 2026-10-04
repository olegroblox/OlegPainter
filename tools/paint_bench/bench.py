"""Live Paint benchmark through the real engine and Interception transport.

  bench.py learn                      -> bench/learned.json
  bench.py draw <image> <auto|px1>    -> prints one JSON result line
Paint (test window) must be in front with a blank canvas.
"""
import ctypes, json, math, os, sys, time
ctypes.windll.shcore.SetProcessDpiAwareness(2)
sys.path.insert(0, '.')
import numpy as np
from PIL import Image, ImageGrab
from engine.olegpainter.core import OlegPainter
from engine.olegpainter import input_timing

SP = 'tools/paint_bench'
# A saved session with a trained brush (any autosave works); OLEGPAINTER_BENCH_BASE overrides it.
BASE = os.environ.get('OLEGPAINTER_BENCH_BASE', os.path.join(
    os.environ.get('LOCALAPPDATA', ''), 'OlegPainter', 'maintenance', 'claude-live-20260928', 'autosave-after-timing-test.json'))
LEARNED = SP + '/learned.json'
KEYS = ('dynamic_brush_calibration', 'dynamic_brush_profile', 'dynamic_brush_slider_params', 'dynamic_brush_min_value',
        'dynamic_brush_max_value', 'dynamic_brush_default_value', 'dynamic_brush_step_value', 'draw_delay',
        'pen_max_step', 'area_fill_delay', 'run_length_merge_enabled', 'brush_size', 'dynamic_brush_enabled')


def engine():
    d = json.load(open(BASE, encoding='utf-8'))['payload']
    cfg = dict(d['painter']); cfg.update(d['session']['profiles']['universal::' + cfg.get('drawing_algorithm', 'dfs_4dir')])
    try:
        cfg.update(json.load(open(LEARNED, encoding='utf-8')))
    except FileNotFoundError:
        pass
    cfg['post_draw_repair_enabled'] = True
    import os
    cfg.update(json.loads(os.environ.get('BENCH_SET', '{}')))
    cfg['dynamic_brush_verify_at_draw'] = False
    e = OlegPainter(status_callback=lambda m: None)
    e.load_config(cfg)
    return e


def learn():
    e = engine()
    e.dynamic_brush_slider_params = e.refine_slider_params(e.dynamic_brush_slider_params)
    zone = tuple(e.dynamic_brush_scratch_zone)
    with e._owned_automation_input('bench learn', target_region=zone):
        ok = e.learn_dynamic_brush_profile()
        table = e._dynamic_brush_v2_radius_table()
        e._apply_dynamic_brush_value(float(table[1][0]), force=True)
        timing = input_timing.learn(e, zone, brush_radius_px=float(table[0][0]))
    cell = e.dynamic_brush_recommended_cell()
    if cell:
        e.brush_size = cell
    if timing:
        e.draw_delay, e.pen_max_step, e.area_fill_delay = timing['draw_delay'], timing['pen_max_step'], 0.0
        e.run_length_merge_enabled = bool(timing['interpolates']) or e.run_length_merge_enabled
    cfg = e.get_config()
    json.dump({k: cfg[k] for k in KEYS}, open(LEARNED, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    radii = [round(float(r), 2) for r in table[0]]
    print(json.dumps(dict(ok=ok, slider=e.dynamic_brush_slider_params, radii=radii, cell=e.brush_size, timing=timing)))


def draw(image, mode):
    e = engine()
    src = Image.open(image)
    src = src.convert('RGBA') if 'A' in src.getbands() else src.convert('RGB')  # keep transparency: it is the background
    scale = float(sys.argv[4]) if len(sys.argv) > 4 else 2
    w, h = int(src.width * scale), int(src.height * scale)
    if max(w, h) > 600:
        k = 600 / max(w, h); w, h = int(w * k), int(h * k)
    import os
    ox, oy = json.loads(os.environ.get('BENCH_ORIGIN', '[80, 290]'))
    region = (ox, oy, w, h)
    if mode == 'px1':
        e.dynamic_brush_enabled = False
        e.brush_size = 1
    e.draw_region = region
    e.set_image_from_pil(src, image)
    e.draw_region = region
    e.prepare_image_and_palette()
    b = max(1, int(e.brush_size))
    cm = np.asarray(e.cluster_map)
    lut = {cid: [int(hx[i:i + 2], 16) for i in (1, 3, 5)] for hx, cid, _, _ in e.color_palette}
    exp = np.full(cm.shape + (3,), 255, np.uint8)
    for cid, rgb in lut.items():
        exp[cm == cid] = rgb
    exp = np.repeat(np.repeat(exp, b, 0), b, 1)[:h, :w]
    e.drawing_enabled = True
    e.stop_flag = False
    e.dynamic_brush_session_active = False
    if mode == 'auto':
        e.prepare_dynamic_brush_for_draw()
    t0 = time.perf_counter()
    if mode == 'px1':
        with e._owned_automation_input('bench size', target_region=region):
            table = e._dynamic_brush_v2_radius_table()
            e._apply_dynamic_brush_value(float(table[1][0]), force=True)
    events = []
    e.drawing_event_callback = events.append
    prof = {'moves': 0, 'downs': 0, 'keys': 0, 'picks': 0, 'pick_s': 0.0, 'sizes': 0, 'size_s': 0.0,
            'sleep_s': 0.0, 'sleep_n': 0}
    real_sleep = time.sleep
    def counting_sleep(t):
        prof['sleep_s'] += max(0.0, float(t)); prof['sleep_n'] += 1
        real_sleep(t)
    time.sleep = counting_sleep
    tr = e._input
    for name, key in (('move', 'moves'), ('button_down', 'downs'), ('send_keys', 'keys')):
        orig = getattr(tr, name)
        def wrapped(*a, _o=orig, _k=key, **k):
            prof[_k] += 1
            return _o(*a, **k)
        setattr(tr, name, wrapped)
    for name, key in (('pick_color', 'picks'), ('_apply_dynamic_brush_value', 'sizes')):
        orig = getattr(e, name)
        def timed(*a, _o=orig, _k=key, **k):
            t = time.perf_counter()
            try:
                return _o(*a, **k)
            finally:
                prof[_k] += 1; prof[_k.rstrip('s') + '_s' if _k != 'sizes' else 'size_s'] += time.perf_counter() - t
        setattr(e, name, timed)
    import os
    pick_wait = float(os.environ.get('BENCH_PICK_WAIT', '0'))
    if pick_wait:
        orig_pick = e.pick_color
        def waited_pick(*a, **k):
            r = orig_pick(*a, **k)
            real_sleep(pick_wait)
            return r
        e.pick_color = waited_pick
    if os.environ.get('BENCH_PROFILE'):
        import cProfile, pstats, io
        pr = cProfile.Profile(); pr.enable()
        e.draw_colors_thread(e.drawing_run_id)
        pr.disable()
        out = io.StringIO(); pstats.Stats(pr, stream=out).sort_stats('tottime').print_stats(30)
        open('test-results/paint_bench_profile.txt', 'w', encoding='utf-8').write(out.getvalue())
    else:
        e.draw_colors_thread(e.drawing_run_id)
    elapsed = time.perf_counter() - t0
    time.sleep = real_sleep
    time.sleep(0.4)
    shot = np.asarray(ImageGrab.grab(bbox=(region[0], region[1], region[0] + exp.shape[1], region[1] + exp.shape[0]),
                                     all_screens=True).convert('RGB')).astype(int)
    diff = np.abs(shot - exp.astype(int)).sum(-1) > 30
    name = image.split('/')[-1].split('.')[0]
    vis = np.concatenate([exp, shot.astype(np.uint8), np.where(diff[..., None], np.array([255, 0, 255], np.uint8), np.uint8(255))], 1)
    Image.fromarray(vis).save(f'test-results/paint_bench_result_{name}_{mode}.png')
    print(json.dumps(dict(image=name, mode=mode, scale=scale, seconds=round(elapsed, 1), cell=b, colors=len(e.color_palette),
                          match=round(100 - diff.mean() * 100, 2), wrong=int(diff.sum()), px=int(diff.size),
                          phase=str(events[-1].phase) if events else None,
                          profile={k: (round(v, 1) if isinstance(v, float) else v) for k, v in prof.items()})))


if __name__ == '__main__':
    if sys.argv[1] == 'learn':
        learn()
    else:
        draw(sys.argv[2], sys.argv[3])
