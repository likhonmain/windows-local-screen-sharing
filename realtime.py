"""Local-only WebRTC video, using Intel Quick Sync where available."""
import asyncio
import ctypes
from fractions import Fraction
import json
import secrets
import threading
import time

import aioice.ice
import av
from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription, VideoStreamTrack
from aiortc.codecs.h264 import H264Encoder
from aiortc.mediastreams import MediaStreamError


def local_offer(sdp, peer_ip):
    """Only accept direct UDP host candidates at the HTTP client's LAN address."""
    lines = []
    for line in sdp.splitlines():
        if line.startswith('a=candidate:'):
            parts = line.split()
            if len(parts) < 8 or parts[2].lower() != 'udp' or parts[7] != 'host':
                continue
            address = parts[4]
            if address.endswith('.local'):
                # Browser mDNS hides the interface IP. The authenticated HTTP peer gives it to us.
                parts[4] = peer_ip
            elif address != peer_ip:
                continue
            line = ' '.join(parts)
        lines.append(line)
    return '\r\n'.join(lines) + '\r\n'


class LowLatencyEncoder(H264Encoder):
    def __init__(self, mirror):
        super().__init__()
        self.mirror = mirror
        self._bitrate = 8_000_000
        self.configuration = None
        self.hardware_failed = False
        self.reformatter = av.video.reformatter.VideoReformatter()

    @property
    def target_bitrate(self):
        return self._bitrate

    @target_bitrate.setter
    def target_bitrate(self, value):
        self._bitrate = max(500_000, min(int(value), 30_000_000))

    def _open(self, frame, fps):
        for name in (('libx264',) if self.hardware_failed else ('h264_qsv', 'libx264')):
            try:
                codec = av.CodecContext.create(name, 'w')
                codec.width, codec.height = frame.width, frame.height
                codec.bit_rate = self.target_bitrate
                codec.pix_fmt = 'nv12' if name == 'h264_qsv' else 'yuv420p'
                codec.framerate = Fraction(fps, 1)
                codec.time_base = Fraction(1, 90000)
                codec.gop_size = fps
                codec.max_b_frames = 0
                codec.profile = 'Baseline'
                macroblocks = ((frame.width + 15) // 16) * ((frame.height + 15) // 16) * fps
                level = '4.2' if macroblocks <= 522240 else '5.1'
                codec.options = ({'preset':'veryfast', 'async_depth':'1', 'look_ahead':'0',
                                  'profile':'baseline', 'level':level} if name == 'h264_qsv' else
                                 {'preset':'ultrafast', 'tune':'zerolatency', 'profile':'baseline',
                                  'level':level, 'sc_threshold':'0'})
                codec.open()
                self.codec = codec
                self.configuration = (frame.width, frame.height, fps)
                self.mirror.encoder_name = 'Intel Quick Sync H.264' if name == 'h264_qsv' else 'CPU H.264 (ultrafast)'
                return
            except (av.FFmpegError, ValueError):
                if name == 'h264_qsv':
                    self.hardware_failed = True
        raise RuntimeError('No working H.264 encoder found')

    def _encode_frame(self, frame, force_keyframe):
        fps = self.mirror.target_fps
        if self.codec is None or self.configuration != (frame.width, frame.height, fps):
            self._open(frame, fps)
        elif abs(self.target_bitrate - self.codec.bit_rate) / max(1, self.codec.bit_rate) > 0.25:
            self._open(frame, fps)
        prepared = self.reformatter.reformat(frame, format=self.codec.pix_fmt)
        prepared.pict_type = av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        try:
            data = b''.join(bytes(packet) for packet in self.codec.encode(prepared))
        except av.FFmpegError:
            if self.hardware_failed:
                raise
            self.hardware_failed = True
            self._open(frame, fps)
            prepared = self.reformatter.reformat(frame, format=self.codec.pix_fmt)
            prepared.pict_type = av.video.frame.PictureType.I
            data = b''.join(bytes(packet) for packet in self.codec.encode(prepared))
        if data:
            self.mirror.encoded_times.append(time.monotonic())
            yield from self._split_bitstream(data)


class DesktopTrack(VideoStreamTrack):
    def __init__(self, mirror):
        super().__init__()
        self.mirror = mirror
        self.started = time.monotonic()
        self.last_pts = -1
        self.prepared = None
        self.available = asyncio.Event()
        self.producer = None
        self.bgra_frame = None
        # Reuse swscale's conversion context. frame.reformat() creates a fresh
        # context for each newly captured frame, which is costly at 60–120 fps.
        self.reformatter = av.video.reformatter.VideoReformatter()

    async def _prepare(self):
        deadline = time.monotonic()
        while not self.mirror.stopping and self.readyState == 'live':
            fps = self.mirror.target_fps
            now = time.monotonic()
            await asyncio.sleep(max(0, deadline - now))
            # Never catch up with obsolete frames when capture or encoding runs behind.
            deadline = max(deadline + 1 / fps, time.monotonic())
            with self.mirror.condition:
                raw = self.mirror.video_frame
                enabled = self.mirror.enabled
                width = self.mirror.presets[self.mirror.preset]['width']
                monitor = self.mirror.monitor
            if raw is None or not enabled:
                await asyncio.sleep(0.01)
                continue
            def convert():
                # Reuse input storage: allocating and copying a fresh full-size
                # ndarray-backed AVFrame every time adds substantial memory traffic.
                frame = self.bgra_frame
                if frame is None or (frame.width, frame.height) != (raw.shape[1], raw.shape[0]):
                    frame = av.VideoFrame(raw.shape[1], raw.shape[0], 'bgra')
                    self.bgra_frame = frame
                if frame.planes[0].buffer_size == raw.nbytes and raw.flags.c_contiguous:
                    frame.planes[0].update(raw)
                else:
                    frame = av.VideoFrame.from_ndarray(raw, format='bgra')
                target_width = min(width, frame.width) if width else frame.width
                target_width = max(2, target_width // 2 * 2)
                target_height = max(2, round(frame.height * target_width / frame.width) // 2 * 2)
                # Convert and resize once, directly into the video pipeline; no JPEG or PNG step.
                return self.reformatter.reformat(frame, width=target_width, height=target_height,
                                                  format='nv12', interpolation='FAST_BILINEAR')
            frame = await asyncio.get_running_loop().run_in_executor(None, convert)
            self.last_pts = max(self.last_pts + 1, round((time.monotonic() - self.started) * 90000))
            frame.pts = self.last_pts
            frame.time_base = Fraction(1, 90000)
            with self.mirror.condition:
                if not self.mirror.enabled or monitor != self.mirror.monitor:
                    continue
            # Prepare the next frame while QSV encodes/sends the previous one.
            # A single latest-frame slot bounds latency when Wi-Fi slows down.
            self.prepared = frame
            self.available.set()
        self.available.set()

    async def recv(self):
        if self.producer is None:
            self.producer = asyncio.create_task(self._prepare())
            def preparation_finished(task):
                if not task.cancelled() and task.exception():
                    self.stop()
            self.producer.add_done_callback(preparation_finished)
        while self.readyState == 'live' and not self.mirror.stopping:
            await self.available.wait()
            self.available.clear()
            frame, self.prepared = self.prepared, None
            if frame is not None:
                return frame
        raise MediaStreamError

    def stop(self):
        super().stop()
        if self.producer:
            self.producer.cancel()
        self.available.set()


class Realtime:
    def __init__(self, mirror):
        self.mirror = mirror
        # Windows otherwise rounds short asyncio timers to roughly 15.6 ms.
        # Balance the process-scoped 1 ms request on shutdown.
        self.timer_active = ctypes.windll.winmm.timeBeginPeriod(1) == 0
        self.loop = asyncio.new_event_loop()
        self.peers = {}
        # aioice defaults to all interfaces. Bind only the app's selected hotspot IPv4.
        self.original_addresses = aioice.ice.get_host_addresses
        aioice.ice.get_host_addresses = lambda use_ipv4, use_ipv6: [mirror.ip] if use_ipv4 else []
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name='Local WebRTC')
        self.thread.start()

    def offer(self, payload, peer_ip):
        if payload.get('type') != 'offer' or not isinstance(payload.get('sdp'), str):
            raise ValueError('Expected a WebRTC offer')
        if len(payload['sdp']) > 60000:
            raise ValueError('Offer is too large')
        future = asyncio.run_coroutine_threadsafe(self._offer(payload['sdp'], peer_ip), self.loop)
        try:
            return future.result(timeout=15)
        except TimeoutError:
            future.cancel()
            raise ValueError('Video negotiation timed out. Reconnect or choose image compatibility mode.')

    async def _offer(self, sdp, peer_ip):
        if len(self.peers) >= 4 or not self.mirror.enabled:
            raise ValueError('Sharing is paused or all video connections are in use')
        identity = secrets.token_urlsafe(16)
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        entry = {'pc':pc, 'counted':False, 'track':None}
        self.peers[identity] = entry
        @pc.on('connectionstatechange')
        async def state_changed():
            if pc.connectionState == 'connected' and not entry['counted']:
                entry['counted'] = True
                with self.mirror.condition:
                    self.mirror.video_viewers += 1
                    self.mirror.viewers += 1
                    self.mirror.condition.notify_all()
            elif pc.connectionState in {'failed', 'closed', 'disconnected'}:
                await self._close(identity)
        @pc.on('datachannel')
        def channel_received(channel):
            if channel.label != 'touch':
                channel.close()
                return
            @channel.on('message')
            def input_received(data):
                try:
                    if not isinstance(data, str) or len(data) > 4096:
                        return
                    payload = json.loads(data)
                    if not isinstance(payload, dict):
                        return
                    self.mirror.touch(payload)
                except (ValueError, TypeError, PermissionError) as exc:
                    if channel.readyState == 'open':
                        channel.send(json.dumps({'error':str(exc)}))
        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=local_offer(sdp, peer_ip), type='offer'))
            track = DesktopTrack(self.mirror)
            entry['track'] = track
            sender = pc.addTrack(track)
            # aiortc 1.15 has no public encoder-factory hook. Pin it and verify this adapter in tests.
            sender._RTCRtpSender__encoder = LowLatencyEncoder(self.mirror)
            for transceiver in pc.getTransceivers():
                if transceiver.kind == 'video':
                    transceiver.setCodecPreferences([codec for codec in RTCRtpSender.getCapabilities('video').codecs
                                                     if codec.mimeType.lower() == 'video/h264'])
            await pc.setLocalDescription(await pc.createAnswer())
            asyncio.create_task(self._expire(identity))
            return {'id':identity, 'type':pc.localDescription.type, 'sdp':pc.localDescription.sdp}
        except BaseException:
            await self._close(identity)
            raise

    async def _expire(self, identity):
        await asyncio.sleep(12)
        entry = self.peers.get(identity)
        if entry and entry['pc'].connectionState != 'connected':
            await self._close(identity)

    async def _close(self, identity):
        entry = self.peers.pop(identity, None)
        if not entry:
            return
        if entry['counted']:
            with self.mirror.condition:
                self.mirror.video_viewers = max(0, self.mirror.video_viewers - 1)
                self.mirror.viewers = max(0, self.mirror.viewers - 1)
                self.mirror.condition.notify_all()
        if entry['track']:
            entry['track'].stop()
        await entry['pc'].close()

    def disconnect(self, identity):
        if not isinstance(identity, str):
            raise ValueError('Invalid video session')
        asyncio.run_coroutine_threadsafe(self._close(identity), self.loop).result(timeout=5)

    def close(self):
        async def shutdown():
            for identity in list(self.peers):
                await self._close(identity)
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)
        self.loop.close()
        aioice.ice.get_host_addresses = self.original_addresses
        if self.timer_active:
            ctypes.windll.winmm.timeEndPeriod(1)
