"""One cursor-free window sample in a disposable, time-bounded native process."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from infrastructure.automation_target import WindowIdentity, WindowsTargetAPI


class WindowSampleError(RuntimeError):
    pass


class SampleCancelled(WindowSampleError):
    pass


@contextmanager
def physical_coordinates():
    import ctypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise WindowSampleError("Не удалось включить физические координаты захвата.")
    try:
        yield
    finally:
        user.SetThreadDpiAwarenessContext(previous)


class WindowSampleAPI(WindowsTargetAPI):
    def rect(self, handle):
        with physical_coordinates():
            return super().rect(handle)

    def frame_bounds(self, handle):
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        if dwm.DwmGetWindowAttribute(handle, 9, ctypes.byref(rect), ctypes.sizeof(rect)) < 0:
            raise WindowSampleError("Не удалось определить границы кадра окна.")
        return rect.left, rect.top, rect.right, rect.bottom


@dataclass(frozen=True)
class WindowSampleRequest:
    identity: WindowIdentity
    bounds: tuple
    frame_bounds: tuple
    point: tuple

    def validate(self, api):
        handle = self.identity.handle
        if not api.available(handle) or api.identity(handle) != self.identity:
            raise WindowSampleError("Окно образца закрыто или заменено. Повторите выбор цвета.")
        if tuple(api.rect(handle)) != self.bounds or tuple(api.frame_bounds(handle)) != self.frame_bounds:
            raise WindowSampleError("Окно переместилось или изменило размер. Повторите выбор цвета.")
        left, top, right, bottom = self.frame_bounds
        x, y = self.point
        if not left <= x < right or not top <= y < bottom:
            raise WindowSampleError("Точка образца находится вне содержимого окна.")

    @classmethod
    def at(cls, x, y, *, api=None):
        api = WindowSampleAPI() if api is None else api
        handle = api.root(api.at((int(x), int(y))))
        if not handle:
            raise WindowSampleError("В выбранной точке нет окна.")
        identity = api.identity(handle)
        if identity.process_id == os.getpid() or identity.class_name in {"Progman", "WorkerW", "Shell_TrayWnd"}:
            return None
        request = cls(identity, tuple(api.rect(handle)), tuple(api.frame_bounds(handle)), (int(x), int(y)))
        request.validate(api)
        return request

    @classmethod
    def from_payload(cls, payload):
        return cls(WindowIdentity(**payload["identity"]), tuple(payload["bounds"]),
                   tuple(payload["frame_bounds"]), tuple(payload["point"]))


def pixel_from_frame(buffer, request):
    left, top, right, bottom = request.frame_bounds
    if buffer.shape != (bottom - top, right - left, 4):
        raise WindowSampleError("Размер кадра не совпал с физическими границами окна.")
    x, y = request.point
    if not left <= x < right or not top <= y < bottom:
        raise WindowSampleError("Точка образца находится вне кадра.")
    if int(buffer[y - top, x - left, 3]) != 255:
        raise WindowSampleError("Образец окна прозрачен. Выберите непрозрачный участок цвета.")
    return tuple(int(channel) for channel in buffer[y - top, x - left, :3][::-1])


def capture_window_pixel(request):
    return capture_window_sample(request, pixel_from_frame)


def capture_window_sample(request, read_frame):
    """Runs only in the worker process; no Qt and no mouse/keyboard hooks."""
    from windows_capture import WindowsCapture
    api = WindowSampleAPI()
    request.validate(api)
    result, failures = [], []
    capture = WindowsCapture(window_hwnd=request.identity.handle, cursor_capture=False,
                             draw_border=True, secondary_window=False)

    @capture.event
    def on_frame_arrived(frame, control):
        try:
            request.validate(api)
            rgb = read_frame(frame.frame_buffer, request)
            request.validate(api)
            result.append(rgb)
        except Exception as error:
            failures.append(error)
        finally:
            control.stop()

    @capture.event
    def on_closed():
        if not result:
            failures.append(WindowSampleError("Окно закрылось до получения образца."))

    capture.start()
    if failures:
        raise failures[0]
    if not result:
        raise WindowSampleError("Не получен кадр целевого окна.")
    return result[0]


def sample_window_pixel(request, cancelled, *, timeout=3.0, command=None):
    result = sample_window_json(asdict(request), cancelled, timeout=timeout, command=command)
    rgb = result.get("rgb")
    if not isinstance(rgb, list) or len(rgb) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in rgb):
        raise WindowSampleError("Измеритель передал некорректный RGB.")
    return tuple(rgb)


def sample_window_json(request_payload, cancelled, *, timeout=3.0, command=None, max_response=16384):
    """Called by the capture worker thread; cancellation never joins from Qt."""
    if cancelled.is_set():
        raise SampleCancelled("Измерение отменено.")
    if command is None:
        command = ([sys.executable, "--window-pixel-worker"] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "infrastructure.window_sample_worker"])
    payload = json.dumps(request_payload).encode("utf-8")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=Path(__file__).resolve().parents[1],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancelled.is_set():
                raise SampleCancelled("Измерение отменено.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WindowSampleError("Окно не передало цвет вовремя. Повторите захват.")
            try:
                output, errors = process.communicate(payload, timeout=min(.05, remaining))
                break
            except subprocess.TimeoutExpired:
                payload = None
        if cancelled.is_set():
            raise SampleCancelled("Измерение отменено.")
        if len(output) > max_response:
            raise WindowSampleError("Получен некорректный ответ измерителя.")
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError) as error:
            raise WindowSampleError("Измеритель окна завершился без результата: " + errors.decode("utf-8", "replace")[-500:]) from error
        if not isinstance(result, dict):
            raise WindowSampleError("Получен некорректный ответ измерителя.")
        if process.returncode or "error" in result:
            raise WindowSampleError("Не удалось измерить цвет: " + str(result.get("error", process.returncode)))
        return result
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def run_worker():
    try:
        payload = json.loads(sys.stdin.buffer.read(16384))
        if payload.get("kind") == "palette":
            from infrastructure.palette_sampling import PaletteSampleRequest, palette_from_frame
            request = PaletteSampleRequest.from_payload(payload)
            response = capture_window_sample(request, palette_from_frame)
        else:
            request = WindowSampleRequest.from_payload(payload)
            response = {"rgb": capture_window_pixel(request)}
        code = 0
    except Exception as error:
        response, code = {"error": str(error)}, 1
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return code


def dispatch_worker():
    if sys.argv[1:] == ["--window-pixel-worker"]:
        raise SystemExit(run_worker())
