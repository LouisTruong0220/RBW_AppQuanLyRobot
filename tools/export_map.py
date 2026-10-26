# -*- coding: utf-8 -*-
"""
Xuất bản đồ robot ra 3 file để đưa vào hệ thống của bạn:

    <tên>.png   ảnh laser gốc của robot (255 sàn trống · 0 vật cản · 128 chưa quét)
    <tên>.json  kích thước, độ phân giải, gốc toạ độ, mọi điểm/đường chạy/tường ảo (mét + pixel)
    <tên>.svg   ảnh + lớp phủ, mở thẳng bằng trình duyệt

    python tools/export_map.py --sn 8FF0xxxxxxxx                    # bản đồ robot đang dùng
    python tools/export_map.py --shop-id 123 --map-name "1#1#Tang-1"  # bản đồ bất kỳ của cửa hàng
    python tools/export_map.py --demo --sn DEMO-FBMAX-0001
"""
import argparse
import os
from common import make_client, ROOT
import map_tools

ap = argparse.ArgumentParser()
ap.add_argument("--sn")
ap.add_argument("--shop-id")
ap.add_argument("--map-name")
ap.add_argument("--out", default=os.path.join(ROOT, "exports"))
ap.add_argument("--demo", action="store_true")
a = ap.parse_args()
if not a.sn and not (a.shop_id and a.map_name):
    raise SystemExit("Cần --sn, hoặc --shop-id kèm --map-name")

loader = None
if a.demo:
    import demo_data
    loader = demo_data.image_for
files = map_tools.export_map(make_client(a.demo), a.out, sn=a.sn, shop_id=a.shop_id,
                             map_name=a.map_name, image_loader=loader)
print("Đã xuất:")
for f in files:
    print("  " + f)
