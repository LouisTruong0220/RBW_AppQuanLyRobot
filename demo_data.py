# -*- coding: utf-8 -*-
"""
demo_data.py — Máy chủ PUDU GIẢ LẬP để chạy thử app khi chưa có ApiAppKey.

  python server.py --demo

Toàn bộ dữ liệu ở đây là DỮ LIỆU MẪU, không phải robot thật:
  · 1 cửa hàng "Toà nhà mẫu (demo)", 2 robot FlashBot Max (1 máy online, 1 máy offline)
  · 1 bản đồ tầng 1 dựng bằng mã (sảnh, hành lang, 4 phòng, pantry, kho, thang máy)

Điểm đáng giá cho người tích hợp: bộ giả lập KIỂM CHỮ KÝ y như cổng thật. Bạn sửa
pudu_client.py mà ký sai thì demo trả đúng câu lỗi của hãng "HMAC signature does not match.",
nên đi qua được demo nghĩa là phần ký đã đúng thuật toán.

Tên trường và giá trị trạng thái lấy đúng theo tài liệu PUDU Cloud API (tải 23/10/2026).
"""
import base64
import hashlib
import hmac
import json
import re
import struct
import threading
import time
import zlib
from urllib.parse import urlparse, unquote, parse_qsl

import pudu_client

DEMO_KEY = "DEMO-APP-KEY"
DEMO_SECRET = "DEMO-APP-SECRET-khong-phai-khoa-that"

SHOP_ID = 900000001
SHOP_NAME = "Toà nhà mẫu (demo)"
SN1, SN2 = "DEMO-FBMAX-0001", "DEMO-FBMAX-0002"
MAP1 = "1#1#DEMO-TANG-1"
MAP2 = "2#2#DEMO-TANG-2"

RES = 0.05                       # mét / điểm ảnh — đúng mức hãng hay trả
ORIGIN = [-6.0, -4.0]
W, H = 640, 400                  # 32 m × 20 m

# Điểm đến (mét). mode theo tài liệu: table, dining_outlet, parking...
POINTS = [
    ("Sảnh",   "dining_outlet", 1.5, 2.0, 1.57),
    ("P.101",  "table",   0.5, 11.5, 1.57),
    ("P.102",  "table",   7.5, 11.5, 1.57),
    ("P.103",  "table",  14.5, 11.5, 1.57),
    ("P.104",  "table",  21.5, 11.5, 1.57),
    ("Pantry", "table",  10.5, 1.5, -1.57),
    ("Kho",    "table",  22.0, 1.5, -1.57),
    ("Chờ",    "parking", -2.5, 6.5, 0.0),
]
CHARGER = (-4.0, 6.5)
ELEVATOR = (16.5, 1.0)
ELEV_WAIT = (16.5, 6.5)


# ------------------------------------------------------------ ảnh bản đồ

def _png_gray(width, height, pixels):
    """Ghi PNG xám 8-bit bằng zlib — không cần thư viện ảnh."""
    raw = b"".join(b"\x00" + bytes(pixels[r * width:(r + 1) * width]) for r in range(height))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _px(x, y):
    return int(round((x - ORIGIN[0]) / RES)), int(round(H - (y - ORIGIN[1]) / RES))


def build_map_png():
    """Mặt bằng tầng 1 giả: 255 sàn trống, 0 tường/vật cản, 128 chưa quét."""
    img = bytearray([128]) * (W * H)

    def rect(x0, y0, x1, y1, val):
        c0, r1 = _px(x0, y0)
        c1, r0 = _px(x1, y1)
        for r in range(max(0, r0), min(H, r1)):
            for c in range(max(0, c0), min(W, c1)):
                img[r * W + c] = val

    def wall_h(x0, x1, y, gaps=()):
        x = x0
        while x < x1:
            if not any(g0 <= x < g1 for g0, g1 in gaps):
                rect(x, y - 0.1, x + RES, y + 0.1, 0)
            x += RES

    def wall_v(x, y0, y1, gaps=()):
        y = y0
        while y < y1:
            if not any(g0 <= y < g1 for g0, g1 in gaps):
                rect(x - 0.1, y, x + 0.1, y + RES, 0)
            y += RES

    rect(-5.0, -2.5, 25.0, 15.0, 255)                         # sàn trong toà nhà
    wall_h(-5.0, 25.0, -2.5)
    wall_h(-5.0, 25.0, 15.0)
    wall_v(-5.0, -2.5, 15.0)
    wall_v(25.0, -2.5, 15.0)
    # dãy phòng phía bắc, cửa mở ra hành lang
    wall_h(-5.0, 25.0, 8.5, gaps=[(-0.5, 1.5), (6.5, 8.5), (13.5, 15.5), (20.5, 22.5)])
    for x in (4.0, 11.0, 18.0):
        wall_v(x, 8.5, 15.0)
    # dãy phía nam: sảnh | pantry | thang máy | kho
    wall_h(-5.0, 25.0, 4.5, gaps=[(-1.0, 5.5), (9.5, 11.5), (15.0, 18.0), (21.0, 23.0)])
    for x in (7.0, 14.0, 19.0):
        wall_v(x, -2.5, 4.5)
    # bàn làm việc, kệ, quầy — vật cản robot nhìn thấy
    for x in (-3.5, 1.5, 5.0, 8.5, 12.0, 15.5, 19.5, 23.0):
        rect(x, 12.5, x + 1.2, 13.4, 0)
    rect(-3.5, -1.5, 0.5, -0.6, 0)        # quầy lễ tân
    rect(8.0, -1.8, 13.0, -1.2, 0)        # kệ pantry
    rect(20.0, -1.8, 24.2, -1.0, 0)       # kệ kho
    rect(15.0, -2.4, 18.0, -0.2, 0)       # buồng thang
    return _png_gray(W, H, img)


MAP_PNG = build_map_png()


def _elements():
    els = [{"type": "source", "id": n, "name": n, "mode": m, "vector_list": [x, y, yaw],
            "clean_path_list": []} for n, m, x, y, yaw in POINTS]
    els.append({"type": "charging_pile", "id": "charger-0", "name": "Trạm sạc",
                "mode": "", "vector_list": [CHARGER[0], CHARGER[1], 0.0]})
    els.append({"type": "elevator", "id": "elevator-1", "name": "Thang máy 1",
                "mode": "", "vector_list": [ELEVATOR[0], ELEVATOR[1], 0.0]})
    els.append({"type": "elev_waiter", "id": "elev-wait-1", "name": "Điểm chờ thang 1",
                "mode": "", "vector_list": [ELEV_WAIT[0], ELEV_WAIT[1], 0.0]})
    els.append({"type": "track", "id": "track-main", "name": "", "mode": "",
                "vector_list": [-4.0, 6.5, 23.5, 6.5]})
    for x, yb in ((0.5, 11.5), (7.5, 11.5), (14.5, 11.5), (21.5, 11.5),
                  (1.5, 2.0), (10.5, 1.5), (16.5, 1.0), (22.0, 1.5)):
        els.append({"type": "track", "id": "track-%s" % x, "name": "", "mode": "",
                    "vector_list": [x, 6.5, x, yb]})
    els.append({"type": "virtual_wall", "id": "vw-1", "name": "", "mode": "",
                "vector_list": [-4.8, 3.6, -1.4, 3.6]})
    return els


# ------------------------------------------------------------ trạng thái robot

class DemoState(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []          # callback giả — server.py lấy ra và đẩy vào luồng sự kiện
        self.robot = {
            SN1: {"battery": 86, "run_state": "IDLE", "move_state": "IDLE",
                  "charge_stage": "CHARGING", "is_charging": 1, "x": CHARGER[0] + 0.4,
                  "y": CHARGER[1], "doors": {"1": "CLOSED", "2": "CLOSED",
                                              "3": "CLOSED", "4": "CLOSED"}},
            SN2: {"battery": 0, "run_state": "OFFLINE", "move_state": "IDLE",
                  "charge_stage": "IDLE", "is_charging": -1, "x": 0, "y": 0,
                  "doors": {"1": "CLOSED", "2": "CLOSED", "3": "CLOSED", "4": "CLOSED"}},
        }
        self.errands = []
        self.seq = 0

    def next_id(self, prefix):
        self.seq += 1
        return "%s-%d-%03d" % (prefix, int(time.time()), self.seq)

    def emit(self, callback_type, data):
        data = dict(data, timestamp=int(time.time()), notify_timestamp=int(time.time() * 1000))
        self.events.append({"callback_type": callback_type, "data": data})

    def drain(self):
        with self.lock:
            ev, self.events = self.events, []
        return ev


STATE = DemoState()


def _point(name):
    for n, m, x, y, _ in POINTS:
        if n == name:
            return x, y
    return None


# ------------------------------------------------------------ "cổng" giả

def _ok(data=None):
    js = {"trace_id": "%s_demo-%d" % (DEMO_KEY, int(time.time() * 1000)), "message": "SUCCESS"}
    if data is not None:
        js["data"] = data
    return 200, json.dumps(js, ensure_ascii=False)


def _fail(message, http=200):
    return http, json.dumps({"trace_id": "%s_demo" % DEMO_KEY, "message": message,
                             "data": None}, ensure_ascii=False)


def _check_signature(method, url, headers, body):
    """Tính lại chữ ký đúng như cổng PUDU và so với header Authorization."""
    auth = headers.get("Authorization", "")
    m = re.search(r'hmac id="([^"]+)".*signature="([^"]+)"', auth)
    if not m:
        return "HMAC id or signature missing."
    if m.group(1) != DEMO_KEY:
        return "HMAC signature cannot be verified."
    u = urlparse(url)
    md5 = headers.get("Content-MD5", "")
    if body and md5 != pudu_client.content_md5(body.decode("utf-8")):
        return "HMAC signature does not match."
    pq = u.path + ("?" + unquote(u.query) if u.query else "")
    expect = pudu_client.sign(DEMO_SECRET, pudu_client.string_to_sign(
        headers.get("x-date", ""), method, md5, pq))
    if not hmac.compare_digest(expect, m.group(2)):
        return "HMAC signature does not match."
    return None


def transport(method, url, headers, body, timeout):
    """Thay cho urllib trong PuduClient khi chạy --demo."""
    err = _check_signature(method, url, headers, body)
    if err:
        return 401, json.dumps({"message": err})
    u = urlparse(url)
    if not u.path.startswith(pudu_client.PREFIX):
        return 404, json.dumps({"message": "Route Not Found"})
    path = u.path[len(pudu_client.PREFIX):]
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    b = json.loads(body.decode("utf-8")) if body else {}
    for k in ("limit",):
        if k in q and int(q[k]) > pudu_client.MAX_LIMIT:
            return _fail("The maximum number of items per page cannot exceed 100.")
    with STATE.lock:
        return _route(method, path, q, b)


def _robot_or_fail(sn):
    if sn not in STATE.robot:
        return None, _fail("you do not have permission for this sn")
    return STATE.robot[sn], None


def _route(method, path, q, b):
    now = int(time.time())
    if path == "/data-open-platform-service/v1/api/healthCheck":
        return _ok({"status": "ok"})

    if path == "/data-open-platform-service/v1/api/shop":
        return _ok({"count": 1, "list": [{"shop_id": SHOP_ID, "shop_name": SHOP_NAME,
                                          "company_id": 1, "company_name": "Đại lý mẫu"}]})
    if path == "/data-open-platform-service/v1/api/robot":
        lst = [{"sn": SN1, "mac": "AA:BB:CC:00:00:01", "shop_id": SHOP_ID, "shop_name": SHOP_NAME,
                "product_code": "FlashBotMax", "nickname": "FlashBot Max 01 (demo)",
                "first_active_time": now - 30 * 86400},
               {"sn": SN2, "mac": "AA:BB:CC:00:00:02", "shop_id": SHOP_ID, "shop_name": SHOP_NAME,
                "product_code": "FlashBotMax", "nickname": "FlashBot Max 02 (demo)",
                "first_active_time": now - 20 * 86400}]
        pc = q.get("product_code")
        if pc:
            lst = [r for r in lst if r["product_code"] in pc.split(",")]
        return _ok({"count": len(lst), "list": lst})

    if path == "/open-platform-service/v2/status/get_by_sn":
        r, f = _robot_or_fail(q.get("sn"))
        if f:
            return f
        return _ok({"sn": q["sn"], "mac": "AA:BB:CC:00:00:0%s" % q["sn"][-1], "timestamp": now,
                    "battery": r["battery"], "is_charging": r["is_charging"],
                    "move_state": r["move_state"], "charge_stage": r["charge_stage"],
                    "remain_time": 0, "product_code": "FlashBotMax",
                    "run_state": r["run_state"], "charge_type": 2})

    if path == "/open-platform-service/v1/robot/get_position":
        r, f = _robot_or_fail(q.get("sn"))
        if f:
            return f
        if r["run_state"] == "OFFLINE":
            return _ok({"map_name": "", "floor": "", "position": {"x": 0, "y": 0, "z": 0}})
        return _ok({"map_name": MAP1, "floor": "1",
                    "position": {"x": r["x"], "y": r["y"], "z": 0}})

    if path == "/open-platform-service/v1/door_state":
        r, f = _robot_or_fail(q.get("sn"))
        if f:
            return f
        return _ok({"door_template_id": 5, "door_states": [
            {"door_number": k, "state": v} for k, v in sorted(r["doors"].items())]})

    if path == "/open-platform-service/v1/errand/list":
        lst = [e for e in STATE.errands if not q.get("sn") or e["sn"] == q["sn"]]
        return _ok({"total": len(lst), "list": list(reversed(lst))[:int(q.get("limit") or 10)]})

    if path == "/data-board/v1/log/error/query_list":
        return _ok({"total": 2, "offset": 0, "limit": 50, "list": [
            {"id": "e1", "sn": SN1, "mac": "AA:BB:CC:00:00:01", "product_code": "FlashBotMax",
             "task_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 5400)),
             "upload_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 5000)),
             "soft_version": "DEMO", "error_level": "Warning", "error_type": "Obstacle",
             "error_detail": "Robot dừng chờ vì có vật cản trên đường chạy (dữ liệu mẫu)",
             "error_id": "demo-1"},
            {"id": "e2", "sn": SN2, "mac": "AA:BB:CC:00:00:02", "product_code": "FlashBotMax",
             "task_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 86000)),
             "upload_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 85000)),
             "soft_version": "DEMO", "error_level": "Event", "error_type": "PowerOff",
             "error_detail": "Robot tắt máy (dữ liệu mẫu)", "error_id": "demo-2"}]})

    if path == "/data-board/v1/brief/robot":
        return _ok({"summary": {"boot_count": 1, "total_count": 2, "bind_count": 2,
                                "active_count": 2, "lively_rate": 0.5}})

    # ---------------- bản đồ
    if path == "/data-open-platform-service/v1/api/maps":
        return _ok({"count": 2, "list": [{"map_name": MAP1}, {"map_name": MAP2}]})
    if path == "/map-service/v1/open/list":
        return _ok({"shop_id": SHOP_ID, "list": [{"name": MAP1, "floor": "1", "is_used": 1},
                                                 {"name": MAP2, "floor": "2", "is_used": 2}]})
    if path == "/map-service/v1/open/current":
        r, f = _robot_or_fail(q.get("sn"))
        if f:
            return f
        els = [{"type": e["type"], "id": e["id"], "name": e["name"], "mode": e["mode"],
                "vector": e["vector_list"], "group": "", "width": 0, "max_speed": 0}
               for e in _elements()] if q.get("need_element") == "true" else []
        return _ok({"name": MAP1, "floor": "1", "shop_id": SHOP_ID, "elements": els})
    if path == "/map-service/v1/open/point":
        lst = [{"name": n, "type": m, "x": x, "y": y, "z": yaw} for n, m, x, y, yaw in POINTS]
        return _ok({"total": len(lst), "list": lst})
    if path == "/map-service/v1/open/map":
        if q.get("map_name") not in (MAP1, MAP2):
            return _fail("DATA_NOT_EXIST")
        # Ví dụ của hãng trả "origin_list" chứ không phải "origin" — giả lập giữ đúng như vậy.
        return _ok({"map_name": q["map_name"], "map_code": "demo-map-code",
                    "url": "https://demo-map.pudutech.com/%s.png" % q["map_name"].replace("#", "_"),
                    "width": W, "height": H, "resolution": RES, "origin_list": ORIGIN + [0],
                    "element_list": _elements(), "zone_list": [
                        {"id": "z-elev", "type": "elevator_area", "zone_node_list": [
                            {"vector_list": [15.0, -0.2]}, {"vector_list": [18.0, -0.2]},
                            {"vector_list": [18.0, 4.4]}, {"vector_list": [15.0, 4.4]}]}]})

    # ---------------- lệnh
    if path == "/open-platform-service/v1/custom_call" and method == "POST":
        r, f = _robot_or_fail(b.get("sn") or SN1)
        if f:
            return f
        if r["run_state"] == "OFFLINE":
            return _fail("CLOUD_OPEN_ROBOT_OFFLINE")
        xy = _point(b.get("point"))
        if not xy:
            return _fail("CLOUD_OPEN_ROBOT_PARAM_ERROR")
        tid = STATE.next_id("CALL")
        r.update(x=xy[0], y=xy[1], run_state="BUSY", move_state="ARRIVE",
                 is_charging=-1, charge_stage="IDLE")
        sn = b.get("sn") or SN1
        STATE.emit("notifyCustomCall", {"task_id": tid, "shop_id": SHOP_ID, "sn": sn,
                                        "map_name": b.get("map_name"), "point": b.get("point"),
                                        "state": "CALL_SUCCESS"})
        STATE.emit("notifyRobotStatus", {"sn": sn, "battery": r["battery"], "is_charging": -1,
                                         "run_state": "BUSY", "move_state": "ARRIVE"})
        return _ok({"task_id": tid, "queue": 0, "state": "CALLING", "remark": "demo"})

    if path == "/open-platform-service/v1/custom_call/cancel" and method == "POST":
        r = STATE.robot[SN1]
        r.update(run_state="IDLE", move_state="IDLE")
        STATE.emit("notifyCustomCall", {"task_id": b.get("task_id", ""), "sn": SN1,
                                        "state": "TASK_CANCEL"})
        return _ok()

    if path == "/open-platform-service/v1/custom_call/complete" and method == "POST":
        STATE.robot[SN1].update(run_state="IDLE", move_state="IDLE")
        STATE.emit("notifyCustomCall", {"task_id": b.get("task_id", ""), "sn": SN1,
                                        "state": "CALL_COMPLETE"})
        return _ok({"next_call_result": None})

    if path == "/open-platform-service/v1/task_errand" and method == "POST":
        sn = b.get("sn") or SN1
        r, f = _robot_or_fail(sn)
        if f:
            return f
        if r["run_state"] == "OFFLINE":
            return _fail("CLOUD_OPEN_ROBOT_OFFLINE")
        tasks = (b.get("payload") or {}).get("tasks") or []
        if not tasks or len(tasks[0].get("point_list") or []) != 2:
            return _fail("CLOUD_OPEN_ROBOT_PARAM_ERROR")
        sid = STATE.next_id("ERRAND")
        pl = tasks[0]["point_list"]
        rec = {"session_id": sid, "sn": sn, "auth": (b.get("payload") or {}).get("auth", ""),
               "task_time": int(time.time() * 1000), "create_time": int(time.time()),
               "back_mode": (b.get("payload") or {}).get("back_mode", "UNSPECIFIED"),
               "task_status": "ON_THE_WAY",
               "tasks": [{"task_id": sid + "-1", "task_name": tasks[0].get("task_name", ""),
                          "task_status": "ONGOING", "hatch_id": tasks[0].get("hatch_id", ""),
                          "point_list": [dict(p, point_status="ON_THE_WAY" if i == 0 else "AWAIT")
                                         for i, p in enumerate(pl)]}]}
        STATE.errands.append(rec)
        xy = _point(pl[0]["point"])
        if xy:
            r.update(x=xy[0], y=xy[1])
        r.update(run_state="BUSY", move_state="MOVING", is_charging=-1, charge_stage="IDLE")
        STATE.emit("notifyErrandStatus", {"sn": sn, "session_id": sid, "task_type": "REMOTE",
                                          "task_status": "ON_THE_WAY", "tasks": rec["tasks"]})
        return _ok({"code": 0, "message": "demo", "session_id": sid,
                    "task_status": "ON_THE_WAY", "queue_no": 0})

    if path == "/open-platform-service/v1/errand_action" and method == "POST":
        p = b.get("payload") or {}
        for e in STATE.errands:
            if e["session_id"] == p.get("session_id"):
                e["task_status"] = "CANCEL" if p.get("action") == "CANCEL" else "ON_THE_WAY"
                STATE.emit("notifyErrandStatus", {"sn": e["sn"], "session_id": e["session_id"],
                                                  "task_type": "REMOTE",
                                                  "task_status": e["task_status"],
                                                  "tasks": e["tasks"]})
        STATE.robot[SN1].update(run_state="IDLE", move_state="IDLE")
        return _ok({"code": 0, "message": "demo", "session_id": p.get("session_id")})

    if path == "/open-platform-service/v1/delivery_task" and method == "POST":
        r, f = _robot_or_fail(b.get("sn") or SN1)
        if f:
            return f
        r.update(run_state="BUSY", move_state="MOVING", is_charging=-1)
        return _ok({"code": 0, "message": "demo", "task_id": STATE.next_id("DELIVERY"),
                    "task_status": "ON_THE_WAY", "queue_no": 0})

    if path == "/open-platform-service/v1/delivery_action" and method == "POST":
        STATE.robot[SN1].update(run_state="IDLE", move_state="IDLE")
        return _ok({"code": 0, "message": "demo"})

    if path == "/open-platform-service/v1/control_doors" and method == "POST":
        r, f = _robot_or_fail(b.get("sn"))
        if f:
            return f
        if r["run_state"] == "OFFLINE":
            return _fail("CLOUD_OPEN_ROBOT_OFFLINE")
        out = []
        for c in (b.get("payload") or {}).get("control_states") or []:
            n = str(c.get("door_number"))
            if n in r["doors"]:
                r["doors"][n] = "OPENED" if c.get("operation") else "CLOSED"
                out.append({"door_number": n, "state": "OPENING" if c.get("operation") else "CLOSING"})
        # Callback của hãng đánh số cửa kiểu "H_01" — khác với API đọc ("1"). Giả lập giữ đúng.
        STATE.emit("notifyDoorsState", {"sn": b["sn"], "target_name": "", "door_states": [
            {"door_number": "H_%02d" % int(k), "state": v} for k, v in sorted(r["doors"].items())]})
        return _ok({"door_states": out})

    if path == "/open-platform-service/v2/recharge":
        r, f = _robot_or_fail(q.get("sn"))
        if f:
            return f
        if r["run_state"] == "OFFLINE":
            return _fail("CLOUD_OPEN_ROBOT_OFFLINE")
        tid = STATE.next_id("RECHARGE")
        r.update(x=CHARGER[0] + 0.4, y=CHARGER[1], run_state="IDLE", move_state="IDLE",
                 is_charging=1, charge_stage="CHARGING")
        STATE.emit("notifyRechargeStatus", {"sn": q["sn"], "task_id": tid, "status": "COMPLETED",
                                            "map_name": MAP1, "point": "charger-0"})
        return _ok({"desc": "Recharge task started (demo)", "task_id": tid})

    if path == "/open-platform-service/v1/position_command" and method == "POST":
        r, f = _robot_or_fail(b.get("sn"))
        if f:
            return f
        STATE.emit("notifyRobotPose", {"sn": b["sn"], "x": r["x"], "y": r["y"], "yaw": 0.0,
                                       "nearest_point": ""})
        return _ok()

    if path == "/open-platform-service/v1/robot/map/switch_in_elevator" and method == "POST":
        return _ok({"code": 0, "message": "demo"})

    return 404, json.dumps({"message": "Route Not Found"})


def image_for(url):
    """Ảnh bản đồ giả cho mọi URL demo-map.pudutech.com."""
    return MAP_PNG
