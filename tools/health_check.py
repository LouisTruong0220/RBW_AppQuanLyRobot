# -*- coding: utf-8 -*-
"""
Bước đầu tiên sau khi nhận khoá: kiểm tra khoá sống + khoá thấy robot nào.

    python tools/health_check.py          # khoá thật trong config.json
    python tools/health_check.py --demo   # robot giả lập

Chỉ ĐỌC — không gửi lệnh nào làm robot cử động.
"""
import argparse
from common import make_client
from pudu_client import PuduError, FLASHBOT_CODES

ap = argparse.ArgumentParser()
ap.add_argument("--demo", action="store_true")
a = ap.parse_args()
c = make_client(a.demo)

print("1) Health Check ...", end=" ")
try:
    c.health_check()
    print("OK")
except PuduError as e:
    print("LỖI: %s (HTTP %s)" % (e.message, e.http_status))
    print("   - 'HMAC signature does not match' → secret sai, hoặc đồng hồ máy lệch quá 15 phút")
    print("   - 'Route Not Found'                → thiếu tiền tố /pudu-entry hoặc sai nút máy chủ")
    raise SystemExit(1)

shops = c.list_shops()
print("2) Cửa hàng khoá này thấy: %d" % len(shops))
for s in shops:
    print("   - %s  (shop_id %s)" % (s.get("shop_name"), s.get("shop_id")))

robots = c.list_robots()
fb = [r for r in robots if r.get("product_code") in FLASHBOT_CODES]
print("3) Robot: %d (FlashBot: %d)" % (len(robots), len(fb)))
for r in fb:
    try:
        st = c.robot_status(r["sn"])
        info = "%s · pin %s%%" % (st.get("run_state"), st.get("battery"))
    except PuduError as e:
        info = "không đọc được trạng thái: %s" % e.message
    print("   - %-22s %-18s %s" % (r.get("nickname") or "", r["sn"], info))
print("\nXong. Khoá dùng được — chạy tiếp: python server.py")
