# -*- coding: utf-8 -*-
"""Bộ thử — chạy:  python -m unittest discover -s tests -v   (không cần khoá, không gọi mạng)"""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pudu_client                     # noqa: E402
from pudu_client import PuduClient, PuduError   # noqa: E402
import map_tools                       # noqa: E402
import demo_data                       # noqa: E402
import server                          # noqa: E402


class TestSignature(unittest.TestCase):
    def test_query_sorted_and_encoded(self):
        self.assertEqual(pudu_client.build_query({"b": 2, "a": "###x y", "c": True}),
                         "a=%23%23%23x%20y&b=2&c=true")

    def test_array_param_joined_by_comma(self):
        self.assertEqual(pudu_client.build_query({"error_levels": ["Error", "Warning"]}),
                         "error_levels=Error%2CWarning")

    def test_content_md5_is_base64_of_hex(self):
        body = '{"sn":"X"}'
        expect = base64.b64encode(hashlib.md5(body.encode()).hexdigest().encode()).decode()
        self.assertEqual(pudu_client.content_md5(body), expect)
        self.assertEqual(pudu_client.content_md5(""), "")

    def test_signature_matches_reference_algorithm(self):
        """Tính tay đúng như mẫu Go của hãng: chuỗi ký dùng query ĐÃ GIẢI MÃ."""
        date = "Mon, 26 Oct 2026 03:00:00 GMT"
        h, url_path = pudu_client.signed_headers("KEY", "SECRET", "GET",
                                                 "/pudu-entry/map-service/v1/open/map",
                                                 pudu_client.build_query({"shop_id": 1, "map_name": "1#1#Tầng 1"}),
                                                 x_date=date)
        self.assertIn("map_name=1%231%23T%E1%BA%A7ng%201", url_path)
        text = ("x-date: %s\nGET\napplication/json\napplication/json\n\n"
                "/pudu-entry/map-service/v1/open/map?map_name=1#1#Tầng 1&shop_id=1" % date)
        sig = base64.b64encode(hmac.new(b"SECRET", text.encode(), hashlib.sha1).digest()).decode()
        self.assertIn('signature="%s"' % sig, h["Authorization"])
        self.assertIn('hmac id="KEY"', h["Authorization"])
        self.assertNotIn("Content-MD5", h)

    def test_prefix_added_and_demo_accepts_signature(self):
        c = PuduClient(demo_data.DEMO_KEY, demo_data.DEMO_SECRET, transport=demo_data.transport)
        self.assertEqual(c.health_check()["message"], "SUCCESS")
        self.assertTrue(c.last_exchange["request"]["path"].startswith("/pudu-entry/"))

    def test_wrong_secret_rejected_like_real_gateway(self):
        c = PuduClient(demo_data.DEMO_KEY, "sai-secret", transport=demo_data.transport)
        with self.assertRaises(PuduError) as cm:
            c.robot_status(demo_data.SN1)
        self.assertEqual(cm.exception.message, "HMAC signature does not match.")
        self.assertEqual(cm.exception.http_status, 401)

    def test_post_body_signed(self):
        c = PuduClient(demo_data.DEMO_KEY, demo_data.DEMO_SECRET, transport=demo_data.transport)
        r = c.control_doors(demo_data.SN1, {"1": True})
        self.assertEqual(r["door_states"][0]["door_number"], "1")
        c.control_doors(demo_data.SN1, {"1": False})


class TestMap(unittest.TestCase):
    def test_world_pixel_round_trip(self):
        g = map_tools.MapGeometry(640, 400, 0.05, [-6.0, -4.0])
        col, row = g.world_to_pixel(-6.0, -4.0)
        self.assertEqual((col, row), (0.0, 400.0))          # gốc bản đồ = góc DƯỚI-trái ảnh
        x, y = g.pixel_to_world(*g.world_to_pixel(3.2, 7.7))
        self.assertAlmostEqual(x, 3.2)
        self.assertAlmostEqual(y, 7.7)

    def test_origin_or_origin_list(self):
        self.assertEqual(map_tools.map_origin({"origin": [1, 2, 0]}), [1.0, 2.0])
        self.assertEqual(map_tools.map_origin({"origin_list": [3, 4, 0]}), [3.0, 4.0])

    def test_image_host_whitelist(self):
        with self.assertRaises(ValueError):
            map_tools.download_image("https://evil.example.com/x.png")
        with self.assertRaises(ValueError):
            map_tools.download_image("http://a.pudutech.com/x.png")

    def test_bundle_and_svg_from_demo(self):
        c = PuduClient(demo_data.DEMO_KEY, demo_data.DEMO_SECRET, transport=demo_data.transport)
        b = map_tools.fetch_map_bundle(c, sn=demo_data.SN1, image_loader=demo_data.image_for)
        self.assertEqual(map_tools.png_size(b["image_bytes"]), (demo_data.W, demo_data.H))
        js = map_tools.bundle_to_json(b)
        self.assertEqual(len(js["points"]), len(demo_data.POINTS))
        p = next(x for x in js["points"] if x["name"] == "P.101")
        self.assertEqual(p["pixel"], [130.0, 90.0])
        svg = map_tools.build_svg(b["image_bytes"], b["geometry"], b["elements"])
        self.assertTrue(svg.startswith("<svg") and "data:image/png;base64," in svg)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.APP = server.App(demo=True)
        cls.ui = server.SingleServer(("127.0.0.1", 0), server.UIHandler)
        cls.base = "http://127.0.0.1:%d" % cls.ui.server_address[1]
        threading.Thread(target=cls.ui.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.ui.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as f:
            return f.read().decode("utf-8")

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as f:
                return f.status, json.loads(f.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_secret_never_leaves_server(self):
        for path in ("/api/system", "/api/robots", "/api/robot?sn=" + demo_data.SN1,
                     "/api/map?sn=" + demo_data.SN1, "/api/events"):
            self.assertNotIn(demo_data.DEMO_SECRET, self.get(path), path)

    def test_commands_blocked_when_read_only(self):
        orig = server.load_config
        server.load_config = lambda: dict(orig(), allow_commands=False)
        try:
            code, js = self.post("/api/command", {"action": "charge", "sn": demo_data.SN1})
        finally:
            server.load_config = orig
        self.assertEqual(code, 502)
        self.assertIn("CHỈ ĐỌC", js["error"])

    def test_errand_flow_when_allowed(self):
        orig = server.load_config
        server.load_config = lambda: dict(orig(), allow_commands=True)
        try:
            code, js = self.post("/api/command", {"action": "errand", "sn": demo_data.SN1,
                                                  "map_name": demo_data.MAP1, "pickup": "Sảnh",
                                                  "dropoff": "P.103", "hatch_id": "1"})
            self.assertEqual(code, 200, js)
            self.assertTrue(js["result"]["session_id"].startswith("ERRAND"))
            self.assertEqual(js["exchange"]["request"]["path"], "/pudu-entry/open-platform-service/v1/task_errand")
            ev = json.loads(self.get("/api/events?after=0"))["list"]
            self.assertTrue(any(e["callback_type"] == "notifyErrandStatus" for e in ev))
        finally:
            server.load_config = orig


class TestCallback(unittest.TestCase):
    def test_callback_code_required(self):
        server.APP = server.App(demo=True)
        orig = server.load_config
        server.load_config = lambda: dict(orig(), callback={"enabled": True, "path": "/pudu/callback",
                                                            "callback_code": "A" * 32})
        cb = server.SingleServer(("127.0.0.1", 0), server.CallbackHandler)
        threading.Thread(target=cb.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/pudu/callback" % cb.server_address[1]
        body = json.dumps({"callback_type": "robotStatus", "data": {"sn": "X", "run_status": "online"}}).encode()

        def send(code):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "CallbackCode": code})
            try:
                with urllib.request.urlopen(req) as f:
                    return f.status
            except urllib.error.HTTPError as e:
                return e.code
        try:
            self.assertEqual(send("B" * 32), 401)
            self.assertEqual(send("A" * 32), 200)
            self.assertEqual(server.APP.events.since(0)[-1]["callback_type"], "robotStatus")
        finally:
            cb.shutdown()
            server.load_config = orig


if __name__ == "__main__":
    unittest.main()
