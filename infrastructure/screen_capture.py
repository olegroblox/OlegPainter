"""Capture the target desktop without this application's screen tools.

Qt publishes native handles; capture workers never access widgets. Exclusion is
temporary so normal user screenshots still include the tools.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import threading

from PIL import ImageGrab


class CaptureError(RuntimeError):
    pass


class WindowsCaptureExclusion:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowDisplayAffinity.restype = wintypes.BOOL
        self.user.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.dwm.DwmFlush.argtypes = []
        self.dwm.DwmFlush.restype = ctypes.c_long

    def owned(self, handle):
        pid = wintypes.DWORD()
        return bool(self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))) and pid.value == os.getpid()

    def exclude(self, handle):
        if sys.getwindowsversion().build < 19041:
            raise CaptureError("Clean screen capture requires Windows 10 2004 or newer")
        if not self.owned(handle):
            raise CaptureError("Screen tool window changed before capture")
        previous = wintypes.DWORD()
        if not self.user.GetWindowDisplayAffinity(handle, ctypes.byref(previous)):
            raise CaptureError(f"Cannot read screen tool capture mode: {ctypes.get_last_error()}")
        self.restore(handle, 0x11)  # WDA_EXCLUDEFROMCAPTURE (not a black rectangle)
        return previous.value

    def restore(self, handle, affinity):
        if not self.owned(handle):
            return  # destroyed during capture; never modify a foreign window
        if not self.user.SetWindowDisplayAffinity(handle, affinity):
            raise CaptureError(f"Cannot set screen tool capture mode: {ctypes.get_last_error()}")

    def flush(self):
        result = self.dwm.DwmFlush()
        if result < 0:
            raise CaptureError(f"Desktop composition synchronization failed: {result}")


class ScreenCapture:
    def __init__(self, backend=None, grabber=None):
        self._backend = backend
        self._grabber = grabber or ImageGrab.grab
        self._registry_lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._windows = {}
        self._revision = 0

    def register(self, key, handle):
        with self._registry_lock:
            if self._windows.get(key) != handle:
                self._windows[key] = handle
                self._revision += 1

    def unregister(self, key):
        with self._registry_lock:
            if key in self._windows:
                del self._windows[key]
                self._revision += 1

    def grab(self, **kwargs):
        # PIL must interpret bbox in physical virtual-desktop coordinates,
        # including negative origins on monitors left/above the primary.
        kwargs.setdefault("all_screens", True)
        with self._capture_lock:
            with self._registry_lock:
                handles, revision = set(self._windows.values()), self._revision
            if not handles:
                return self._grabber(**kwargs)
            if self._backend is None:
                if os.name != "nt":
                    raise CaptureError("Screen tool exclusion is only implemented on Windows")
                self._backend = WindowsCaptureExclusion()
            changed = []
            try:
                for handle in handles:
                    changed.append((handle, self._backend.exclude(handle)))
                self._backend.flush()
                result = self._grabber(**kwargs)
                with self._registry_lock:
                    if revision != self._revision:
                        raise CaptureError("Screen tools changed during capture; repeat calibration")
                return result
            finally:
                errors = []
                for handle, previous in reversed(changed):
                    try:
                        self._backend.restore(handle, previous)
                    except Exception as exc:
                        errors.append(str(exc))
                if changed:
                    try:
                        self._backend.flush()
                    except Exception as exc:
                        errors.append(str(exc))
                if errors:
                    raise CaptureError("Cannot restore screen tools: " + "; ".join(errors))


screen_capture = ScreenCapture()
capture_screen = screen_capture.grab


def virtual_screen_origin() -> tuple[int, int]:
    """Top-left of the whole desktop in physical px: `capture_screen()` starts there."""
    if os.name != "nt":
        return 0, 0
    metrics = ctypes.windll.user32.GetSystemMetrics
    return int(metrics(76)), int(metrics(77))  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
