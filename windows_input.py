"""Validated Windows mouse input for paired local viewers."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import threading
import time


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", InputUnion)]


def send_mouse(dx=0, dy=0, data=0, flags=0):
    event = Input(type=0)
    event.mi = MouseInput(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    function = ctypes.windll.user32.SendInput
    function.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    function.restype = wintypes.UINT
    if function(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
        raise PermissionError("Windows blocked input. Touch cannot control administrator windows or secure prompts.")


def desktop_coordinates(x, y, monitor, desktop):
    """Map a point in the selected display to the Windows virtual desktop."""
    if any(type(value) not in (int, float) or not math.isfinite(value) or
           not 0 <= value <= 1 for value in (x, y)):
        raise ValueError("Coordinates must be finite numbers from 0 to 1")
    px = monitor['left'] + round(x * (monitor['width'] - 1))
    py = monitor['top'] + round(y * (monitor['height'] - 1))
    dx = round((px - desktop['left']) * 65535 / max(1, desktop['width'] - 1))
    dy = round((py - desktop['top']) * 65535 / max(1, desktop['height'] - 1))
    return max(0, min(65535, dx)), max(0, min(65535, dy))


class MouseController:
    def __init__(self, sender=send_mouse, timeout=2.0):
        self.sender = sender
        self.timeout = timeout
        self.lock = threading.RLock()
        self.pressed = False
        self.last_activity = 0.0
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self.watchdog, daemon=True, name='Touch release watchdog')
        self.worker.start()

    def dispatch(self, payload, monitor, desktop):
        action = payload.get('action')
        if not isinstance(action, str) or action not in {'click', 'right_click', 'down', 'move', 'up', 'scroll', 'release', 'hold'}:
            raise ValueError('Unknown touch action')
        if set(payload) - {'action', 'x', 'y', 'delta', 'monitor'}:
            raise ValueError('Unknown touch field')
        if action in {'release', 'hold'}:
            coordinates = None
        else:
            coordinates = desktop_coordinates(payload.get('x'), payload.get('y'), monitor, desktop)
        if action == 'scroll':
            delta = payload.get('delta')
            if type(delta) is not int or not -1200 <= delta <= 1200:
                raise ValueError('Scroll delta must be an integer between -1200 and 1200')
        with self.lock:
            self.last_activity = time.monotonic()
            if action == 'release':
                self.release()
                return
            if action == 'hold':
                return
            # MOVE | ABSOLUTE | VIRTUALDESK supports monitors left of the primary display.
            self.sender(dx=coordinates[0], dy=coordinates[1], flags=0x0001 | 0x8000 | 0x4000)
            if action in {'click', 'right_click'}:
                self.release()
                down, up = (0x0002, 0x0004) if action == 'click' else (0x0008, 0x0010)
                try:
                    self.sender(flags=down)
                finally:
                    self.sender(flags=up)
            elif action == 'down' and not self.pressed:
                self.sender(flags=0x0002)
                self.pressed = True
            elif action == 'up':
                self.release()
            elif action == 'scroll':
                self.sender(data=delta, flags=0x0800)

    def release(self):
        with self.lock:
            if self.pressed:
                self.sender(flags=0x0004)
                self.pressed = False

    def watchdog(self):
        while not self.closed.wait(0.25):
            with self.lock:
                if self.pressed and time.monotonic() - self.last_activity > self.timeout:
                    try:
                        self.release()
                    except PermissionError:
                        # Retry after a Windows secure prompt or elevated window closes.
                        pass

    def close(self):
        self.closed.set()
        try:
            self.release()
        except PermissionError:
            pass
        self.worker.join(timeout=1)
