# -*- coding: utf-8 -*-
"""
pudu_client.py — Lớp gọi PUDU Open Platform (Cloud API) cho robot FlashBot Max.

Chỉ dùng thư viện chuẩn của Python 3.8+ (không cần pip install).

Thuật toán ký HMAC-SHA1 trong file này là bản đã chạy thật trên hệ thống quản lý
đội robot của Roboworld (HTTP 200 với khoá thật). Ba điều phải nhớ:

  1. Mọi đường dẫn PHẢI có tiền tố /pudu-entry. Thiếu → 404 "Route Not Found".
     Lớp này tự thêm, bạn chỉ cần truyền "/open-platform-service/...".
  2. Chuỗi ký dùng query ĐÃ GIẢI MÃ (unquote), xếp theo thứ tự chữ cái.
     URL gửi đi thì dùng query ĐÃ MÃ HOÁ. Mã hoá hai lần là lệch chữ ký.
  3. limit tối đa 100. Xin nhiều hơn thì hãng trả data = null (không phải list rỗng).

Cách dùng nhanh:

    from pudu_client import PuduClient
    c = PuduClient(app_key, app_secret, region="sea")
    print(c.robot_status("8FF0xxxxxxxxxxx"))
"""
import base64
import datetime
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from urllib.parse import quote, unquote

PREFIX = "/pudu-entry"

# Nút máy chủ của hãng. Robot bán tại Việt Nam dùng nút "sea".
REGIONS = {
    "sea": "css-open-platform.pudutech.com",   # Nhật/Hàn/Singapore — Việt Nam dùng nút này
    "cn":  "open-platform.pudutech.com",       # Trung Quốc
    "de":  "csg-open-platform.pudutech.com",   # Đức
    "us":  "csu-open-platform.pudutech.com",   # Mỹ
}

# product_code của các dòng FlashBot (Development Guide → FAQ → Robot Types)
FLASHBOT_CODES = ("FlashBotMax", "FlashBot2025", "FlashBotPro", "FlashBotUltra", "FlashBot")

MAX_LIMIT = 100


class PuduError(Exception):
    """Gọi API thất bại. Giữ nguyên mã HTTP, message và trace_id của hãng để đối chiếu."""

    def __init__(self, message, http_status=0, body="", trace_id=""):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.body = body
        self.trace_id = trace_id

    def to_dict(self):
        return {"error": self.message, "http_status": self.http_status,
                "trace_id": self.trace_id, "body": self.body[:500]}


# ------------------------------------------------------------------ ký chữ ký

def build_query(params):
    """Ghép query theo thứ tự chữ cái của tên tham số — bắt buộc, sai thứ tự là sai chữ ký.

    Giá trị kiểu list/tuple được nối bằng dấu phẩy (hãng nhận "a,b" cho tham số mảng).
    Bool được đổi thành "true"/"false".
    """
    parts = []
    for k in sorted(params):
        v = params[k]
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        if v is None or v == "":
            parts.append(str(k))
        else:
            parts.append("%s=%s" % (k, quote(str(v), safe="")))
    return "&".join(parts)


def content_md5(body_text):
    """Content-MD5 kiểu PUDU: base64( hex( md5(body) ) ) — chú ý là base64 của chuỗi HEX."""
    if not body_text:
        return ""
    return base64.b64encode(hashlib.md5(body_text.encode("utf-8")).hexdigest().encode()).decode()


def string_to_sign(x_date, method, md5, path_and_query):
    """Chuỗi ký theo tài liệu Authentication Access:

        x-date: <ngày GMT>\\n<METHOD>\\napplication/json\\napplication/json\\n<Content-MD5>\\n<path?query>
    """
    return "x-date: %s\n%s\napplication/json\napplication/json\n%s\n%s" % (
        x_date, method.upper(), md5, path_and_query)


def sign(app_secret, text):
    return base64.b64encode(
        hmac.new(app_secret.encode("utf-8"), text.encode("utf-8"), hashlib.sha1).digest()).decode()


def gmt_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def signed_headers(app_key, app_secret, method, path, query="", body_text="", x_date=None,
                   language=None):
    """Trả về (headers, url_path). path phải đã có tiền tố /pudu-entry.

    language: tuỳ chọn ("en-US", "zh-CN"...). Hãng không có tiếng Việt; không gửi thì mặc định
    tiếng Trung cho các trường mô tả. Header này không nằm trong chuỗi ký.
    """
    x_date = x_date or gmt_now()
    md5 = content_md5(body_text)
    path_to_sign = path + ("?" + unquote(query) if query else "")
    signature = sign(app_secret, string_to_sign(x_date, method, md5, path_to_sign))
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-date": x_date,
        "Authorization": 'hmac id="%s", algorithm="hmac-sha1", headers="x-date", '
                         'signature="%s"' % (app_key, signature),
    }
    if md5:
        headers["Content-MD5"] = md5
    if language:
        headers["Language"] = language
    return headers, path + ("?" + query if query else "")


def _urllib_transport(method, url, headers, body_bytes, timeout):
    req = urllib.request.Request(url, method=method, headers=headers, data=body_bytes)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.status, f.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ client

class PuduClient(object):
    """Gọi PUDU Cloud API. Mỗi hàm public ứng với MỘT API trong tài liệu hãng.

    transport: tuỳ chọn, dùng để chạy chế độ demo/kiểm thử mà không gọi mạng.
               Hàm nhận (method, url, headers, body_bytes, timeout) → (http_status, text).
    """

    def __init__(self, app_key, app_secret, region="sea", timeout=25, transport=None,
                 language=None):
        self.language = language or None
        self.app_key = app_key or ""
        self.app_secret = app_secret or ""
        self.region = region if region in REGIONS else "sea"
        self.host = REGIONS[self.region]
        self.timeout = timeout
        self.transport = transport or _urllib_transport
        self.last_exchange = None   # request/response gần nhất — giao diện mẫu hiển thị để học

    def has_credentials(self):
        return bool(self.app_key and self.app_secret)

    # ---- lõi -----------------------------------------------------------

    def request(self, path, method="GET", params=None, body=None):
        """Gọi một API, trả về JSON đầy đủ của hãng ({"message", "data", "trace_id"}).

        Ném PuduError khi HTTP khác 200 hoặc message không phải SUCCESS/ok.
        """
        if not self.has_credentials():
            raise PuduError("Chưa có ApiAppKey / ApiAppSecret (xem config.example.json).")
        method = method.upper()
        path = path if path.startswith(PREFIX) else PREFIX + path
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        query = build_query(params) if params else ""
        body_text = ""
        if method != "GET":
            body_text = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
        headers, url_path = signed_headers(self.app_key, self.app_secret, method, path,
                                           query, body_text, language=self.language)
        url = "https://%s%s" % (self.host, url_path)
        try:
            status, text = self.transport(method, url, headers,
                                          body_text.encode("utf-8") if body_text else None,
                                          self.timeout)
        except Exception as e:                       # mất mạng, DNS, hết giờ
            raise PuduError("Không kết nối được máy chủ PUDU: %s" % e)

        self.last_exchange = {
            "request": {"method": method, "path": url_path,
                        "body": json.loads(body_text) if body_text else None},
            "http_status": status,
            "response": None,
        }
        try:
            js = json.loads(text)
        except ValueError:
            self.last_exchange["response"] = text[:500]
            raise PuduError("Hãng trả về không phải JSON (HTTP %s)" % status, status, text[:500])
        self.last_exchange["response"] = js

        msg = str(js.get("message", ""))
        if status != 200 or msg.upper() not in ("SUCCESS", "OK", ""):
            raise PuduError(msg or ("HTTP %s" % status), status, text[:500],
                            js.get("trace_id") or js.get("traceId") or "")
        return js

    def data(self, path, method="GET", params=None, body=None):
        """Như request() nhưng chỉ trả phần "data" (luôn là dict, không bao giờ None)."""
        return self.request(path, method, params, body).get("data") or {}

    def paged(self, path, params=None, total_keys=("total", "count")):
        """Lấy hết các trang của một API có limit/offset. Tự lật trang 100 dòng một."""
        params = dict(params or {})
        items, offset = [], 0
        while True:
            params.update(limit=MAX_LIMIT, offset=offset)
            d = self.data(path, params=params)
            page = d.get("list") or []
            items += page
            total = next((int(d[k]) for k in total_keys if d.get(k) is not None), len(items))
            if not page or len(items) >= total:
                return items
            offset += MAX_LIMIT

    # ==================================================================
    # 0. KIỂM TRA KẾT NỐI
    # ==================================================================

    def health_check(self):
        """Health Check (Quick Start, bước 2) — khoá đúng thì trả SUCCESS."""
        return self.request("/data-open-platform-service/v1/api/healthCheck")

    # ==================================================================
    # 1. QUẢN LÝ ROBOT TỪ XA
    # ==================================================================

    def list_shops(self):
        """GetStoreList — mọi cửa hàng mà khoá này được cấp quyền."""
        return self.paged("/data-open-platform-service/v1/api/shop")

    def list_robots(self, shop_id=None, product_code=None):
        """StoreRobotList — robot đang gắn vào cửa hàng (sn, mac, product_code, nickname)."""
        return self.paged("/data-open-platform-service/v1/api/robot",
                          {"shop_id": shop_id, "product_code": product_code})

    def robot_status(self, sn):
        """GetRobotStatusV2 — pin, run_state (OFFLINE/DISABLE/BUSY/IDLE), move_state, charge_stage.

        Gửi lệnh khi run_state = IDLE. BUSY thì lệnh gọi robot sẽ xếp hàng.
        """
        return self.data("/open-platform-service/v2/status/get_by_sn", params={"sn": sn})

    def robot_position(self, sn):
        """GetRealtimeMapPosition — toạ độ robot trên bản đồ (mét).

        ⚠ Hãng ghim kết quả 10 giây: gọi dày hơn vẫn nhận số cũ. Muốn sát thời gian thực
        thì dùng request_position_reports() + callback notifyRobotPose.
        """
        return self.data("/open-platform-service/v1/robot/get_position", params={"sn": sn})

    def door_state(self, sn):
        """GetDoorStatus — trạng thái từng cửa khoang + door_template_id (kiểu bố trí khoang)."""
        return self.data("/open-platform-service/v1/door_state", params={"sn": sn})

    def errand_list(self, sn=None, session_id=None, limit=20, offset=0):
        """Errands Task List — lịch sử việc chạy vặt (errand) đã giao, kèm trạng thái từng điểm."""
        return self.data("/open-platform-service/v1/errand/list", params={
            "sn": sn, "session_id": session_id, "limit": min(limit, MAX_LIMIT), "offset": offset})

    def error_logs(self, start_time, end_time, timezone_offset=7, shop_id=None,
                   levels=("Fatal", "Error", "Warning"), limit=50, offset=0):
        """Faults | Events — Query List. Dữ liệu trễ ~10 phút, mỗi lần hỏi nên ≤ 7 ngày.

        ⚠ Mức lỗi PHÂN BIỆT HOA THƯỜNG ("Warning" và "WARNING" là hai mức khác nhau trên
        dữ liệu thật) — hàm này tự gửi cả hai dạng.
        """
        lv = []
        for x in levels or ():
            lv += [x, x.upper()] if x != x.upper() else [x]
        return self.data("/data-board/v1/log/error/query_list", params={
            "start_time": int(start_time), "end_time": int(end_time),
            "timezone_offset": timezone_offset, "shop_id": shop_id,
            "error_levels": lv, "limit": min(limit, MAX_LIMIT), "offset": offset})

    def battery_health(self, sn, start_time, end_time, timezone_offset=7):
        """Battery Health — bản ghi sức khoẻ pin mới nhất (soh, cycle, nhiệt độ)."""
        return self.data("/data-board/v1/log/battery/query_list", params={
            "sn": sn, "start_time": int(start_time), "end_time": int(end_time),
            "timezone_offset": timezone_offset, "limit": 1, "offset": 0})

    def robot_overview(self, start_time, end_time, timezone_offset=7, shop_id=None):
        """Machine Overview — số máy bật/gắn/kích hoạt trong kỳ."""
        return self.data("/data-board/v1/brief/robot", params={
            "start_time": int(start_time), "end_time": int(end_time),
            "timezone_offset": timezone_offset, "shop_id": shop_id})

    # ==================================================================
    # 2. ĐIỀU KHIỂN ROBOT TỪ XA  — MỌI HÀM DƯỚI ĐÂY LÀM ROBOT CỬ ĐỘNG THẬT
    # ==================================================================

    def call_robot(self, map_name, point, point_type="table", sn=None, shop_id=None,
                   call_mode=None, mode_data=None, do_not_queue=False, priority=None):
        """InitiateCallTask — gọi robot tới một điểm. Trả {task_id, state, queue}.

        Truyền sn để gọi đích danh; chỉ truyền shop_id để hãng chọn ngẫu nhiên máy rảnh.
        Lưu task_id lại để huỷ (cancel_call) hoặc hoàn tất (complete_call).
        Việc gọi tự hết hạn sau 30 phút nếu không thực hiện được.
        """
        body = {"map_name": map_name, "point": point, "point_type": point_type,
                "do_not_queue": bool(do_not_queue)}
        if sn:
            body["sn"] = sn
        if shop_id:
            body["shop_id"] = int(shop_id)
        if call_mode:
            body["call_mode"] = call_mode
        if mode_data:
            body["mode_data"] = mode_data
        if priority:
            body["priority"] = int(priority)
        return self.data("/open-platform-service/v1/custom_call", "POST", body=body)

    def cancel_call(self, task_id=None, sn=None, auto_back=None):
        """CancelCallTask — huỷ một lệnh gọi (task_id) hoặc mọi lệnh gọi dở của máy (sn).

        ⚠ Phải dùng CÙNG ApiAppKey đã tạo lệnh, không thì CLOUD_OPEN_TASK_BELONG_ERROR.
        """
        body = {}
        if task_id:
            body["task_id"] = task_id
        if sn:
            body["sn"] = sn
        if auto_back is not None:
            body["is_auto_back"] = bool(auto_back)
        return self.request("/open-platform-service/v1/custom_call/cancel", "POST", body=body)

    def complete_call(self, task_id, next_call_task=None):
        """CompleteCallTask — kết thúc lệnh gọi (chế độ không tự hoàn tất), có thể nối lệnh kế."""
        body = {"task_id": task_id}
        if next_call_task:
            body["next_call_task"] = next_call_task
        return self.data("/open-platform-service/v1/custom_call/complete", "POST", body=body)

    def send_errand(self, map_name, pickup_point, dropoff_point, sn=None, shop_id=None,
                    task_name=None, task_desc="", hatch_id=None, auth=None, back_mode=None,
                    verification_code=None, phone_num=None, phone_code=None,
                    out_order_id=None, priority=None, need_queue=None):
        """ErrandTask — việc CHẠY VẶT đặc trưng của FlashBot: tới điểm lấy hàng rồi giao tới điểm nhận.

        Hiện robot chỉ nhận ĐÚNG 2 điểm mỗi việc: điểm thứ nhất để bỏ hàng vào, điểm thứ hai
        để người nhận lấy ra. Trả {session_id, task_status, queue_no}.
        back_mode: UNSPECIFIED (theo cài đặt máy) · RETURN (không ai lấy thì quay về) ·
                   BACK_START (nhiều món chưa lấy thì quay lại từng điểm bỏ hàng).
        """
        def pt(name, remark):
            p = {"map_name": map_name, "point": name, "point_type": "table"}
            if remark:
                p["remark"] = remark
            return p

        drop = pt(dropoff_point, None)
        if verification_code:
            drop["verification_code"] = str(verification_code)
        if phone_num:
            drop["phone_num"] = str(phone_num)
            drop["phone_code"] = phone_code or "+84"
        task = {"task_name": task_name or ("%s -> %s" % (pickup_point, dropoff_point)),
                "task_desc": task_desc or "",
                "point_list": [pt(pickup_point, None), drop]}
        if hatch_id:
            task["hatch_id"] = str(hatch_id)
        if out_order_id:
            task["out_order_id"] = str(out_order_id)
        body = {"payload": {"tasks": [task]}}
        if auth:
            body["payload"]["auth"] = str(auth)
        if back_mode:
            body["payload"]["back_mode"] = back_mode
        if sn:
            body["sn"] = sn
        if shop_id:
            body["shop_id"] = int(shop_id)
        if priority:
            body["priority"] = int(priority)
        if need_queue is not None:
            body["need_queue"] = bool(need_queue)
        return self.data("/open-platform-service/v1/task_errand", "POST", body=body)

    def errand_action(self, sn, session_id, action="CANCEL", auth=None, hatch_id=None):
        """ErrandCommands — CANCEL (huỷ) hoặc RETRY (thử lại) một việc chạy vặt đang chạy."""
        payload = {"session_id": session_id, "action": action}
        if auth:
            payload["auth"] = auth
        if hatch_id:
            payload["hatch_id"] = hatch_id
        return self.data("/open-platform-service/v1/errand_action", "POST",
                         body={"sn": sn, "payload": payload})

    def send_delivery(self, sn, destinations, map_name=None, execute=True, sort="AUTO"):
        """DeliveryTask — giao hàng tới một hoặc nhiều điểm (mỗi điểm một mã đơn).

        destinations: list[str] tên điểm, hoặc list[dict] {"destination", "id", ...}.
        """
        dests = []
        for i, d in enumerate(destinations):
            if isinstance(d, str):
                d = {"destination": d, "id": "ORDER-%d" % (i + 1)}
            if map_name and "map_info" not in d:
                d = dict(d, map_info={"map_name": map_name})
            dests.append(d)
        body = {"sn": sn, "payload": {"type": "NEW", "delivery_sort": sort,
                                      "execute_task": bool(execute),
                                      "trays": [{"destinations": dests}]}}
        return self.data("/open-platform-service/v1/delivery_task", "POST", body=body)

    def delivery_action(self, sn, action, task_id=None):
        """DeliveryCommands — START | COMPLETE | CANCEL_ALL_DELIVERY | PAUSE | RESUME."""
        body = {"sn": sn, "payload": {"action": action}}
        if task_id:
            body["task_id"] = task_id
        return self.data("/open-platform-service/v1/delivery_action", "POST", body=body)

    def control_doors(self, sn, doors):
        """DoorControl — mở/đóng cửa khoang. doors = {"1": True, "2": False} (True = mở).

        Điều kiện: robot online và ĐANG RẢNH. Số cửa lấy từ door_state().
        """
        states = [{"door_number": str(k), "operation": bool(v)} for k, v in doors.items()]
        return self.data("/open-platform-service/v1/control_doors", "POST",
                         body={"sn": sn, "payload": {"control_states": states}})

    def go_charge(self, sn):
        """OneKeyRechargeV2 — về trạm sạc. Trả {task_id, desc}."""
        return self.data("/open-platform-service/v2/recharge", params={"sn": sn})

    def request_position_reports(self, sn, interval=2, times=60):
        """NotifyRobotReportPosition — yêu cầu robot tự đẩy vị trí qua callback notifyRobotPose.

        interval ≥ 1 giây, times ≤ 1000 lần.
        """
        return self.request("/open-platform-service/v1/position_command", "POST", body={
            "sn": sn, "payload": {"interval": max(1, int(interval)),
                                  "times": max(1, min(1000, int(times)))}})

    def switch_map_in_elevator(self, sn, map_name, floor):
        """SwitchMapInElevator — đổi bản đồ tầng khi robot đang ở trong thang máy."""
        return self.data("/open-platform-service/v1/robot/map/switch_in_elevator", "POST",
                         body={"sn": sn, "payload": {"map": {"name": map_name,
                                                             "floor": str(floor)}}})

    # ==================================================================
    # 3. TRÍCH XUẤT BẢN ĐỒ
    # ==================================================================

    def shop_maps(self, shop_id):
        """StoreMapList — tên mọi bản đồ của một cửa hàng."""
        return self.data("/data-open-platform-service/v1/api/maps", params={"shop_id": shop_id})

    def robot_maps(self, sn):
        """AvailableMaps — bản đồ robot đang có (is_used = 1 là bản đang dùng)."""
        return self.data("/map-service/v1/open/list", params={"sn": sn})

    def current_map(self, sn, with_elements=True):
        """CurrentMap — bản đồ đang dùng + phần tử (điểm, đường chạy, tường ảo...)."""
        return self.data("/map-service/v1/open/current",
                         params={"sn": sn, "need_element": bool(with_elements)})

    def map_points(self, sn):
        """GetCurrentMapPoints — mọi điểm đến được trên bản đồ hiện tại (tên, loại, x, y)."""
        return self.paged("/map-service/v1/open/point", {"sn": sn})

    def map_detail(self, shop_id, map_name):
        """GetMapDetailV2 — ảnh bản đồ laser (url) + width/height/resolution/origin + phần tử.

        ⚠ Tài liệu ghi trường "origin" nhưng ví dụ của hãng trả "origin_list" — đọc cả hai
        (map_tools.map_origin làm việc này).
        """
        return self.data("/map-service/v1/open/map",
                         params={"shop_id": shop_id, "map_name": map_name})
