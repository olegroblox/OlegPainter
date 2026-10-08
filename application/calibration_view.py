"""What the program will click, as data for the on-screen check (CALIB-VIEW-001).

Qt-free: the shell passes the result to DesktopOverlayController.show_calibration.
Item kinds are the ones CalibFlashOverlay draws: point, circle, line, rect.
"""
from __future__ import annotations


def _point(items, coord, label):
    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
        items.append({"type": "point", "x": int(coord[0]), "y": int(coord[1]), "label": label})


def _rect(items, rect, label=""):
    if isinstance(rect, (list, tuple)) and len(rect) >= 4 and rect[2] > 0 and rect[3] > 0:
        items.append({"type": "rect", "x": int(rect[0]), "y": int(rect[1]),
                      "w": int(rect[2]), "h": int(rect[3]), "label": label})


def area_rect(service):
    """The canvas outline in physical desktop pixels, or None."""
    try:
        region = service.get_viewport_state().draw_region_desktop_px
    except Exception:
        return None
    if isinstance(region, (list, tuple)) and len(region) >= 4:
        return [int(value) for value in region[:4]]
    return None


def outline_fill(service):
    try:
        settings = service.get_outline_fill_settings() or {}
    except Exception:
        settings = {}
    coord = lambda value: [int(v) for v in value[:2]] if isinstance(value, (list, tuple)) and len(value) >= 2 else None
    return dict(mode=str(settings.get("mode") or "keys"),
                brush_key=str(settings.get("brush_key") or ""), fill_key=str(settings.get("fill_key") or ""),
                brush_coord=coord(settings.get("brush_coord")), fill_coord=coord(settings.get("fill_coord")))


def items(service):
    """Everything the current setup uses: canvas, colour method, brush, layers, actions."""
    engine = service.engine
    result = []
    _rect(result, area_rect(service), "Холст")
    method = str(getattr(engine, "color_picking_method", "") or "")
    if method == "hex_field":
        _point(result, getattr(engine, "hex_input_coord", None), "HEX")
    elif method == "hsv_palette":
        circle = getattr(engine, "circle_params_calib", None)
        if isinstance(circle, (list, tuple)) and len(circle) >= 3:
            result.append({"type": "circle", "x": int(circle[0]), "y": int(circle[1]), "r": int(circle[2]),
                           "label": "Круг"})
        slider = getattr(engine, "slider_params_calib", None)
        if isinstance(slider, (list, tuple)) and len(slider) >= 4:
            fixed, one, zero = int(slider[1]), int(slider[2]), int(slider[3])
            if str(slider[0] or "").lower() == "vertical":
                result.append({"type": "line", "x1": fixed, "y1": one, "x2": fixed, "y2": zero, "label": "Яркость"})
            else:
                result.append({"type": "line", "x1": zero, "y1": fixed, "x2": one, "y2": fixed, "label": "Яркость"})
    elif method == "manual_palette":
        for index, entry in enumerate(list(getattr(engine, "manual_palette_coords", None) or [])[:64]):
            if isinstance(entry, dict):
                _point(result, (entry.get("x"), entry.get("y")) if entry.get("x") is not None else None, str(index + 1))
            else:
                _point(result, entry, str(index + 1))
    elif method == "wheel_square":
        wheel = getattr(engine, "wheel_square_calib", None)
        if isinstance(wheel, dict) and wheel.get("centre") and wheel.get("square"):
            cx, cy = (int(round(v)) for v in wheel["centre"])
            radius = int(round((float(wheel["r_in"]) + float(wheel["r_out"])) / 2))
            result.append({"type": "circle", "x": cx, "y": cy, "r": radius, "label": "Кольцо"})
            left, top, right, bottom = (int(v) for v in wheel["square"])
            _rect(result, (left, top, right - left, bottom - top), "Квадрат")
    elif method == "screen_palette":
        calib = getattr(engine, "screen_palette_calib", None)
        coords = calib.get("coords") if isinstance(calib, dict) else None
        points = [(int(c[0]), int(c[1])) for c in (coords if coords is not None else [])
                  if hasattr(c, "__len__") and len(c) >= 2]
        if points:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            _rect(result, (min(xs) - 8, min(ys) - 8, max(xs) - min(xs) + 16, max(ys) - min(ys) + 16), "Палитра")
    try:
        brush = engine.get_dynamic_brush_settings()
    except Exception:
        brush = {}
    if brush.get("enabled"):
        result += brush_items(engine)
    if getattr(engine, "draw_with_layers_enabled", False):
        for index, coord in enumerate(list(getattr(engine, "target_app_layer_coords", None) or [])[:30]):
            _point(result, coord, f"Слой {index + 1}")
    for slot, label in (("pre", "До цвета"), ("post", "После цвета")):
        try:
            if not engine._should_play_actions(slot):
                continue
            events = engine._serialize_actions(slot)
        except Exception:
            continue
        for number, event in enumerate(e for e in events if isinstance(e, dict) and e.get("x") is not None):
            _point(result, (event.get("x"), event.get("y")), f"{label} {number + 1}")
    if resolve_route(engine) == "outline_and_fill":
        tools = outline_fill(service)
        if tools["mode"] == "coords":
            _point(result, tools["brush_coord"], "Кисть")
            _point(result, tools["fill_coord"], "Заливка")
    return result


def brush_items(engine):
    """The brush size control and the test spot as learning will use them: shown
    right after they are picked, so a wrong frame is seen before learning clicks."""
    try:
        brush = engine.get_dynamic_brush_settings()
    except Exception:
        return []
    result = []
    mode = str(brush.get("control_mode") or "text")
    if mode == "slider":
        slider = brush.get("slider_params")
        if isinstance(slider, (list, tuple)) and len(slider) >= 4:
            fixed, one, zero = (int(round(float(value))) for value in slider[1:4])
            if str(slider[0] or "").lower() == "vertical":
                result.append({"type": "line", "x1": fixed, "y1": one, "x2": fixed, "y2": zero, "label": "Ползунок кисти"})
            else:
                result.append({"type": "line", "x1": zero, "y1": fixed, "x2": one, "y2": fixed, "label": "Ползунок кисти"})
    elif mode == "points":
        for point in brush.get("points") or []:
            if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None:
                _point(result, (point["x"], point["y"]), f"Размер {float(point.get('value', 0)):g}")
    else:
        _point(result, brush.get("coord"), "Размер")
    _rect(result, brush.get("scratch_zone"), "Пробы")
    return result


def resolve_route(engine):
    return str(getattr(engine, "drawing_algorithm", "") or "")
