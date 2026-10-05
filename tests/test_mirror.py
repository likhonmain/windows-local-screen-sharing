import http.client
import io
import json
import threading
import unittest

from PIL import Image
from server import Mirror, Server, discover_network


class MirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        network = discover_network()
        cls.mirror = Mirror(network['ip'], network['prefix'], 0, 0)
        cls.viewer = Server((network['ip'], 0), cls.mirror)
        cls.admin = Server(('127.0.0.1', 0), cls.mirror, admin=True)
        cls.mirror.port = cls.viewer.server_port
        cls.mirror.control_port = cls.admin.server_port
        for server in (cls.viewer, cls.admin):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.mirror.close()
        for server in (cls.viewer, cls.admin):
            server.shutdown()
            server.server_close()

    def request(self, path, payload=None, admin=False, cookie=None, extra=None):
        host = '127.0.0.1' if admin else self.mirror.ip
        port = self.mirror.control_port if admin else self.mirror.port
        connection = http.client.HTTPConnection(host, port, timeout=8)
        headers = {'Content-Type':'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        headers.update(extra or {})
        connection.request('GET' if payload is None else 'POST', path,
                           body=None if payload is None else json.dumps(payload), headers=headers)
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.headers))
        connection.close()
        return result

    def pair(self):
        status, _, headers = self.request('/api/pair', {'token':self.mirror.token})
        self.assertEqual(status, 200)
        return headers['Set-Cookie'].split(';')[0]

    def test_authentication_and_control_isolation(self):
        self.assertEqual(self.request('/api/status')[0], 401)
        self.assertEqual(self.request('/stream')[0], 401)
        self.assertEqual(self.request('/api/pair', {'token':'wrong'})[0], 401)
        cookie = self.pair()
        status, body, _ = self.request('/api/status', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn('pair_url', json.loads(body))
        self.assertEqual(self.request('/api/settings', {'sharing':False}, cookie=cookie)[0], 400)
        self.assertEqual(self.request('/qr.png', cookie=cookie)[0], 404)

    def test_host_origin_and_invalid_input(self):
        self.assertEqual(self.request('/', extra={'Host':'attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/settings', {'sharing':False}, admin=True,
                                      extra={'Origin':'https://attacker.example'})[0], 403)
        self.assertEqual(self.request('/api/settings', {'monitor':999}, admin=True)[0], 400)
        self.assertEqual(self.request('/api/settings', {'sharing':'yes'}, admin=True)[0], 400)
        self.assertEqual(self.request('/api/settings', {'preset':[]}, admin=True)[0], 400)

    def test_live_frame_pause_quality_and_qr(self):
        cookie = self.pair()
        self.request('/api/settings', {'sharing':True, 'preset':'fast'}, admin=True)
        connection = http.client.HTTPConnection(self.mirror.ip, self.mirror.port, timeout=10)
        connection.request('GET', '/stream', headers={'Cookie':cookie})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.readline(), b'--frame\r\n')
        self.assertIn(b'image/jpeg', response.readline())
        length = int(response.readline().decode().split(':')[1])
        self.assertEqual(response.readline(), b'\r\n')
        jpeg = response.read(length)
        picture = Image.open(io.BytesIO(jpeg))
        picture.load()
        self.assertLessEqual(picture.width, 1024)
        self.assertGreater(picture.height, 0)
        self.request('/api/settings', {'sharing':False}, admin=True)
        response.close()
        connection.close()
        self.assertEqual(self.request('/stream', cookie=cookie)[0], 409)
        self.assertEqual(self.request('/api/settings', {'preset':'sharp'}, cookie=cookie)[0], 200)
        status, png, _ = self.request('/qr.png', admin=True)
        self.assertEqual(status, 200)
        self.assertEqual(Image.open(io.BytesIO(png)).format, 'PNG')
        self.request('/api/settings', {'sharing':True, 'preset':'balanced'}, admin=True)


if __name__ == '__main__':
    unittest.main()
