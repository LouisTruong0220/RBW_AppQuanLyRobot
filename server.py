# -*- coding: utf-8 -*-
"""
server.py — App mẫu Quản lý · Điều khiển · Bản đồ robot FlashBot Max qua PUDU Open API.

    python server.py            # dùng khoá thật trong config.json (hoặc biến môi trường)
    python server.py --demo     # chạy thử với robot GIẢ LẬP, không cần khoá
    python server.py --port 9000 --no-browser

Kiến trúc (nên giữ nguyên khi đưa vào hệ thống của bạn):

    Trình duyệt ──► server.py (giữ ApiAppSecret, ký HMAC) ──► PUDU Cloud API
                        ▲
    PUDU Cloud ─────────┘  cổng callback riêng (kiểm CallbackCode)

  · Trình duyệt KHÔNG BAO GIỜ thấy ApiAppSecret. Ký ở máy chủ rồi mới gọi hãng.
  · Giao diện chỉ nghe 127.0.0.1. Cổng callback mới cần mở ra ngoài, và nó chỉ nhận
    đúng một đường dẫn POST.
  · Mặc định CHỈ ĐỌC. Bật allow_commands mới ra lệnh được — robot sẽ cử động thật.
"""
import argparse
import hmac
import io
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import pudu_client                                          # noqa: E402
from pudu_client import PuduClient, PuduError, FLASHBOT_CODES, REGIONS   # noqa: E402
import map_tools                                            # noqa: E402

CONFIG_FILE = os.path.join(ROOT, "config.json")
LOG_DIR = os.path.join(ROOT, "logs")
WEB_DIR = os.path.join(ROOT, "web")
VERSION = "1.0.0"

DEFAULT_CONFIG = {
    "app_key": "",
    "app_secret": "",
    "region": "sea",
    "language": "",
    "timezone_offset": 7,
    "allow_commands": False,
    "callback": {"enabled": False, "host": "0.0.0.0", "port": 8766,
                 "path": "/pudu/callback", "callback_code": ""},
}

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}


# ================================================================ cấu hình

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_FILE):
        with io.open(CONFIG_FILE, encoding="utf-8") as f:
            user = json.load(f)
        cb = dict(cfg["callback"], **(user.get("callback") or {}))
        cfg.update(user)
        cfg["callback"] = cb
    # Biến môi trường thắng file — tiện khi chạy trên máy chủ / container
    cfg["app_key"] = os.environ.get("PUDU_APP_KEY") or cfg.get("app_key") or ""
    cfg["app_secret"] = os.environ.get("PUDU_APP_SECRET") or cfg.get("app_secret") or ""
    cfg["callback"]["callback_code"] = (os.environ.get("PUDU_CALLBACK_CODE")
                                        or cfg["callback"].get("callback_code") or "")
    return cfg


def save_allow_commands(value):
    """Chỉ ghi công tắc ra lệnh — không bao giờ ghi khoá từ giao diện."""
    data = {}
    if os.path.exists(CONFIG_FILE):
        with io.open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    data["allow_commands"] = bool(value)
    with io.open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_log(name, line):
    os.makedirs(LOG_DIR, exist_ok=True)
    with io.open(os.path.join(LOG_DIR, name), "a", encoding="utf-8") as f:
        f.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))


def mask(key):
    if len(key) > 12:
        return key[:6] + "…" + key[-4:]
    return (key[:4] + "…") if key else ""


# ================================================================ trạng thái app

class Cache(object):
    """Đệm ngắn — tránh gọi hãng quá dày (vị trí robot phía hãng vốn đã ghim 10 giây)."""

    def __init__(self):
        self.store, self.lock = {}, threading.Lock()

    def get(self, key, ttl, make):
        now = time.time()
        with self.lock:
            hit = self.store.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = make()
        with self.lock:
            self.store[key] = (time.time(), value)
        return value

    def clear(self):
        with self.lock:
            self.store.clear()


class EventStore(object):
    """Sự kiện callback nhận được — giao diện hỏi theo id tăng dần."""

    def __init__(self, keep=500):
        self.items, self.next_id, self.keep = [], 1, keep
        self.lock = threading.Lock()

    def add(self, payload, source="pudu"):
        with self.lock:
            ev = {"id": self.next_id, "received_at": int(time.time()), "source": source,
                  "callback_type": payload.get("callback_type", ""),
                  "data": payload.get("data") or {}}
            self.next_id += 1
            self.items.append(ev)
            self.items = self.items[-self.keep:]
        os.makedirs(LOG_DIR, exist_ok=True)
        with io.open(os.path.join(LOG_DIR, "callbacks.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    def since(self, after):
        with self.lock:
            return [e for e in self.items if e["id"] > after]


class App(object):
    def __init__(self, demo=False):
        self.demo = demo
        self.cache = Cache()
        self.events = EventStore()
        # Hãng chỉ cho 1 truy vấn đồng thời trên cùng nhóm API — gọi tuần tự cho chắc.
        self.pudu_lock = threading.Lock()
        self.images = {}

    def config(self):
        cfg = load_config()
        if self.demo:
            import demo_data
            cfg["app_key"], cfg["app_secret"] = demo_data.DEMO_KEY, demo_data.DEMO_SECRET
        return cfg

    def client(self, cfg=None):
        cfg = cfg or self.config()
        transport = None
        if self.demo:
            import demo_data
            transport = demo_data.transport
        base = transport or pudu_client._urllib_transport

        def serial(*a):
            with self.pudu_lock:
                return base(*a)
        return PuduClient(cfg["app_key"], cfg["app_secret"], cfg.get("region", "sea"),
                          transport=serial, language=cfg.get("language") or None)

    def image_loader(self, url):
        if url not in self.images:
            if self.demo:
                import demo_data
                self.images[url] = demo_data.image_for(url)
            else:
                self.images[url] = map_tools.download_image(url)
        return self.images[url]

    def drain_demo_events(self):
        if self.demo:
            import demo_data
            for ev in demo_data.STATE.drain():
                self.events.add(ev, source="demo")


APP = None


# ================================================================ lệnh ĐỌC

def q1(q, name, default=None):
    v = q.get(name)
    return v[0] if v else default


def read_shops(app, q):
    return {"list": app.client().list_shops()}


def read_robots(app, q):
    rs = app.client().list_robots(shop_id=q1(q, "shop_id"))
    if q1(q, "flashbot_only", "1") == "1":
        rs = [r for r in rs if r.get("product_code") in FLASHBOT_CODES]
    rs.sort(key=lambda r: (r.get("shop_name", ""), r.get("nickname") or r.get("sn", "")))
    return {"list": rs}


def read_robot(app, q):
    """Một robot: trạng thái V2 + vị trí + cửa khoang. Lỗi phần nào ghi riêng phần đó."""
    sn = q1(q, "sn")
    if not sn:
        raise PuduError("Thiếu sn")
    c = app.client()
    out = {"sn": sn}
    for key, fn in (("status", c.robot_status), ("position", c.robot_position),
                    ("doors", c.door_state)):
        try:
            out[key] = fn(sn)
        except PuduError as e:
            out[key + "_error"] = e.message
    return out


def read_statuses(app, q):
    """Trạng thái nhiều máy — hỏi TUẦN TỰ, đệm 15 giây cho từng SN."""
    c = app.client()
    out = {}
    for sn in [s for s in (q1(q, "sn", "") or "").split(",") if s][:100]:
        def one(sn=sn):
            try:
                return c.robot_status(sn)
            except PuduError as e:
                return {"error": e.message}
        out[sn] = app.cache.get("st:" + sn, 15, one)
    return {"map": out}


def read_map(app, q):
    sn, shop_id, name = q1(q, "sn"), q1(q, "shop_id"), q1(q, "map_name")
    b = map_tools.fetch_map_bundle(app.client(), sn=sn, shop_id=shop_id, map_name=name,
                                   image_loader=app.image_loader)
    js = map_tools.bundle_to_json(b)
    js["image"] = ("/api/map-image?url=" + _quote(b["image_url"])) if b["image_url"] else ""
    return js


def read_robot_maps(app, q):
    return app.client().robot_maps(q1(q, "sn"))


def read_errands(app, q):
    return app.client().errand_list(sn=q1(q, "sn"), limit=int(q1(q, "limit", 20)))


def read_errors(app, q):
    cfg = app.config()
    hours = max(1, min(int(q1(q, "hours", 24)), 24 * 7))     # hãng khuyên mỗi lần ≤ 7 ngày
    end = int(time.time())
    return app.client(cfg).error_logs(end - hours * 3600, end, cfg.get("timezone_offset", 7),
                                      shop_id=q1(q, "shop_id"))


def _quote(s):
    from urllib.parse import quote
    return quote(s, safe="")


READS = {
    # tên: (hàm, số giây đệm)
    "shops": (read_shops, 120),
    "robots": (read_robots, 60),
    "robot": (read_robot, 5),
    "statuses": (read_statuses, 0),
    "map": (read_map, 60),
    "robot-maps": (read_robot_maps, 60),
    "errands": (read_errands, 5),
    "errors": (read_errors, 120),
}


# ================================================================ lệnh GHI (robot cử động thật)

def run_command(app, body):
    cfg = app.config()
    if not cfg.get("allow_commands"):
        raise PuduError("App đang ở chế độ CHỈ ĐỌC. Vào tab Cài đặt bật 'Cho phép ra lệnh' "
                        "nếu thật sự muốn điều khiển robot.")
    action = body.get("action") or ""
    sn = body.get("sn") or ""
    c = app.client(cfg)

    if action == "call":
        r = c.call_robot(body["map_name"], body["point"], body.get("point_type") or "table", sn=sn)
        summary = "gọi robot tới điểm %s" % body["point"]
    elif action == "cancel_call":
        r = c.cancel_call(task_id=body.get("task_id") or None, sn=None if body.get("task_id") else sn)
        summary = "huỷ lệnh gọi %s" % (body.get("task_id") or "(mọi lệnh của máy)")
    elif action == "complete_call":
        r = c.complete_call(body["task_id"])
        summary = "hoàn tất lệnh gọi %s" % body["task_id"]
    elif action == "errand":
        r = c.send_errand(body["map_name"], body["pickup"], body["dropoff"], sn=sn,
                          hatch_id=body.get("hatch_id") or None,
                          verification_code=body.get("verification_code") or None,
                          back_mode=body.get("back_mode") or None,
                          task_desc=body.get("note") or "")
        summary = "chạy vặt %s → %s" % (body["pickup"], body["dropoff"])
    elif action == "errand_cancel":
        r = c.errand_action(sn, body["session_id"], "CANCEL")
        summary = "huỷ việc chạy vặt %s" % body["session_id"]
    elif action == "delivery":
        r = c.send_delivery(sn, body["destinations"], map_name=body.get("map_name"))
        summary = "giao hàng tới %s" % ", ".join(body["destinations"])
    elif action == "delivery_action":
        r = c.delivery_action(sn, body["command"])
        summary = "lệnh giao hàng %s" % body["command"]
    elif action == "doors":
        doors = {str(k): bool(v) for k, v in (body.get("doors") or {}).items()}
        if not doors:
            raise PuduError("Chưa chọn cửa khoang nào")
        r = c.control_doors(sn, doors)
        summary = "cửa khoang " + ", ".join("%s:%s" % (k, "mở" if v else "đóng")
                                            for k, v in doors.items())
    elif action == "charge":
        r = c.go_charge(sn)
        summary = "về trạm sạc"
    elif action == "report_position":
        r = c.request_position_reports(sn, body.get("interval", 2), body.get("times", 30))
        summary = "yêu cầu báo vị trí %s lần" % body.get("times", 30)
    else:
        raise PuduError("Không biết lệnh: %s" % action)

    write_log("commands.log", "%s\t%s\t%s" % (sn, action, summary))
    app.cache.clear()
    app.drain_demo_events()
    return {"ok": True, "summary": summary, "result": r, "exchange": c.last_exchange}


# ================================================================ HTTP giao diện

class UIHandler(BaseHTTPRequestHandler):
    server_version = "RBW-FlashBot-Sample/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, ctype, data, extra=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False))

    def _error(self, e, code=502):
        if isinstance(e, PuduError):
            self._json(dict(e.to_dict(), ok=False), code)
        else:
            self._json({"ok": False, "error": str(e)}, code)

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            return self._api_get(u.path[5:], parse_qs(u.query))
        return self._static(u.path)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if rel.startswith(".."):
            return self._send(403, "text/plain; charset=utf-8", "Forbidden")
        f = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(f):
            return self._send(404, "text/plain; charset=utf-8", "Not found: " + path)
        with open(f, "rb") as fh:
            self._send(200, MIME.get(os.path.splitext(f)[1].lower(), "application/octet-stream"),
                       fh.read())

    def _api_get(self, name, q):
        app = APP
        cfg = app.config()
        if name == "system":
            cb = cfg["callback"]
            return self._json({
                "version": VERSION, "demo": app.demo,
                "has_key": bool(cfg["app_key"] and cfg["app_secret"]),
                "key_masked": mask(cfg["app_key"]),
                "region": cfg.get("region", "sea"), "host": REGIONS.get(cfg.get("region", "sea")),
                "allow_commands": bool(cfg.get("allow_commands")),
                "callback": {"enabled": bool(cb.get("enabled")) or app.demo,
                             "port": cb.get("port"), "path": cb.get("path"),
                             "has_code": bool(cb.get("callback_code"))},
            })
        if name == "events":
            app.drain_demo_events()
            return self._json({"list": app.events.since(int(q1(q, "after", 0) or 0))})
        if name == "map-image":
            try:
                png = app.image_loader(q1(q, "url", ""))
            except Exception as e:
                return self._error(e)
            return self._send(200, "image/png", png)
        if name == "map-export":
            return self._map_export(app, q)
        if name not in READS:
            return self._json({"ok": False, "error": "Không có API đọc tên '%s'" % name}, 404)
        if not (cfg["app_key"] and cfg["app_secret"]):
            return self._json({"ok": False, "need_key": True,
                               "error": "Chưa có khoá — điền config.json hoặc chạy --demo"}, 428)
        fn, ttl = READS[name]
        key = name + "?" + json.dumps(sorted(q.items()), ensure_ascii=False)
        try:
            data = app.cache.get(key, ttl, lambda: fn(app, q)) if ttl else fn(app, q)
            return self._json(data)
        except PuduError as e:
            return self._error(e)
        except Exception as e:
            traceback.print_exc()
            return self._error(e, 500)

    def _map_export(self, app, q):
        fmt = q1(q, "format", "json")
        try:
            b = map_tools.fetch_map_bundle(app.client(), sn=q1(q, "sn"), shop_id=q1(q, "shop_id"),
                                           map_name=q1(q, "map_name"), image_loader=app.image_loader)
        except Exception as e:
            return self._error(e)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in b["map_name"])
        disp = {"Content-Disposition": 'attachment; filename="%s.%s"' % (safe, fmt)}
        if fmt == "png" and b["image_bytes"]:
            return self._send(200, "image/png", b["image_bytes"], disp)
        if fmt == "svg":
            pos = b["robot_position"]
            svg = map_tools.build_svg(b["image_bytes"], b["geometry"], b["elements"],
                                      (pos["x"], pos["y"]) if pos else None, b["map_name"])
            return self._send(200, "image/svg+xml", svg, disp)
        return self._send(200, "application/json; charset=utf-8",
                          json.dumps(map_tools.bundle_to_json(b), ensure_ascii=False, indent=1), disp)

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except ValueError:
            return self._json({"ok": False, "error": "Body không phải JSON"}, 400)
        if u.path == "/api/settings":
            save_allow_commands(bool(body.get("allow_commands")))
            write_log("commands.log", "-\tsettings\tallow_commands=%s" % bool(body.get("allow_commands")))
            return self._json({"ok": True})
        if u.path == "/api/command":
            try:
                return self._json(run_command(APP, body))
            except PuduError as e:
                write_log("commands.log", "%s\t%s\tLỖI: %s" % (body.get("sn", ""), body.get("action"), e))
                return self._error(e)
            except KeyError as e:
                return self._json({"ok": False, "error": "Thiếu tham số %s" % e}, 400)
            except Exception as e:
                traceback.print_exc()
                return self._error(e, 500)
        return self._json({"ok": False, "error": "Không có đường dẫn này"}, 404)


# ================================================================ HTTP callback

class CallbackHandler(BaseHTTPRequestHandler):
    """Nhận callback của PUDU. Chỉ một đường dẫn POST, bắt buộc đúng CallbackCode.

    Hãng chờ tối đa 5 giây, quá là bỏ và thử lại tối đa 3 lần — nên trả 200 NGAY,
    việc nặng (ghi CSDL, đẩy thông báo) làm sau.
    """
    server_version = "RBW-Callback/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply(405, {"error": "POST only"})

    def do_POST(self):
        cfg = APP.config()["callback"]
        if urlparse(self.path).path != cfg.get("path", "/pudu/callback"):
            return self._reply(404, {"error": "not found"})
        expect = cfg.get("callback_code") or ""
        got = self.headers.get("CallbackCode") or ""
        if not expect or not hmac.compare_digest(expect.encode(), got.encode()):
            write_log("callbacks-rejected.log", "%s\t%s" % (self.client_address[0], self.path))
            return self._reply(401, {"error": "invalid CallbackCode"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return self._reply(400, {"error": "invalid json"})
        APP.events.add(payload, source="pudu")
        self._reply(200, {"code": 0, "message": "ok"})


class SingleServer(ThreadingHTTPServer):
    """Python bật SO_REUSEADDR mặc định; trên Windows điều đó cho phép HAI bản app cùng nghe
    một cổng mà không báo lỗi — trình duyệt rơi vào bản cũ. Tắt đi trên Windows."""
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def utf8_console():
    """Console Windows mặc định cp1252/cp437: in tiếng Việt sẽ ném UnicodeEncodeError."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    global APP
    utf8_console()
    ap = argparse.ArgumentParser(description="App mẫu quản lý FlashBot Max qua PUDU Open API")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--demo", action="store_true", help="chạy với robot giả lập, không cần khoá")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    APP = App(demo=a.demo)
    cfg = APP.config()
    url = "http://127.0.0.1:%d" % a.port
    print("=" * 64)
    print(" ROBOWORLD · APP MẪU TÍCH HỢP FLASHBOT MAX (PUDU OPEN API) v%s" % VERSION)
    print("=" * 64)
    print(" Giao diện   : %s" % url)
    print(" Chế độ      : %s" % ("DEMO — robot giả lập" if a.demo else "THẬT — nút %s" % cfg["region"]))
    print(" Khoá        : %s" % (mask(cfg["app_key"]) if cfg["app_key"] else "CHƯA CÓ — xem config.example.json"))
    print(" Ra lệnh     : %s" % ("ĐANG BẬT — robot sẽ cử động thật" if cfg.get("allow_commands") else "tắt (chỉ đọc)"))

    try:
        ui = SingleServer(("127.0.0.1", a.port), UIHandler)
    except OSError:
        print("\n Cổng %d đang có chương trình khác. Đóng cửa sổ cũ hoặc dùng --port khác.\n" % a.port)
        sys.exit(1)

    cb = cfg["callback"]
    if cb.get("enabled") and not a.demo:
        if not cb.get("callback_code"):
            print(" Callback    : BỎ QUA — chưa có callback_code (hãng cấp khi duyệt callback URL)")
        else:
            try:
                cbs = SingleServer((cb.get("host", "0.0.0.0"), int(cb.get("port", 8766))), CallbackHandler)
                threading.Thread(target=cbs.serve_forever, daemon=True).start()
                print(" Callback    : nghe %s:%s%s" % (cb.get("host"), cb.get("port"), cb.get("path")))
            except OSError as e:
                print(" Callback    : KHÔNG mở được cổng %s (%s)" % (cb.get("port"), e))
    print(" Dừng app    : Ctrl + C")
    print("=" * 64)

    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        ui.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    finally:
        ui.server_close()


if __name__ == "__main__":
    main()
