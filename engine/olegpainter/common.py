# Auto-generated from monolith: shared imports and platform setup

import ctypes
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

import time
import threading
import numpy as np
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
import keyboard
import interception
import cv2
from sklearn.cluster import KMeans
import traceback
import math
import colorsys
import json
import random

try:
    from pynput import mouse
    PYNPUT_AVAILABLE = True
except ImportError:
    mouse = None
    PYNPUT_AVAILABLE = False
from ctypes import wintypes

try:
    _user32 = ctypes.WinDLL('user32', use_last_error=True)

    _GetWindowLongW = _user32.GetWindowLongW
    _GetWindowLongW.restype = wintypes.LONG
    _GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]

    _SetWindowLongW = _user32.SetWindowLongW
    _SetWindowLongW.restype = wintypes.LONG
    _SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]

    _SetLayeredWindowAttributes = _user32.SetLayeredWindowAttributes
    _SetLayeredWindowAttributes.restype = wintypes.BOOL
    _SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]

    _SetWindowPos = _user32.SetWindowPos
    _SetWindowPos.restype = wintypes.BOOL
    _SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT
    ]
    HWND_TOPMOST = -1
    SWP_SHOWWINDOW = 0x0040
    SWP_NOACTIVATE = 0x0010

except Exception as e:
    logging.error(f"Не удалось загрузить функции user32 через ctypes: {e}")
    _user32 = None
    _GetWindowLongW = lambda *args: 0
    _SetWindowLongW = lambda *args: 0
    _SetLayeredWindowAttributes = lambda *args: 0
    _SetWindowPos = lambda *args: 0

APP_DATA_DIR = os.path.join(os.path.expanduser("~"), ".oleg_painter")
os.makedirs(APP_DATA_DIR, exist_ok=True)
