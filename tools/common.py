# -*- coding: utf-8 -*-
"""Phần dùng chung cho các công cụ dòng lệnh: đọc config.json / biến môi trường, tạo client."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):              # console Windows: in tiếng Việt không lỗi charmap
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import server                                     # noqa: E402
from pudu_client import PuduClient                # noqa: E402


def make_client(demo=False):
    if demo:
        import demo_data
        return PuduClient(demo_data.DEMO_KEY, demo_data.DEMO_SECRET, transport=demo_data.transport)
    cfg = server.load_config()
    if not (cfg["app_key"] and cfg["app_secret"]):
        sys.exit("Chưa có khoá. Tạo config.json từ config.example.json, hoặc đặt biến môi trường "
                 "PUDU_APP_KEY / PUDU_APP_SECRET. Muốn thử không cần khoá: thêm --demo")
    return PuduClient(cfg["app_key"], cfg["app_secret"], cfg.get("region", "sea"),
                      language=cfg.get("language") or None)
