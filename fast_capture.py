"""Latest-frame Windows Graphics Capture with a GDI compatibility fallback."""
import time

import dxcam
import numpy as np


class FastCapture:
    def __init__(self, fallback):
        self.fallback = fallback
        self.camera = None
        self.selection = None
        self.backend = 'GDI'
        self.failed = set()
        self.warmup = 0

    def _start(self, monitor, fps):
        self.close()
        # Match desktop geometry, rather than assuming MSS and DXGI enumerate identically.
        factory = getattr(dxcam, '__factory')
        matches = []
        for device, outputs in enumerate(factory.outputs):
            for index, output in enumerate(outputs):
                rect = output.desc.DesktopCoordinates
                if (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top) == (
                        monitor['left'], monitor['top'], monitor['width'], monitor['height']):
                    matches.append((device, index))
        for backend in ('winrt', 'dxgi'):
            for device, index in matches:
                if (device, index, backend) in self.failed:
                    continue
                try:
                    self.camera = dxcam.create(device_idx=device, output_idx=index,
                                               output_color='BGRA', backend=backend,
                                               max_buffer_len=2)
                    self.camera.start(target_fps=fps, video_mode=True)
                    self.backend = 'Windows Graphics Capture' if backend == 'winrt' else 'DXGI'
                    self.selection = (tuple(monitor.values()), fps)
                    self.warmup = time.monotonic()
                    return
                except Exception:
                    self.failed.add((device, index, backend))
                    self.close()
        self.selection = (tuple(monitor.values()), fps)

    def read(self, monitor, fps):
        if self.selection != (tuple(monitor.values()), fps):
            self._start(monitor, fps)
        if self.camera:
            try:
                # grab() reads the latest ring-buffer entry without blocking on an empty buffer.
                frame = self.camera.grab()
                if frame is not None:
                    return frame, self.backend
                if time.monotonic() - self.warmup < 1:
                    return None, self.backend
            except Exception:
                pass
            self.close()
        shot = self.fallback.grab(monitor)
        frame = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return frame, 'GDI'

    def close(self):
        if self.camera:
            try:
                self.camera.stop()
                self.camera.release()
            except Exception:
                pass
        self.camera = None
        self.backend = 'GDI'
