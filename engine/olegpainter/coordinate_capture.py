"""Coordinate capture drafts and atomic publication under the input session."""
import time

from .translations import tr


def _listen(engine, attribute, callback):
    from .core import mouse
    try:
        listener = engine._capture_session.listener(mouse.Listener, on_click=callback)
        setattr(engine, attribute, listener)
        listener.start()
    except Exception as error:
        engine._capture_failed(error)
        raise


def start_hex(engine):
    from .core import mouse
    if engine.drawing_enabled:
        engine.status_callback(tr("status_error_cannot_define_hex_drawing"))
        return
    if engine.is_waiting_for_hex_click:
        engine.status_callback(tr("status_error_already_waiting_hex"))
        return
    engine._begin_capture_session("координата HEX-поля")
    session = engine._capture_session
    engine.is_waiting_for_hex_click = True
    engine.status_callback(tr("status_waiting_hex_click"))

    def on_click(x, y, button, pressed):
        if not pressed or button != mouse.Button.left:
            return
        point = engine._capture_point(fallback=(int(x), int(y)))
        def commit():
            session.close()
            engine.hex_input_coord = point
            engine.is_waiting_for_hex_click = False
            if engine.hex_coord_set_callback:
                engine.hex_coord_set_callback(point)
            else:
                engine.status_callback(tr("status_hex_coords_set_status", coords=point))
        session.publish(commit)

    _listen(engine, "mouse_listener_hex", on_click)


def start_outline(engine):
    from .core import mouse
    if engine.drawing_enabled:
        engine.status_callback(tr("status_error_outline_fill_capture_drawing"))
        return
    if engine.is_capturing_outline_fill_coords:
        engine.status_callback(tr("status_outline_fill_capture_in_progress"))
        return
    engine._begin_capture_session("координаты инструментов заливки")
    session = engine._capture_session
    draft = {}
    requires_fill = engine._outline_fill_requires_fill_tool()
    engine.is_capturing_outline_fill_coords = True
    engine._outline_fill_capture_step = "brush"
    engine.status_callback(tr("status_outline_fill_wait_brush"))

    def on_click(x, y, button, pressed):
        if not pressed or button != mouse.Button.left:
            return
        point = engine._capture_point(fallback=(int(x), int(y)))
        def commit():
            step = engine._outline_fill_capture_step
            draft[step] = point
            if step == "brush" and requires_fill:
                engine._outline_fill_capture_step = "fill"
                engine.status_callback(tr("status_outline_fill_wait_fill"))
                return
            session.close()
            engine.outline_fill_brush_coord = draft["brush"]
            if requires_fill:
                engine.outline_fill_fill_coord = draft["fill"]
            engine.is_capturing_outline_fill_coords = False
            engine._outline_fill_capture_step = None
            engine.mouse_listener_outline_fill = None
            engine._notify_outline_fill_settings_changed()
            engine.status_callback(tr("status_outline_fill_coords_saved"))
        session.publish(commit)

    _listen(engine, "mouse_listener_outline_fill", on_click)


def layer_points(engine):
    if engine.is_capturing_app_layer_coords:
        return list(engine._layer_capture_draft)
    return list(engine.target_app_layer_coords)


def start_layers(engine):
    from .core import mouse
    if engine.is_capturing_app_layer_coords:
        engine.status_callback(tr("status_already_capturing_app_layers"))
        return
    engine._begin_capture_session("координаты слоёв")
    session = engine._capture_session
    engine._layer_capture_draft = []
    engine.is_capturing_app_layer_coords = True
    engine.ignore_clicks_until = time.time() + .25
    engine._notify_layers_changed()
    engine.status_callback(tr("status_app_layer_capture_start", max_layers=engine.max_definable_app_layers))

    def on_click(x, y, button, pressed):
        if not pressed or button != mouse.Button.left or time.time() < engine.ignore_clicks_until:
            return
        if isinstance(engine.app_window_rect, (tuple, list)) and len(engine.app_window_rect) == 4:
            x1, y1, x2, y2 = engine.app_window_rect
            if x1 <= int(x) <= x2 and y1 <= int(y) <= y2:
                return
        point = engine._capture_point(fallback=(int(x), int(y)))
        def commit():
            engine._layer_capture_draft.append(point)
            count = len(engine._layer_capture_draft)
            engine._notify_layers_changed()
            engine.status_callback(tr("status_app_layer_captured", count=count))
            if count >= engine.max_definable_app_layers:
                finish_layers(engine)
        session.publish(commit)

    _listen(engine, "mouse_listener_app_layer", on_click)


def finish_layers(engine):
    if not engine.is_capturing_app_layer_coords:
        return
    session = engine._capture_session
    def commit():
        session.close()
        engine.target_app_layer_coords = list(engine._layer_capture_draft)
        engine._layer_capture_draft = []
        engine.is_capturing_app_layer_coords = False
        engine.mouse_listener_app_layer = None
        engine._notify_layers_changed()
        engine.status_callback(tr("status_app_layer_capture_end", count=len(engine.target_app_layer_coords)))
    session.publish(commit)
