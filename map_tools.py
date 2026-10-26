# -*- coding: utf-8 -*-
"""
map_tools.py — Trích xuất bản đồ robot từ PUDU Cloud API.

Bản đồ của robot gồm hai lớp:

  · ẢNH LASER (PNG xám 8-bit) robot đã quét: 255 = sàn trống, 0 = vật cản/tường,
    128 = vùng chưa quét. Lấy url ảnh từ GetMapDetailV2.
  · PHẦN TỬ VECTOR (mét): điểm đến, đường chạy, tường ảo, trạm sạc, thang máy...

Quy đổi toạ độ (đã vẽ thử điểm thật chồng lên ảnh thật, khớp từng điểm):

    cột  = (x - origin_x) / resolution
    hàng = height - (y - origin_y) / resolution        # trục y bản đồ hướng LÊN, ảnh hướng XUỐNG

Cấu trúc vector_list theo loại phần tử (đo trên dữ liệu thật):
    source (điểm)            [x, y, hướng_radian]
    track / virtual_wall     [x1, y1, x2, y2, ...]   dãy cặp toạ độ
    charging_pile, elevator  [x, y, ...]
    area                     [x1, y1, ..., xn, yn]   đa giác
"""
import base64
import io
import json
import os
import urllib.request
from urllib.parse import urlparse
from xml.sax.saxutils import escape

# Ảnh bản đồ nằm trên kho lưu trữ của hãng. Chỉ tải từ các miền này.
ALLOWED_IMAGE_HOSTS = (".pudutech.com", ".amazonaws.com", ".aliyuncs.com")

POINT_COLORS = {
    "table": "#E8654A", "dining_outlet": "#F2B134", "transit": "#8E7CC3",
    "dishwashing": "#4FA3D9", "parking": "#9AA0A6", "usher": "#5DBB8A",
    "return_point": "#C77DBA", "clean_endpoint": "#7FB3D5",
}
POINT_LABELS = {
    "table": "Điểm giao/nhận", "dining_outlet": "Điểm lấy hàng", "transit": "Điểm trung chuyển",
    "dishwashing": "Điểm thu hồi", "parking": "Điểm đỗ", "usher": "Điểm đón",
    "return_point": "Điểm quay về", "clean_endpoint": "Điểm kết thúc",
}


def map_origin(detail):
    """Gốc toạ độ ảnh [x, y]. Tài liệu ghi "origin", ví dụ của hãng lại trả "origin_list"."""
    o = detail.get("origin") or detail.get("origin_list") or [0, 0]
    return [float(o[0]), float(o[1])]


class MapGeometry(object):
    """Quy đổi mét ↔ điểm ảnh cho một ảnh bản đồ."""

    def __init__(self, width, height, resolution, origin):
        self.width = int(width)
        self.height = int(height)
        self.resolution = float(resolution)
        self.origin = [float(origin[0]), float(origin[1])]

    @classmethod
    def from_detail(cls, detail):
        return cls(detail.get("width") or 0, detail.get("height") or 0,
                   detail.get("resolution") or 0.05, map_origin(detail))

    def world_to_pixel(self, x, y):
        col = (float(x) - self.origin[0]) / self.resolution
        row = self.height - (float(y) - self.origin[1]) / self.resolution
        return col, row

    def pixel_to_world(self, col, row):
        x = self.origin[0] + float(col) * self.resolution
        y = self.origin[1] + (self.height - float(row)) * self.resolution
        return x, y

    def to_dict(self):
        return {"width": self.width, "height": self.height,
                "resolution": self.resolution, "origin": self.origin,
                "width_m": round(self.width * self.resolution, 3),
                "height_m": round(self.height * self.resolution, 3)}


def download_image(url, timeout=30):
    """Tải ảnh bản đồ. Chặn mọi miền không thuộc kho của hãng (tránh bị lợi dụng tải URL lạ)."""
    host = urlparse(url or "").hostname or ""
    if not (url or "").startswith("https://") or not host.endswith(ALLOWED_IMAGE_HOSTS):
        raise ValueError("URL ảnh bản đồ không thuộc kho của PUDU: %s" % (url or "(trống)"))
    with urllib.request.urlopen(url, timeout=timeout) as f:
        return f.read()


def pairs(v):
    return [(float(v[i]), float(v[i + 1])) for i in range(0, len(v) - 1, 2)]


def normalize_elements(detail, geometry):
    """Đổi element_list của hãng thành danh sách gọn, kèm toạ độ điểm ảnh để vẽ."""
    out = []
    for e in detail.get("element_list") or detail.get("elements") or []:
        v = e.get("vector_list") or e.get("vector") or []
        if len(v) < 2:
            continue
        kind = e.get("type", "")
        item = {"id": e.get("id", ""), "name": e.get("name", ""), "type": kind,
                "mode": e.get("mode", "")}
        if kind in ("track", "virtual_wall", "area", "circle"):
            item["world"] = [[round(x, 4), round(y, 4)] for x, y in pairs(v)]
        else:
            item["world"] = [round(float(v[0]), 4), round(float(v[1]), 4)]
            if kind == "source" and len(v) >= 3:
                item["yaw_rad"] = round(float(v[2]), 4)
        if geometry and geometry.width:
            if isinstance(item["world"][0], list):
                item["pixel"] = [[round(c, 1) for c in geometry.world_to_pixel(x, y)]
                                 for x, y in item["world"]]
            else:
                item["pixel"] = [round(c, 1) for c in geometry.world_to_pixel(*item["world"])]
        out.append(item)
    return out


def png_size(png_bytes):
    """Đọc width/height từ header PNG (không cần thư viện ảnh)."""
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(png_bytes[16:20], "big"), int.from_bytes(png_bytes[20:24], "big")


def build_svg(png_bytes, geometry, elements, robot_xy=None, title=""):
    """SVG tự chứa: ảnh laser nhúng base64 + điểm, đường chạy, tường ảo, vị trí robot.

    Mở bằng trình duyệt hoặc chèn thẳng vào web của bạn. Toạ độ SVG = toạ độ điểm ảnh.
    """
    w, h = geometry.width, geometry.height
    s = max(1.0, w / 600.0)                     # cỡ nét theo kích thước ảnh
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
             % (w, h, w, h)]
    if title:
        parts.append("<title>%s</title>" % escape(title))
    if png_bytes:
        parts.append('<image href="data:image/png;base64,%s" x="0" y="0" width="%d" height="%d" '
                     'style="image-rendering:pixelated"/>'
                     % (base64.b64encode(png_bytes).decode(), w, h))
    for e in elements:
        px = e.get("pixel")
        if not px:
            continue
        if e["type"] in ("track", "virtual_wall", "circle"):
            d = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", c, r) for i, (c, r) in enumerate(px))
            color = "#E14040" if e["type"] == "virtual_wall" else "#3D84E0"
            dash = ' stroke-dasharray="%.1f %.1f"' % (4 * s, 3 * s) if e["type"] == "virtual_wall" else ""
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f" opacity="0.8"%s/>'
                         % (d, color, 1.6 * s, dash))
        elif e["type"] == "area":
            d = " ".join("%.1f,%.1f" % (c, r) for c, r in px)
            parts.append('<polygon points="%s" fill="rgba(61,132,224,.10)" stroke="#3D84E0" '
                         'stroke-width="%.1f"/>' % (d, s))
    for e in elements:
        px = e.get("pixel")
        if not px or isinstance(px[0], list):
            continue
        c, r = px
        if e["type"] == "source":
            color = POINT_COLORS.get(e.get("mode"), "#E8654A")
            parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="#111" stroke-width="%.1f"/>'
                         % (c, r, 3.2 * s, color, 0.8 * s))
            label = e.get("id") or e.get("name")
            parts.append('<text x="%.1f" y="%.1f" font-family="Arial" font-size="%.1f" text-anchor="middle" '
                         'fill="#111" stroke="#fff" stroke-width="%.1f" paint-order="stroke">%s</text>'
                         % (c, r - 5 * s, 7 * s, 2 * s, escape(str(label))))
        elif e["type"] in ("charging_pile", "chargeWorkStation"):
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#2FA579" stroke="#111"/>'
                         % (c - 3 * s, r - 3 * s, 6 * s, 6 * s))
        elif e["type"] in ("elevator", "elev_waiter"):
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" stroke="#8E7CC3" '
                         'stroke-width="%.1f"/>' % (c - 4 * s, r - 4 * s, 8 * s, 8 * s, 1.2 * s))
    if robot_xy:
        c, r = geometry.world_to_pixel(*robot_xy)
        parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="rgba(193,63,40,.25)"/>' % (c, r, 9 * s))
        parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#C13F28" stroke="#fff" stroke-width="%.1f"/>'
                     % (c, r, 4.5 * s, 1.2 * s))
    parts.append("</svg>")
    return "\n".join(parts)


def fetch_map_bundle(client, sn=None, shop_id=None, map_name=None, image_loader=None):
    """Gom đủ dữ liệu bản đồ một lần: thông tin ảnh + phần tử + điểm + vị trí robot.

    Truyền sn → lấy bản đồ ĐANG DÙNG của robot đó.
    Truyền shop_id + map_name → lấy một bản đồ bất kỳ của cửa hàng (không có vị trí robot).
    image_loader: hàm url → bytes PNG; mặc định download_image (tải từ kho của hãng).
    """
    position, points = None, []
    if sn:
        cur = client.current_map(sn, with_elements=False)
        map_name = map_name or cur.get("name")
        shop_id = shop_id or cur.get("shop_id")
        if not map_name:
            raise ValueError("Robot chưa báo bản đồ đang dùng (máy tắt hoặc chưa định vị).")
        try:
            points = client.map_points(sn)
        except Exception:
            points = []
        try:
            pos = client.robot_position(sn)
            p = pos.get("position") or {}
            if p and (p.get("x") or p.get("y")):
                position = {"x": float(p["x"]), "y": float(p["y"]),
                            "map_name": pos.get("map_name") or pos.get("name") or map_name}
        except Exception:
            position = None
    if not (shop_id and map_name):
        raise ValueError("Cần sn, hoặc shop_id + map_name.")

    detail = client.map_detail(shop_id, map_name)
    geometry = MapGeometry.from_detail(detail)
    image_bytes = (image_loader or download_image)(detail["url"]) if detail.get("url") else None
    if image_bytes:
        size = png_size(image_bytes)
        if size and (not geometry.width or not geometry.height):
            geometry.width, geometry.height = size
    elements = normalize_elements(detail, geometry)
    return {
        "map_name": map_name, "map_code": detail.get("map_code", ""), "shop_id": shop_id,
        "geometry": geometry, "elements": elements, "points": points,
        "zones": detail.get("zone_list") or [], "robot_position": position,
        "image_bytes": image_bytes, "image_url": detail.get("url", ""),
    }


def bundle_to_json(bundle):
    g = bundle["geometry"]
    pts = []
    for p in bundle["points"]:
        item = {"name": p.get("name"), "type": p.get("type"),
                "x": p.get("x"), "y": p.get("y")}
        if g.width and p.get("x") is not None:
            item["pixel"] = [round(c, 1) for c in g.world_to_pixel(p["x"], p["y"])]
        pts.append(item)
    return {
        "map_name": bundle["map_name"], "map_code": bundle["map_code"],
        "shop_id": bundle["shop_id"], "geometry": g.to_dict(),
        "coordinate_note": "x,y tính bằng mét. pixel = [cột, hàng] trên ảnh map.png "
                           "(gốc trên-trái). cột=(x-origin_x)/resolution; "
                           "hàng=height-(y-origin_y)/resolution",
        "robot_position": bundle["robot_position"],
        "points": pts, "elements": bundle["elements"], "zones": bundle["zones"],
    }


def export_map(client, out_dir, sn=None, shop_id=None, map_name=None, image_loader=None):
    """Xuất bản đồ ra 3 file: <tên>.png (ảnh laser gốc) · <tên>.json · <tên>.svg."""
    b = fetch_map_bundle(client, sn=sn, shop_id=shop_id, map_name=map_name,
                         image_loader=image_loader)
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in b["map_name"])
    files = []
    if b["image_bytes"]:
        p = os.path.join(out_dir, safe + ".png")
        with open(p, "wb") as f:
            f.write(b["image_bytes"])
        files.append(p)
    p = os.path.join(out_dir, safe + ".json")
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(bundle_to_json(b), f, ensure_ascii=False, indent=1)
    files.append(p)
    pos = b["robot_position"]
    svg = build_svg(b["image_bytes"], b["geometry"], b["elements"],
                    (pos["x"], pos["y"]) if pos else None, title=b["map_name"])
    p = os.path.join(out_dir, safe + ".svg")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(svg)
    files.append(p)
    return files
