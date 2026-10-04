"""Read-only native input ownership; never access Qt widgets from a mouse hook."""
import os


def is_external_capture_point(x, y):
    """Do not calibrate on this application's own buttons or dialogs.

    WindowFromPoint performs native hit testing, including click-through layered
    windows. Only the process identifier is read; no foreign window is modified.
    """
    import win32gui
    import win32process

    handle = win32gui.WindowFromPoint((int(x), int(y)))
    if not handle:
        return False
    _, pid = win32process.GetWindowThreadProcessId(handle)
    return pid != os.getpid()
