"""Manual palette capture: quick native callbacks, bounded window measurements."""
import time

from infrastructure.capture_work import CaptureWorkQueue
from infrastructure.window_sampling import WindowSampleRequest, sample_window_pixel
from .translations import tr


def start_palette_capture(engine):
    from .core import mouse
    if engine.is_capturing_manual_palette:
        engine.status_callback(tr("status_manual_palette_already"))
        return
    if len(engine.manual_palette_coords) >= engine.max_manual_palette_colors:
        engine.status_callback("error: Палитра заполнена. Удалите ненужный образец перед добавлением.")
        return
    engine._begin_capture_session("захват палитры")
    session = engine._capture_session
    engine.is_capturing_manual_palette = True
    engine.ignore_clicks_until = time.time() + .25
    engine._manual_palette_last_click_at = 0.0
    engine._manual_palette_last_click_pos = None
    engine._invalidate_manual_palette_cache()
    engine._notify_manual_palette_changed()
    selected_points = {(item["x"], item["y"]) for item in engine.manual_palette_coords}

    def publish(request, rgb):
        x, y = request.point
        entry = dict(x=x, y=y, rgb=list(rgb), hex="#{:02X}{:02X}{:02X}".format(*rgb))
        existing = next((i for i, item in enumerate(engine.manual_palette_coords)
                         if isinstance(item, dict) and item.get("x") == x and item.get("y") == y), None)
        if existing is None:
            engine.manual_palette_coords.append(entry)
        else:
            engine.manual_palette_coords[existing] = entry
        engine._invalidate_manual_palette_cache()
        engine._notify_manual_palette_changed()
        count = len(engine.manual_palette_coords)
        engine._log(f"Manual palette color {count}: ({x},{y}) -> {entry['hex']}")
        engine.status_callback(tr("status_manual_palette_captured", count=count, x=x, y=y))
        if count >= engine.max_manual_palette_colors:
            work.finish()

    def on_click(x, y, button, pressed):
        if not pressed or button != mouse.Button.left or time.time() < engine.ignore_clicks_until:
            return
        point = int(x), int(y)
        now = time.monotonic()
        last = engine._manual_palette_last_click_pos
        if (last is not None and now - engine._manual_palette_last_click_at < .18
                and max(abs(point[0] - last[0]), abs(point[1] - last[1])) <= 3):
            return
        try:
            request = WindowSampleRequest.at(*point)
            if request is None:
                return
            if point not in selected_points and len(selected_points) >= engine.max_manual_palette_colors:
                raise RuntimeError("Все места палитры уже заняты выбранными образцами.")
            if not work.submit(request):
                return
            selected_points.add(point)
        except Exception as error:
            engine.status_callback("Не удалось выбрать образец: " + str(error))
            return
        engine._manual_palette_last_click_at = now
        engine._manual_palette_last_click_pos = point
        engine.status_callback("Считываю цвет выбранного окна…")

    try:
        work = CaptureWorkQueue(session, sample_window_pixel, publish,
                                engine._finish_manual_palette_capture, engine._capture_failed)
        engine._palette_capture_work = work
        engine.mouse_listener_manual_palette = session.listener(mouse.Listener, on_click=on_click)
        engine.mouse_listener_manual_palette.start()
    except Exception:
        session.close()
        engine.is_capturing_manual_palette = False
        engine._notify_manual_palette_changed()
        raise
    engine._log("Starting asynchronous manual palette capture.")
    engine.status_callback(tr("status_manual_palette_capture_start", max_colors=engine.max_manual_palette_colors))
