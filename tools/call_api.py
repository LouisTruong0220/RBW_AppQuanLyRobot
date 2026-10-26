# -*- coding: utf-8 -*-
"""
Gọi thử MỘT API bất kỳ của PUDU — tự ký, in JSON trả về.

    python tools/call_api.py GET /open-platform-service/v2/status/get_by_sn sn=8FF0xxxxxxxx
    python tools/call_api.py GET /map-service/v1/open/current sn=8FF0xxxxxxxx need_element=true
    python tools/call_api.py POST /open-platform-service/v1/control_doors \
        --body '{"sn":"8FF0xxxxxxxx","payload":{"control_states":[{"door_number":"1","operation":true}]}}' --yes

POST làm robot cử động thật → bắt buộc thêm --yes.
"""
import argparse
import json
from common import make_client
from pudu_client import PuduError

ap = argparse.ArgumentParser()
ap.add_argument("method", choices=["GET", "POST", "get", "post"])
ap.add_argument("path", help="vd /open-platform-service/v2/status/get_by_sn (không cần /pudu-entry)")
ap.add_argument("params", nargs="*", help="tham số query dạng ten=gia_tri")
ap.add_argument("--body", default="", help="JSON body cho POST")
ap.add_argument("--yes", action="store_true", help="xác nhận gửi lệnh POST")
ap.add_argument("--demo", action="store_true")
a = ap.parse_args()

method = a.method.upper()
if method == "POST" and not a.yes:
    raise SystemExit("POST là lệnh điều khiển robot thật. Thêm --yes nếu chắc chắn.")
params = dict(p.split("=", 1) for p in a.params)
c = make_client(a.demo)
try:
    js = c.request(a.path, method, params=params, body=json.loads(a.body) if a.body else None)
    print(json.dumps(js, ensure_ascii=False, indent=2))
except PuduError as e:
    print(json.dumps(e.to_dict(), ensure_ascii=False, indent=2))
    raise SystemExit(1)
