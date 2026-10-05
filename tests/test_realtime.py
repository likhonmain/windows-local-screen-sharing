import asyncio
import json
import unittest

from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from realtime import local_offer
from server import Mirror, discover_network


class CandidateTests(unittest.TestCase):
    def test_only_direct_hotspot_candidates_and_mdns_resolution(self):
        sdp = '\r\n'.join([
            'v=0', 'a=candidate:1 1 udp 1 abc.local 10000 typ host',
            'a=candidate:2 1 udp 1 10.1.1.2 10000 typ host',
            'a=candidate:3 1 udp 1 8.8.8.8 10000 typ srflx',
            'a=candidate:4 1 tcp 1 10.1.1.2 10000 typ host',
            'a=candidate:5 1 udp 1 192.168.90.1 10000 typ host'])
        filtered = local_offer(sdp, '10.1.1.2')
        self.assertIn('10.1.1.2 10000 typ host', filtered)
        self.assertNotIn('.local', filtered)
        self.assertNotIn('8.8.8.8', filtered)
        self.assertNotIn(' tcp ', filtered)
        self.assertNotIn('192.168.90.1', filtered)


class RealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_h264_decoding_datachannel_and_cleanup(self):
        network = discover_network()
        inputs = []
        mirror = Mirror(network['ip'], network['prefix'], 0, 0,
                        input_sender=lambda **event: inputs.append(event))
        client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        try:
            mirror.update({'preset':'speed120'}, admin=True)
            self.assertEqual(mirror.target_fps, 120)
            for invalid in (121, 144, True, '120', 0):
                with self.assertRaises(ValueError):
                    mirror.update({'fps':invalid}, admin=True)
            receiver = client.addTransceiver('video', direction='recvonly')
            receiver.setCodecPreferences([codec for codec in RTCRtpSender.getCapabilities('video').codecs
                                          if codec.mimeType.lower() == 'video/h264'])
            track_ready = asyncio.Future()
            @client.on('track')
            def on_track(track):
                if not track_ready.done():
                    track_ready.set_result(track)
            channel = client.createDataChannel('touch')
            channel_ready = asyncio.Event()
            @channel.on('open')
            def on_open():
                channel_ready.set()
            await client.setLocalDescription(await client.createOffer())
            answer = await asyncio.to_thread(mirror.rtc.offer,
                        {'type':'offer','sdp':client.localDescription.sdp}, network['ip'])
            candidates = [line for line in answer['sdp'].splitlines() if line.startswith('a=candidate:')]
            self.assertTrue(candidates)
            self.assertTrue(all(network['ip'] in line and 'typ host' in line for line in candidates))
            await client.setRemoteDescription(RTCSessionDescription(type=answer['type'],sdp=answer['sdp']))
            track = await asyncio.wait_for(track_ready, 10)
            frames = [await asyncio.wait_for(track.recv(), 15) for _ in range(12)]
            self.assertEqual((frames[-1].width,frames[-1].height), (1280,720))
            self.assertGreater(frames[-1].pts,frames[0].pts)
            self.assertIn('H.264', mirror.encoder_name)
            self.assertEqual(mirror.video_viewers, 1)
            await asyncio.wait_for(channel_ready.wait(), 5)
            channel.send(json.dumps({'action':'click','x':0.5,'y':0.5,'monitor':1}))
            deadline = asyncio.get_running_loop().time() + 2
            while not inputs and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            self.assertEqual(inputs[-1]['flags'], 0x0004)
            await asyncio.to_thread(mirror.rtc.disconnect, answer['id'])
            self.assertEqual(mirror.video_viewers, 0)
            self.assertEqual(mirror.viewers, 0)
        finally:
            await client.close()
            mirror.close()
