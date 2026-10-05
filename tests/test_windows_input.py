import ctypes
import time
import unittest

from windows_input import Input, MouseController, desktop_coordinates


class MouseInputTests(unittest.TestCase):
    monitor = {'left':-1920, 'top':0, 'width':1920, 'height':1080}
    desktop = {'left':-1920, 'top':0, 'width':3840, 'height':1080}

    def test_windows_input_structure_and_negative_monitor_mapping(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        self.assertEqual(desktop_coordinates(0, 0, self.monitor, self.desktop), (0, 0))
        end = desktop_coordinates(1, 1, self.monitor, self.desktop)
        self.assertLess(end[0], 32768)
        self.assertEqual(end[1], 65535)
        for x in (-1, 1.1, True, float('inf'), float('nan'), '0.5'):
            with self.assertRaises(ValueError):
                desktop_coordinates(x, 0, self.monitor, self.desktop)

    def test_drag_watchdog_releases_after_disconnect(self):
        calls = []
        mouse = MouseController(sender=lambda **event:calls.append(event), timeout=0.02)
        try:
            mouse.dispatch({'action':'down', 'x':0.5, 'y':0.5}, self.monitor, self.desktop)
            self.assertTrue(mouse.pressed)
            deadline = time.monotonic() + 1
            while mouse.pressed and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(mouse.pressed)
            self.assertEqual(calls[-1]['flags'], 0x0004)
        finally:
            mouse.close()

    def test_right_click_and_negative_scroll(self):
        calls = []
        mouse = MouseController(sender=lambda **event:calls.append(event))
        try:
            mouse.dispatch({'action':'right_click', 'x':0.5, 'y':0.5}, self.monitor, self.desktop)
            self.assertEqual([item['flags'] for item in calls], [0xC001, 0x0008, 0x0010])
            mouse.dispatch({'action':'scroll', 'x':0.5, 'y':0.5, 'delta':-120}, self.monitor, self.desktop)
            self.assertEqual(calls[-1], {'data':-120, 'flags':0x0800})
        finally:
            mouse.close()
