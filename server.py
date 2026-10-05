"""Local, view-only desktop mirror. No external services or network dependencies."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hmac
import io
import ipaddress
import json
import secrets
import signal
import subprocess
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import mss
import qrcode
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
PRESETS = {
    "fast": {"width": 1024, "quality": 55, "fps": 15},
    "balanced": {"width": 1600, "quality": 75, "fps": 20},
    "sharp": {"width": 2560, "quality": 88, "fps": 15},
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
    def __init__(self, ip, prefix, port, control_port):
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
        self.viewers = 0
        self.frame = b""
        self.sequence = 0
        self.last_frame_at = 0.0
        self.actual_fps = 0.0
        self.error = ""
        self.preset = "balanced"
        self.monitor = 1
        with mss.MSS() as capture:
            self.monitors = [dict(item) for item in capture.monitors]
        self.worker = threading.Thread(target=self.capture, name="Screen capture", daemon=True)
        self.worker.start()

    def status(self, admin=False):
        with self.condition:
            result = {
                "sharing": self.enabled, "viewers": self.viewers,
                "preset": self.preset, "monitor": self.monitor,
                "fps": round(self.actual_fps, 1) if self.viewers and self.enabled else 0,
                "error": self.error,
                "monitors": [{"id": i, "width": m["width"], "height": m["height"]}
                             for i, m in enumerate(self.monitors) if i > 0],
            }
            if admin:
                result.update(pair_url=self.pair_url, ip=self.ip, port=self.port,
                              subnet=str(self.network))
            return result

    def update(self, payload, admin):
        allowed = {"preset", "monitor", "sharing"} if admin else {"preset"}
        if not payload or set(payload) - allowed:
            raise ValueError("Unsupported setting")
        if "preset" in payload and payload["preset"] not in PRESETS:
            raise ValueError("Unknown quality preset")
        if "monitor" in payload and (type(payload["monitor"]) is not int or
                                     not 1 <= payload["monitor"] < len(self.monitors)):
            raise ValueError("Unknown display")
        if "sharing" in payload and type(payload["sharing"]) is not bool:
            raise ValueError("Sharing must be true or false")
        with self.condition:
            if "preset" in payload:
                self.preset = payload["preset"]
            if "monitor" in payload:
                self.monitor = payload["monitor"]
                self.frame = b""
            if "sharing" in payload:
                self.enabled = payload["sharing"]
                self.frame = b""
            self.condition.notify_all()

    def capture(self):
        try:
            # MSS capture handles belong to the thread that uses them.
            with mss.MSS() as capture:
                while not self.stopping:
                    with self.condition:
                        self.condition.wait_for(
                            lambda: self.stopping or (self.enabled and self.viewers > 0))
                        if self.stopping:
                            break
                        monitor, preset = self.monitor, self.preset
                        config = PRESETS[preset]
                    start = time.monotonic()
                    try:
                        shot = capture.grab(self.monitors[monitor])
                        picture = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                        draw_pointer(picture, self.monitors[monitor])
                        if picture.width > config["width"]:
                            height = max(1, round(picture.height * config["width"] / picture.width))
                            picture = picture.resize((config["width"], height), Image.Resampling.BILINEAR)
                        output = io.BytesIO()
                        picture.save(output, format="JPEG", quality=config["quality"])
                        with self.condition:
                            # Discard a frame if sharing stopped or the selection changed mid-capture.
                            if self.enabled and monitor == self.monitor and preset == self.preset:
                                self.frame = output.getvalue()
                                self.sequence += 1
                                now = time.monotonic()
                                if self.last_frame_at:
                                    elapsed = now - self.last_frame_at
                                    self.actual_fps = 1 / elapsed if elapsed else 0
                                self.last_frame_at = now
                                self.error = ""
                                self.condition.notify_all()
                    except Exception as exc:
                        with self.condition:
                            self.error = f"Capture unavailable: {exc}"
                        time.sleep(1)
                    delay = max(0, 1 / config["fps"] - (time.monotonic() - start))
                    if delay:
                        time.sleep(delay)
        except Exception as exc:
            with self.condition:
                self.error = f"Could not initialize screen capture: {exc}"
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.stopping = True
            self.frame = b""
            self.condition.notify_all()
        self.worker.join(timeout=3)


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
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

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
        if path in {"/", "/app.js", "/style.css"}:
            name, kind = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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
            if not 0 < length <= 4096:
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
            elif path == "/api/stop" and self.server.admin:
                self.json_reply(200, {"ok": True})
                self.server.mirror.stop_event.set()
            else:
                self.reply(404, b"Not found")
        except (ValueError, TypeError) as exc:
            self.json_reply(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def stream(self):
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
                    frame, sequence = mirror.frame, mirror.sequence
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
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
                                                        "port": args.port}), encoding="utf-8")
        stop = mirror.stop_event
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"\nLOCAL SCREEN MIRROR\nLaptop controls: {mirror.control_url}\nPhone address:   http://{mirror.ip}:{args.port}/\nAllowed network: {mirror.network}\n", flush=True)
        print("Scan the QR code in Laptop controls. Keep this window open. Ctrl+C stops sharing.\n"
              "If the phone cannot connect, run Enable-Hotspot-Access.cmd once.\n"
              "Screen only: no audio or remote control. All stream traffic stays local.", flush=True)
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
