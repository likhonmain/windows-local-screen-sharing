"""Local desktop mirror with paired phone touch control. No external services."""
from __future__ import annotations

import argparse
from collections import deque
import ctypes
from ctypes import wintypes
import hmac
import io
import ipaddress
import json
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from windows_input import MouseController
from fast_capture import FastCapture
from realtime import Realtime

import mss
import qrcode
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
PRESETS = {
    "speed120": {"label": "120 fps · 720p speed", "width": 1280, "quality": 70, "fps": 120},
    "speed60": {"label": "60 fps · 1080p smooth", "width": 1920, "quality": 80, "fps": 60},
    "eco": {"label": "Data saver · 854px", "width": 854, "quality": 45, "fps": 12},
    "fast": {"label": "Fast · 1024px", "width": 1024, "quality": 55, "fps": 20},
    "balanced": {"label": "Balanced · 1600px", "width": 1600, "quality": 75, "fps": 20},
    "smooth": {"label": "Smooth · 1080p", "width": 1920, "quality": 82, "fps": 30},
    "sharp": {"label": "Sharp text · 1440p", "width": 2560, "quality": 92, "fps": 15},
    "ultra": {"label": "Ultra · 4K", "width": 3840, "quality": 98, "fps": 15},
    "native": {"label": "Native · full resolution", "width": 0, "quality": 100, "fps": 15},
    "lossless": {"label": "Lossless · native PNG", "width": 0, "quality": 100, "fps": 8, "format": "PNG"},
}


class CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


def draw_pointer(picture, monitor):
    """Windows MSS excludes the cursor; add a pointer at its actual position."""
    info = CursorInfo()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)) and info.flags & 1:
        x = info.ptScreenPos.x - monitor['left']
        y = info.ptScreenPos.y - monitor['top']
        if 0 <= x < picture.width and 0 <= y < picture.height:
            points = [(x,y), (x,y+23), (x+6,y+17), (x+11,y+26),
                      (x+15,y+24), (x+10,y+15), (x+19,y+15)]
            ImageDraw.Draw(picture).polygon(points, fill='white', outline='black', width=2)


def discover_network():
    """Prefer Wi-Fi with a gateway; never accidentally choose a VM adapter."""
    command = r"""$items = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object { $cfg = $_; $addr = Get-NetIPAddress -InterfaceIndex $cfg.InterfaceIndex -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1; if ($addr) { [pscustomobject]@{ip=$addr.IPAddress; prefix=$addr.PrefixLength; alias=$cfg.InterfaceAlias; wifi=($cfg.NetAdapter.NdisPhysicalMedium -eq 9 -or $cfg.InterfaceAlias -match 'Wi-?Fi|Wireless')} } }); ConvertTo-Json -InputObject $items -Compress"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, text=True, check=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    items = json.loads(result.stdout.lstrip("\ufeff"))
    items = [item for item in items if ipaddress.ip_address(item["ip"]).is_private]
    if not items:
        raise RuntimeError("No local connection with a gateway found. Connect to your phone hotspot first.")
    wifi = [item for item in items if item["wifi"]]
    if len(wifi or items) != 1:
        raise RuntimeError("Multiple possible connections found. Use --bind LAPTOP_IP --prefix PREFIX.")
    return (wifi or items)[0]


class Mirror:
    def __init__(self, ip, prefix, port, control_port, input_sender=None):
        self.ip = ip
        self.network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        self.port = port
        self.control_port = control_port
        self.token = secrets.token_urlsafe(24)
        self.session = secrets.token_urlsafe(32)
        self.pair_url = f"http://{ip}:{port}/#{self.token}"
        self.control_url = f"http://127.0.0.1:{control_port}/"
        self.condition = threading.Condition()
        self.stopping = False
        self.stop_event = threading.Event()
        self.enabled = True
        self.touch_enabled = True
        self.viewers = 0
        self.video_viewers = 0
        self.video_frame = None
        self.fps_limit = 60
        self.presets = PRESETS
        self.capture_backend = 'Starting'
        self.encoder_name = 'Not connected'
        self.capture_times = deque(maxlen=240)
        self.encoded_times = deque(maxlen=240)
        self.frame = b""
        self.frame_type = "image/jpeg"
        self.sequence = 0
        self.last_frame_at = 0.0
        self.actual_fps = 0.0
        self.error = ""
        self.preset = "speed60"
        self.monitor = 1
        with mss.MSS() as capture:
            self.monitors = [dict(item) for item in capture.monitors]
        self.mouse = MouseController() if input_sender is None else MouseController(sender=input_sender)
        self.rtc = Realtime(self)
        self.worker = threading.Thread(target=self.capture, name="Screen capture", daemon=True)
        self.worker.start()

    def status(self, admin=False):
        with self.condition:
            result = {
                "sharing": self.enabled, "viewers": self.viewers,
                "preset": self.preset, "monitor": self.monitor,
                "touch": self.touch_enabled,
                "presets": PRESETS,
                "target_fps": self.target_fps,
                "fps_limit": self.fps_limit,
                "video_viewers": self.video_viewers,
                "capture_backend": self.capture_backend,
                "encoder": self.encoder_name,
                "encoded_fps": self.measured_fps(self.encoded_times),
                "realtime": True,
                "fps": round(self.actual_fps, 1) if self.viewers and self.enabled else 0,
                "error": self.error,
                "monitors": [{"id": i, "width": m["width"], "height": m["height"]}
                             for i, m in enumerate(self.monitors) if i > 0],
            }
            if admin:
                result.update(pair_url=self.pair_url, ip=self.ip, port=self.port,
                              subnet=str(self.network))
        return result

    @staticmethod
    def measured_fps(samples):
        now = time.monotonic()
        recent = [stamp for stamp in list(samples) if now - stamp < 2]
        return round((len(recent) - 1) / (recent[-1] - recent[0]), 1) if len(recent) > 1 else 0

    @property
    def target_fps(self):
        return min(self.fps_limit, 8) if self.preset == 'lossless' else self.fps_limit

    def update(self, payload, admin):
        allowed = {"preset", "monitor", "sharing", "touch", "fps"} if admin else {"preset", "fps"}
        if not payload or set(payload) - allowed:
            raise ValueError("Unsupported setting")
        if "preset" in payload and payload["preset"] not in PRESETS:
            raise ValueError("Unknown quality preset")
        if "monitor" in payload and (type(payload["monitor"]) is not int or
                                     not 1 <= payload["monitor"] < len(self.monitors)):
            raise ValueError("Unknown display")
        if "sharing" in payload and type(payload["sharing"]) is not bool:
            raise ValueError("Sharing must be true or false")
        if "touch" in payload and type(payload["touch"]) is not bool:
            raise ValueError("Touch must be true or false")
        if 'fps' in payload and (type(payload['fps']) is not int or payload['fps'] not in {30, 60, 90, 120}):
            raise ValueError('Frame rate must be 30, 60, 90, or 120')
        with self.condition:
            if 'monitor' in payload or payload.get('sharing') is False or payload.get('touch') is False:
                self.mouse.release()
            if "preset" in payload:
                self.preset = payload["preset"]
                self.frame = b""
                self.video_frame = None
                if self.preset in {'speed60', 'speed120'}:
                    self.fps_limit = PRESETS[self.preset]['fps']
            if 'fps' in payload:
                self.fps_limit = payload['fps']
            if "monitor" in payload:
                self.monitor = payload["monitor"]
                self.frame = b""
                self.video_frame = None
            if "sharing" in payload:
                self.enabled = payload["sharing"]
                self.frame = b""
                self.video_frame = None
            if "touch" in payload:
                self.touch_enabled = payload["touch"]
            self.condition.notify_all()

    def touch(self, payload):
        with self.condition:
            if payload.get('action') != 'release':
                if not self.enabled or not self.touch_enabled:
                    raise PermissionError('Touch control is disabled on the laptop.')
                if type(payload.get('monitor')) is not int or payload['monitor'] != self.monitor:
                    raise ValueError('Display changed. Wait for the current display before touching.')
            self.mouse.dispatch(payload, self.monitors[self.monitor], self.monitors[0])

    def capture(self):
        try:
            # MSS capture handles belong to the thread that uses them.
            with mss.MSS() as capture:
                source = FastCapture(capture)
                last_jpeg = 0
                while not self.stopping:
                    with self.condition:
                        active = self.enabled and self.viewers > 0
                    # Native capture shutdown can wait for callbacks. Keep it outside
                    # the application lock so peer cleanup and input remain responsive.
                    if not active:
                        source.close()
                        source.selection = None
                    with self.condition:
                        self.condition.wait_for(
                            lambda: self.stopping or (self.enabled and self.viewers > 0))
                        if self.stopping:
                            break
                        monitor, preset = self.monitor, self.preset
                        config = PRESETS[preset]
                        target_fps = self.target_fps
                        image_viewers = self.viewers - self.video_viewers
                    start = time.monotonic()
                    try:
                        raw, backend = source.read(self.monitors[monitor], target_fps)
                        if raw is None:
                            time.sleep(0.005)
                            continue
                        self.capture_backend = backend
                        output = None
                        image_format = config.get('format', 'JPEG')
                        # Real-time video skips image compression entirely. Keep compatibility images
                        # at at most 60 fps to avoid flooding TCP with full-size screenshots.
                        if image_viewers and time.monotonic() - last_jpeg >= 1 / min(target_fps, 60):
                            picture = Image.frombytes('RGB', (raw.shape[1], raw.shape[0]), raw.tobytes(), 'raw', 'BGRX')
                            if backend != 'Windows Graphics Capture':
                                draw_pointer(picture, self.monitors[monitor])
                            if config['width'] and picture.width > config['width']:
                                height = max(1, round(picture.height * config['width'] / picture.width))
                                picture = picture.resize((config['width'], height), Image.Resampling.BILINEAR)
                            output = io.BytesIO()
                            if image_format == 'PNG':
                                picture.save(output, format='PNG', compress_level=1)
                            else:
                                picture.save(output, format='JPEG', quality=config['quality'],
                                             subsampling=0 if config['quality'] >= 90 else 2)
                            last_jpeg = time.monotonic()
                        with self.condition:
                            # Discard a frame if sharing stopped or the selection changed mid-capture.
                            if self.enabled and monitor == self.monitor and preset == self.preset:
                                self.video_frame = raw
                                if output is not None:
                                    self.frame = output.getvalue()
                                    self.frame_type = 'image/png' if image_format == 'PNG' else 'image/jpeg'
                                    self.sequence += 1
                                now = time.monotonic()
                                self.capture_times.append(now)
                                self.actual_fps = self.measured_fps(self.capture_times)
                                self.last_frame_at = now
                                self.error = ""
                                self.condition.notify_all()
                    except Exception as exc:
                        with self.condition:
                            self.error = f"Capture unavailable: {exc}"
                        time.sleep(1)
                    delay = max(0, 1 / target_fps - (time.monotonic() - start))
                    if delay:
                        time.sleep(delay)
                source.close()
        except Exception as exc:
            with self.condition:
                self.error = f"Could not initialize screen capture: {exc}"
                self.condition.notify_all()

    def close(self):
        self.mouse.close()
        with self.condition:
            self.stopping = True
            self.frame = b""
            self.video_frame = None
            self.condition.notify_all()
        self.worker.join(timeout=3)
        self.rtc.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, mirror, admin=False):
        self.mirror = mirror
        self.admin = admin
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        # Pairing credentials never go to access logs.
        pass

    def gate(self):
        mirror = self.server.mirror
        peer = ipaddress.ip_address(self.client_address[0])
        expected = {f"127.0.0.1:{mirror.control_port}", f"localhost:{mirror.control_port}"} if self.server.admin else {f"{mirror.ip}:{mirror.port}"}
        if self.headers.get("Host", "") not in expected:
            self.reply(403, b"Use the address shown in the laptop control panel.")
            return False
        if (self.server.admin and not peer.is_loopback) or (not self.server.admin and peer not in mirror.network):
            self.reply(403, b"Local network only.")
            return False
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers['Host']}":
                self.reply(403, b"Cross-origin requests are blocked.")
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.reply(403, b"Cross-site requests are blocked.")
                return False
        return True

    def authenticated(self):
        if self.server.admin:
            return True
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            cookie = cookies.get("mirror_session")
            return bool(cookie and hmac.compare_digest(cookie.value, self.server.mirror.session))
        except Exception:
            return False

    def headers_for(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def reply(self, status, body, content_type="text/plain; charset=utf-8", cookie=None):
        self.headers_for(status, content_type, len(body))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status, value):
        self.reply(status, json.dumps(value).encode(), "application/json")

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def get(self):
        self.connection.settimeout(15)
        if not self.gate():
            return
        path = urlsplit(self.path).path
        if path in {"/", "/app.js", "/touch.js", "/style.css"}:
            name, kind = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/touch.js": ("touch.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }[path]
            body = (ROOT / "web" / name).read_bytes()
            if path == "/":
                body = body.replace(b"__ROLE__", b"control" if self.server.admin else b"viewer")
            self.reply(200, body, kind)
            return
        if path == "/favicon.ico":
            self.reply(204, b"")
            return
        if not self.authenticated():
            self.json_reply(401, {"error": "Pair this phone using the link or QR code on your laptop."})
            return
        if path == "/api/status":
            self.json_reply(200, self.server.mirror.status(self.server.admin))
        elif path == "/qr.png" and self.server.admin:
            output = io.BytesIO()
            qrcode.make(self.server.mirror.pair_url).save(output, format="PNG")
            self.reply(200, output.getvalue(), "image/png")
        elif path == "/stream" and not self.server.admin:
            self.stream()
        else:
            self.reply(404, b"Not found")

    def do_POST(self):
        self.connection.settimeout(10)
        try:
            if not self.gate():
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (65536 if urlsplit(self.path).path == '/api/rtc/offer' else 4096):
                self.reply(400, b"Invalid request size")
                self.close_connection = True
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            path = urlsplit(self.path).path
            if path == "/api/pair" and not self.server.admin:
                token = payload.get("token", "")
                if isinstance(token, str) and hmac.compare_digest(token, self.server.mirror.token):
                    cookie = f"mirror_session={self.server.mirror.session}; HttpOnly; SameSite=Strict; Path=/"
                    self.reply(200, b'{"ok":true}', "application/json", cookie)
                else:
                    self.json_reply(401, {"error": "Invalid pairing link. Scan the current QR code."})
            elif not self.authenticated():
                self.json_reply(401, {"error": "Pairing required"})
            elif path == "/api/settings":
                self.server.mirror.update(payload, self.server.admin)
                self.json_reply(200, self.server.mirror.status(self.server.admin))
            elif path == '/api/input' and not self.server.admin:
                self.server.mirror.touch(payload)
                self.json_reply(200, {"ok": True})
            elif path == '/api/rtc/offer' and not self.server.admin:
                self.json_reply(200, self.server.mirror.rtc.offer(payload, self.client_address[0]))
            elif path == '/api/rtc/close' and not self.server.admin:
                self.server.mirror.rtc.disconnect(payload.get('id'))
                self.json_reply(200, {'ok':True})
            elif path == "/api/stop" and self.server.admin:
                self.json_reply(200, {"ok": True})
                self.server.mirror.stop_event.set()
            else:
                self.reply(404, b"Not found")
        except (ValueError, TypeError) as exc:
            self.json_reply(400, {"error": str(exc)})
        except PermissionError as exc:
            self.json_reply(403, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def stream(self):
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        mirror = self.server.mirror
        with mirror.condition:
            if not mirror.enabled:
                self.reply(409, b"Sharing is paused on the laptop.")
                return
            if mirror.viewers >= 4:
                self.reply(429, b"Too many viewers; close another viewing tab.")
                return
            mirror.viewers += 1
            mirror.condition.notify_all()
        try:
            self.headers_for(200, "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            sequence = -1
            while not mirror.stopping:
                with mirror.condition:
                    mirror.condition.wait_for(lambda: mirror.stopping or not mirror.enabled or
                                              (mirror.frame and mirror.sequence != sequence), timeout=5)
                    if mirror.stopping or not mirror.enabled:
                        break
                    if not mirror.frame or mirror.sequence == sequence:
                        # Break on a capture failure instead of leaving the phone loading forever.
                        if mirror.error:
                            break
                        continue
                    frame, sequence, frame_type = mirror.frame, mirror.sequence, mirror.frame_type
                self.wfile.write(b"--frame\r\nContent-Type: " + frame_type.encode() + b"\r\nContent-Length: " +
                                 str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            with mirror.condition:
                mirror.viewers -= 1
                if not mirror.viewers:
                    mirror.frame = b""
                mirror.condition.notify_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", help="Laptop hotspot IPv4 address (automatically detected by default)")
    parser.add_argument("--prefix", type=int, default=24, help="Subnet prefix for an explicit --bind")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--control-port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    network = {"ip": args.bind, "prefix": args.prefix} if args.bind else discover_network()
    address = ipaddress.ip_address(network["ip"])
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
        parser.error("Bind must be a private LAN IPv4 address.")
    mirror = Mirror(str(address), network["prefix"], args.port, args.control_port)
    servers = []
    try:
        servers.append(Server((mirror.ip, args.port), mirror))
        servers.append(Server(("127.0.0.1", args.control_port), mirror, admin=True))
        runtime = ROOT / ".runtime"
        runtime.mkdir(exist_ok=True)
        # This file contains network details only, never a pairing credential.
        (runtime / "network.json").write_text(json.dumps({"ip": mirror.ip, "subnet": str(mirror.network),
                                                        "port": args.port, "python":sys._base_executable}), encoding="utf-8")
        stop = mirror.stop_event
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"\nLOCAL SCREEN MIRROR\nLaptop controls: {mirror.control_url}\nPhone address:   http://{mirror.ip}:{args.port}/\nAllowed network: {mirror.network}\n", flush=True)
        print("Scan the QR code in Laptop controls. Keep this window open. Ctrl+C stops sharing.\n"
              "If the phone cannot connect, run Enable-Hotspot-Access.cmd once.\n"
              "Touch control enabled for paired phones. No audio. All stream traffic stays local.", flush=True)
        if not args.no_browser:
            webbrowser.open(mirror.control_url)
        while not stop.wait(0.5):
            pass
    finally:
        mirror.close()
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nCould not start: {exc}\nCheck the hotspot connection and whether another mirror is already running.", flush=True)
        raise SystemExit(1)
