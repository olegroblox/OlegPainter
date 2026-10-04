"""Single native event boundary, with cancellation between input commands."""
from threading import RLock


class InputCancelled(RuntimeError):
    """The operation was cancelled before another native event was sent."""


def absolute_pixel(value, origin, extent):
    """Aim at the centre of a physical pixel in the 16-bit desktop space."""
    if extent <= 0 or not origin <= value < origin + extent:
        raise ValueError("Координата ввода находится вне рабочего стола.")
    return min(65535, max(0, round((value - origin + .5) * 65536 / extent)))


def _os_button_down(virtual_key: int) -> bool:
    """Logical state of a mouse button (injected presses included); unknown = down."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(int(virtual_key)) & 0x8000)
    except Exception:
        return True


class WindowsInputBackend:
    def __init__(self):
        self._desktop = None
        self._target = None

    def bind_target(self, region, focus=True):
        from infrastructure.automation_target import TargetWindowGuard
        self._target = TargetWindowGuard()
        self._target.bind(region, focus=focus)

    def unbind_target(self):
        self._target = None

    def check_target(self, point=None):
        from infrastructure.automation_target import TargetChanged
        if self._target is None:
            raise TargetChanged("Ввод не привязан к окну рисования.")
        self._target.check(point)

    def reset(self):
        self._desktop = None

    def _send_mouse(self, flags, state, x=0, y=0):
        import interception
        context = interception.inputs._g_context
        if not context.valid:
            raise RuntimeError("Драйвер ввода недоступен.")
        result = context.send(context.mouse, interception.MouseStroke(flags, state, 0, x, y))
        if not result.succeeded:
            raise RuntimeError("Драйвер отклонил команду мыши.")

    def move(self, x, y):
        self.check_target((x, y))
        import ctypes
        from interception.constants import MouseFlag
        if self._desktop is None:
            metrics = ctypes.windll.user32.GetSystemMetrics
            self._desktop = tuple(int(metrics(index)) for index in (76, 77, 78, 79))
        left, top, width, height = self._desktop
        ix, iy = absolute_pixel(int(x), left, width), absolute_pixel(int(y), top, height)
        flags = MouseFlag.MOUSE_MOVE_ABSOLUTE | MouseFlag.MOUSE_VIRTUAL_DESKTOP | MouseFlag.MOUSE_MOVE_NOCOALESCE
        self._send_mouse(int(flags), 0, ix, iy)

    def button(self, button, down):
        if down:
            self.check_target()
            self.check_target(self._target.api.cursor())
        elif button == "right" and not _os_button_down(0x02):
            # The final cleanup releases both buttons; a right release nobody pressed
            # opens the context menu of a browser game (Gartic Phone). Already up = released.
            return
        from interception.constants import MouseButtonFlag
        states = MouseButtonFlag.from_string(button)
        # Relative zero displacement changes only the button, never cursor position.
        self._send_mouse(0, int(states[0 if down else 1]))

    def send_keys(self, sequence):
        self.check_target()
        import keyboard
        keyboard.send(sequence)

    def write_text(self, text):
        self.check_target()
        import keyboard
        keyboard.write(text)

    def key(self, key, down):
        if down:
            self.check_target()
        import keyboard
        (keyboard.press if down else keyboard.release)(key)


class AutomationTransport:
    """No silent fallback on failed injection; releases remain possible on stop.

    Worker ownership is supplied by InputOwnership. Native input is bound to one
    target window; arbitration between different processes is not implemented.
    """
    def __init__(self, cancelled, backend=None):
        self.backend = WindowsInputBackend() if backend is None else backend
        self.cancelled = cancelled
        self.failure = None
        self._lock = RLock()
        self._active = False

    def begin(self, region=None, *, focus=True):
        with self._lock:
            self.failure = None
            self.backend.reset()
            self._active = True
            self._execute(self.backend.bind_target, region, focus)

    def end(self):
        with self._lock:
            self._active = False
            self.backend.unbind_target()

    def check_target(self):
        with self._lock:
            if self._active:
                self._execute(self.backend.check_target)

    def _execute(self, function, *args, release=False):
        with self._lock:
            if not release:
                if self.failure is not None:
                    raise self.failure
                if self.cancelled():
                    raise InputCancelled("Ввод отменён.")
            try:
                return function(*args)
            except Exception as error:
                self.failure = error
                raise

    def move(self, x, y):
        self._execute(self.backend.move, x, y)

    def button_down(self, button="left"):
        self._execute(self.backend.button, button, True)

    def button_up(self, button="left"):
        self._execute(self.backend.button, button, False, release=True)

    def send_keys(self, sequence):
        self._execute(self.backend.send_keys, sequence)

    def write_text(self, text):
        for character in str(text):
            self._execute(self.backend.write_text, character)

    def key_down(self, key):
        self._execute(self.backend.key, key, True)

    def key_up(self, key):
        self._execute(self.backend.key, key, False, release=True)
