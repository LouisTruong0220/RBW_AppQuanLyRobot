# RBW App Quản Lý Robot — mã nguồn mẫu tích hợp FlashBot Max qua PUDU Open API

Mã nguồn mẫu do **Công ty Cổ phần Tập đoàn Roboworld** cung cấp cho đối tác muốn đưa robot
giao hàng **PUDU FlashBot Max** vào phần mềm quản lý của mình qua **PUDU Open Platform (Cloud API)**.

Bao trọn ba phần:

| # | Phần | API PUDU dùng | Nằm ở |
|---|---|---|---|
| 1 | **Quản lý robot từ xa** — danh sách, trạng thái, pin, vị trí, cửa khoang, lịch sử việc, sự cố | GetStoreList · StoreRobotList · GetRobotStatusV2 · GetRealtimeMapPosition · GetDoorStatus · Errands Task List · Faults/Events | `pudu_client.py` mục 1 · tab **Quản lý** |
| 2 | **Điều khiển robot từ xa** — gọi tới điểm, giao việc chạy vặt, mở/đóng khoang, về sạc | InitiateCallTask · CancelCallTask · CompleteCallTask · ErrandTask · ErrandCommands · DeliveryTask · DoorControl · OneKeyRechargeV2 · NotifyRobotReportPosition | `pudu_client.py` mục 2 · tab **Điều khiển** |
| 3 | **Trích xuất bản đồ** — ảnh laser, điểm, đường chạy, tường ảo; xuất PNG / JSON / SVG | StoreMapList · AvailableMaps · CurrentMap · GetMapDetailV2 · GetCurrentMapPoints | `map_tools.py` · tab **Bản đồ** · `tools/export_map.py` |
| + | **Nhận callback** — PUDU đẩy sự kiện về hệ thống của bạn | notifyRobotStatus · notifyErrandStatus · notifyDoorsState · notifyCustomCall · notifyRobotPose… | `server.py` (CallbackHandler) · tab **Sự kiện** |

Chỉ dùng **thư viện chuẩn Python 3.8+** — không `pip install`. Giao diện là HTML/CSS/JS thuần.

---

## Chạy thử ngay, chưa cần khoá

```bash
python server.py --demo
```

Mở `http://127.0.0.1:8765`. Chế độ demo dùng **robot giả lập** (`demo_data.py`) — không gọi PUDU,
không có robot nào cử động. Bộ giả lập **kiểm chữ ký y như cổng thật**: sửa phần ký mà sai là demo
trả đúng câu lỗi `HMAC signature does not match.` của hãng.

## Chạy với robot thật

1. Lấy **ApiAppKey + ApiAppSecret** (xem tài liệu hướng dẫn PDF đi kèm, mục 2).
2. Chép `config.example.json` thành `config.json`, điền khoá. `config.json` đã bị `.gitignore` chặn.
   Hoặc đặt biến môi trường `PUDU_APP_KEY`, `PUDU_APP_SECRET`.
3. Kiểm tra khoá (chỉ đọc):
   ```bash
   python tools/health_check.py
   ```
4. Chạy app: `python server.py` (Windows: nháy đúp `run.bat`).

App **mặc định CHỈ ĐỌC**. Muốn ra lệnh phải bật ở tab Cài đặt — khi bật, robot cử động thật.
Mọi lệnh ghi vào `logs/commands.log`.

## Cấu trúc

```
pudu_client.py         Lớp gọi API: ký HMAC-SHA1 + mỗi hàm public = một API của hãng
map_tools.py           Trích xuất bản đồ: tải ảnh laser, quy đổi mét ↔ pixel, xuất PNG/JSON/SVG
server.py              Máy chủ cục bộ: giữ secret, phục vụ web/, chặn lệnh khi chỉ đọc, nhận callback
demo_data.py           Máy chủ PUDU giả lập (--demo)
web/                   Giao diện mẫu: Quản lý · Điều khiển · Bản đồ · Sự kiện · Cài đặt
tools/health_check.py  Kiểm khoá + liệt kê cửa hàng/robot
tools/call_api.py      Gọi thử một API bất kỳ (POST phải thêm --yes)
tools/export_map.py    Xuất bản đồ ra file
examples/nodejs/       Cùng thuật toán ký bằng Node.js 18+
tests/                 Bộ thử: python -m unittest discover -s tests -v
```

## Dùng lớp gọi API trong mã của bạn

```python
from pudu_client import PuduClient

c = PuduClient(APP_KEY, APP_SECRET, region="sea")      # robot bán tại Việt Nam dùng nút "sea"

st = c.robot_status(SN)                                # {"run_state": "IDLE", "battery": 86, ...}
if st["run_state"] == "IDLE":
    r = c.send_errand(map_name, "Sảnh", "P.103", sn=SN, hatch_id="1", verification_code="2468")
    session_id = r["session_id"]                       # giữ lại để huỷ / đối chiếu callback

c.control_doors(SN, {"1": True})                       # mở cửa khoang 1
c.go_charge(SN)                                        # về trạm sạc

import map_tools
map_tools.export_map(c, "exports/", sn=SN)             # <tên bản đồ>.png / .json / .svg
```

## Mười điều đã trả giá — đọc trước khi tự viết

1. **Đường dẫn phải có tiền tố `/pudu-entry`.** Thiếu → `404 Route Not Found`.
2. **Chuỗi ký dùng query đã giải mã, xếp tên tham số theo bảng chữ cái;** URL gửi đi thì mã hoá.
3. **`Content-MD5` = base64 của chuỗi HEX md5**, không phải base64 của md5 nhị phân. Chỉ có ở POST.
4. **Đồng hồ máy chủ lệch quá 15 phút** → `x-date header is out of date`. Bật đồng bộ giờ (NTP).
5. **`limit` tối đa 100.** Xin hơn → `data: null` (không phải danh sách rỗng). `paged()` tự lật trang.
6. **Vị trí robot bị hãng ghim 10 giây.** Muốn sát thời gian thực: `request_position_reports()` + callback `notifyRobotPose`.
7. **Hỏi tuần tự.** Hãng chỉ cho 1 truy vấn đồng thời mỗi nhóm API; server.py khoá tuần tự sẵn.
8. **Số cửa khoang hai kiểu:** API đọc trả `"1"`, callback `notifyDoorsState` trả `"H_01"`. Chuẩn hoá trước khi so.
9. **GetMapDetailV2:** tài liệu ghi `origin`, ví dụ trả `origin_list` — `map_tools.map_origin()` đọc cả hai.
10. **Huỷ/hoàn tất lệnh gọi phải dùng đúng ApiAppKey đã tạo lệnh**, không thì `CLOUD_OPEN_TASK_BELONG_ERROR`.

Thêm: mức sự cố phân biệt hoa thường (`Warning` ≠ `WARNING`) — `error_logs()` tự gửi cả hai.

## Bảo mật

- **ApiAppSecret chỉ nằm ở máy chủ.** Đừng đặt nó trong mã front-end hay app di động.
- Giao diện chỉ nghe `127.0.0.1`. Cổng callback (mặc định 8766) mới cần mở ra Internet, chỉ nhận
  đúng một đường dẫn POST và bắt buộc header `CallbackCode` khớp mã hãng cấp.
- Hãng chờ callback tối đa **5 giây**, lỗi thì thử lại tối đa 3 lần — trả 200 ngay, xử lý nặng làm sau.

## Hỗ trợ

Công ty Cổ phần Tập đoàn Roboworld — nhà phân phối PUDU Robotics tại Việt Nam
Hotline **0866 153 946** · roboworld.com.vn

Tài liệu gốc của hãng: https://open.pudutech.com
