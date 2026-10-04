from .common import *  # noqa
from infrastructure.screen_capture import capture_screen


def calib_grab_screen_ex():
    """Grab the monitor under the cursor.

    Returns ``(img_bgr, offset_x, offset_y)`` where ``(offset_x, offset_y)`` is the
    top-left corner of the grabbed monitor in **virtual-desktop (physical) pixels**.

    The offset is what was historically dropped: cv2 click coordinates are relative
    to the grabbed image (0-based from the monitor corner), but everything that later
    *clicks* those coordinates (``_click_abs``) works in virtual-desktop space. Adding
    the offset back makes calibration correct on secondary monitors and when the
    primary monitor is not at (0, 0).
    """
    try:
        if os.name == 'nt':
            user32 = ctypes.windll.user32
            MONITOR_DEFAULTTONEAREST = 2
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong),
                            ("rcMonitor", RECT),
                            ("rcWork", RECT),
                            ("dwFlags", ctypes.c_ulong)]
            mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                l, t, r, b = mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom
                img_pil = capture_screen(bbox=(l, t, r, b))
                img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
                return img, int(l), int(t)

        # fallback (не Windows): захватываем весь виртуальный рабочий стол.
        img_pil = capture_screen(all_screens=True)
        img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
        ox, oy = 0, 0
        try:
            if os.name == 'nt':
                user32 = ctypes.windll.user32
                ox = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
                oy = int(user32.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
        except Exception:
            ox, oy = 0, 0
        return img, ox, oy
    except Exception:
        return None, 0, 0


def calib_grab_screen():
    """Backward-compatible wrapper: returns only the image (offset dropped).

    Callers that click captured coordinates should use :func:`calib_grab_screen_ex`
    and forward the offset into the ``calib_select_*`` helpers below.
    """
    img, _ox, _oy = calib_grab_screen_ex()
    return img


def calib_show_fullscreen_top(win_name, img, painter_instance_log_func):
    # Поведение окна намеренно идентично рабочему select_area (WINDOW_NORMAL+TOPMOST):
    # cv2 на этом сетапе отдаёт координаты в пространстве изображения 1:1, поэтому
    # масштабирования нет. Единственная правка координат — добавление offset монитора
    # делается в вызывающих функциях ниже (как abs_x = x + offset_x в select_area).
    if img is None: return
    try:
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(win_name, cv2.WND_PROP_TOPMOST, 1)
        cv2.imshow(win_name, img)
        cv2.waitKey(1)
        if painter_instance_log_func: painter_instance_log_func(f"Окно '{win_name}' отображено для калибровки.")
    except Exception as e:
        if painter_instance_log_func: painter_instance_log_func(f"Ошибка отображения '{win_name}': {e}", is_error=True)


def calib_select_two_points(img, win_title, painter_instance_log_func, offset=(0, 0)):
    """Pick two points on the grabbed monitor.

    Returns the two points in **virtual-desktop coordinates** (the monitor offset is
    added back). With ``offset=(0, 0)`` the legacy monitor-local behaviour is kept,
    so callers that have not been updated are unaffected.
    """
    points_selected = []
    img_copy = img.copy()
    unique_win_title = f"OlegPainter_Calib_TwoPoints_{win_title}"

    calib_show_fullscreen_top(unique_win_title, img_copy, painter_instance_log_func)

    def on_mouse_calib_twopoints(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN and len(points_selected) < 2:
            points_selected.append((x, y))
            cv2.circle(img_copy, (x, y), 8, (0, 255, 0) if len(points_selected) == 1 else (0, 0, 255), -1)
            cv2.putText(img_copy, f"P{len(points_selected)}", (x + 10, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (255, 255, 255), 2)
            cv2.imshow(unique_win_title, img_copy)

    cv2.setMouseCallback(unique_win_title, on_mouse_calib_twopoints)
    if painter_instance_log_func: painter_instance_log_func(
        f"В окне '{unique_win_title}': выберите 2 точки. ESC-отмена.")

    while True:
        if len(points_selected) == 2: break
        key = cv2.waitKey(20) & 0xFF
        if key == 27: points_selected.clear(); break
        try:
            if cv2.getWindowProperty(unique_win_title, cv2.WND_PROP_VISIBLE) < 1:
                points_selected.clear();
                break
        except cv2.error:
            points_selected.clear();
            break
    cv2.destroyWindow(unique_win_title)
    if len(points_selected) != 2:
        return None
    try:
        ox, oy = int(offset[0]), int(offset[1])
    except Exception:
        ox, oy = 0, 0
    return tuple((px + ox, py + oy) for (px, py) in points_selected)


def calib_select_region(img, win_title, painter_instance_log_func, offset=(0, 0)):
    """Drag a rectangle on the grabbed monitor.

    Returns ``(x, y, w, h)`` with ``(x, y)`` in **virtual-desktop coordinates** (the
    monitor offset is added back; ``w``/``h`` are sizes and stay unchanged). With
    ``offset=(0, 0)`` the legacy monitor-local behaviour is kept.
    """
    rect_state = {'drawing': False, 'done': False, 'x0': 0, 'y0': 0, 'x1': 0, 'y1': 0}
    img_orig_copy = img.copy()
    img_disp = img_orig_copy.copy()
    unique_win_title_region = f"OlegPainter_Calib_Region_{win_title}"

    calib_show_fullscreen_top(unique_win_title_region, img_disp, painter_instance_log_func)

    def on_mouse_calib_region(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            rect_state.update(x0=x, y0=y, x1=x, y1=y, drawing=True, done=False)
            img_disp = img_orig_copy.copy()
            cv2.imshow(unique_win_title_region, img_disp)
        elif event == cv2.EVENT_MOUSEMOVE and rect_state['drawing']:
            img_disp = img_orig_copy.copy()
            cv2.rectangle(img_disp, (rect_state['x0'], rect_state['y0']), (x, y), (0, 255, 0), 2)
            cv2.imshow(unique_win_title_region, img_disp)
            rect_state.update(x1=x, y1=y)
        elif event == cv2.EVENT_LBUTTONUP and rect_state['drawing']:
            rect_state['drawing'] = False
            rect_state['done'] = True
            rect_state.update(x1=x, y1=y)

    cv2.setMouseCallback(unique_win_title_region, on_mouse_calib_region)
    if painter_instance_log_func: painter_instance_log_func(
        f"В окне '{unique_win_title_region}': выделите область. ESC-отмена.")

    while True:
        if rect_state['done']: break
        key = cv2.waitKey(20) & 0xFF
        if key == 27: rect_state['done'] = False; break
        try:
            if cv2.getWindowProperty(unique_win_title_region, cv2.WND_PROP_VISIBLE) < 1:
                rect_state['done'] = False;
                break
        except cv2.error:
            rect_state['done'] = False;
            break
    cv2.destroyWindow(unique_win_title_region)

    if rect_state['done']:
        xs = min(rect_state['x0'], rect_state['x1'])
        ys = min(rect_state['y0'], rect_state['y1'])
        w = abs(rect_state['x0'] - rect_state['x1'])
        h = abs(rect_state['y0'] - rect_state['y1'])
        if w > 0 and h > 0:
            try:
                ox, oy = int(offset[0]), int(offset[1])
            except Exception:
                ox, oy = 0, 0
            return xs + ox, ys + oy, w, h
    return None
