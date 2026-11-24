"""
Editor zona untuk main.py: zona HITUNG (TOP) + lajur cek lawan arah (boleh lebih dari 2).

Cara pakai (dari folder vehicleTraffic):
    uv run python customzone.py Bandung
    uv run python customzone.py Bali
    uv run python customzone.py Bandung "https://url-stream-lain.m3u8"   # opsional: URL lain
Atau atur DEFAULT_LOCATION di bawah lalu klik tombol Play di editor.

Tombol (jendela video harus aktif):
    1            pilih zona hitung TOP
    2..9         pilih lajur ke-1..8
    N            tambah lajur baru (ketik nama langsung di jendela, Enter)
    E            ganti nama lajur aktif
    H            hapus lajur aktif (tekan H sekali lagi untuk konfirmasi)
    D            panah arah lajur aktif: klik EKOR lalu KEPALA, searah lalu lintas yang BENAR
    X            hapus panah lajur aktif
    klik kiri    tambah titik polygon zona aktif
    klik kanan   hapus titik terakhir (saat menggambar panah: batal)
    R            reset titik zona aktif
    I            sembunyikan / tampilkan teks bantuan
    SPASI        pause / lanjut video
    S / L        simpan / load ulang
    ESC          batal mode nama/panah, atau keluar

Disimpan ke <Lokasi>/zones.json (format "lanes" = format aiConfig Core, lihat docs/09):
    {"top": [[x, y], ...],
     "lanes": [{"name": "atas-keluar", "zone": [[x, y], ...], "direction": [[x1, y1], [x2, y2]]}]}
File lama (zona "left"/"right") otomatis diubah jadi lajur "kiri"/"kanan" saat dibuka.
Lajur yang tidak digambar = tidak dicek lawan arahnya.
"""
import json
import math
import os
import sys

import cv2
import numpy as np

from solomove.contraflow import parse_direction

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://atcs-dishub.bandung.go.id:1990/Pahlawan/index.m3u8"  # Cikutra sudah mati
FRAME_SIZE = (1280, 720)  # harus sama dengan resize di main.py

# lanes=False: main.py lokasi itu belum memakai deteksi lawan arah
LOCATIONS = {"Bandung": {"lanes": True}, "Bali": {"lanes": False}}
DEFAULT_LOCATION = "Bandung"   # >>> kalau dijalankan dari tombol Play, ganti lokasi di sini <<<

TOP_COLOR = (0, 255, 255)
# BGR, urutan sama dengan main.py (lajur-1 biru, lajur-2 hijau, ...)
LANE_COLORS = [(255, 0, 0), (0, 255, 0), (0, 165, 255), (255, 0, 255),
               (255, 255, 0), (180, 105, 255), (0, 128, 255), (128, 0, 128)]
MAX_LANES = 8
LEGACY = {"left": ("kiri", (0, -1)), "right": ("kanan", (0, 1))}
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def lane_color(i):
    return LANE_COLORS[i % len(LANE_COLORS)]


def _pt(p):
    return (int(round(float(p[0]))), int(round(float(p[1]))))


def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def default_arrow(pts, direction, length=50):
    cx, cy = centroid(pts)
    dx, dy = direction
    return [_pt((cx - dx * length, cy - dy * length)), _pt((cx + dx * length, cy + dy * length))]


def to_arrow(value, pts):
    """direction dari file → panah 2 titik untuk editor. Terima panah, vektor, atau "up"/"down"."""
    if value is None:
        return None
    if (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in value)):
        return [_pt(value[0]), _pt(value[1])]
    try:
        vec = parse_direction(value)
    except ValueError as e:
        print(f"[WARN] {e} → panah dihapus, gambar ulang dengan D")
        return None
    return default_arrow(pts, vec) if len(pts) >= 3 else None


def parse_zones(data, lanes_enabled=True):
    """Isi zones.json → (top, lanes, extra). lanes = [{"name", "points", "direction"}]."""
    top = [_pt(p) for p in data.get("top", []) or []]
    lanes = []
    managed = {"top"}
    if lanes_enabled:
        managed |= {"lanes"} | set(LEGACY) | {f"{k}_direction" for k in LEGACY}
        if "lanes" in data:
            for i, item in enumerate(data.get("lanes") or []):
                pts = [_pt(p) for p in item.get("zone", []) or []]
                lanes.append({"name": str(item.get("name") or f"lajur-{i + 1}"), "points": pts,
                              "direction": to_arrow(item.get("direction"), pts)})
        else:  # format lama customzone.py
            for key, (name, vec) in LEGACY.items():
                pts = [_pt(p) for p in data.get(key, []) or []]
                if len(pts) < 3:
                    continue
                arrow = to_arrow(data.get(f"{key}_direction"), pts) or default_arrow(pts, vec)
                lanes.append({"name": name, "points": pts, "direction": arrow})
    extra = {k: v for k, v in data.items() if k not in managed}
    return top, lanes, extra


def validate(top, lanes):
    """Kembalikan daftar pesan error (kosong = boleh disimpan). Lajur tanpa titik diabaikan."""
    errors = []
    if 0 < len(top) < 3:
        errors.append(f"Zona TOP baru {len(top)} titik, minimal 3")
    names = set()
    for lane in lanes:
        n, pts = lane["name"], lane["points"]
        if not pts:
            continue
        if not n:
            errors.append("Ada lajur tanpa nama (tekan E)")
        elif n in names:
            errors.append(f"Nama lajur '{n}' dipakai dua kali")
        names.add(n)
        if len(pts) < 3:
            errors.append(f"Lajur '{n}' baru {len(pts)} titik, minimal 3")
        if not lane["direction"]:
            errors.append(f"Lajur '{n}' belum punya panah arah (tekan D)")
    return errors


def serialize(top, lanes, extra, lanes_enabled=True):
    """JSON rapi: satu titik TOP per baris, satu lajur per blok."""
    parts = ['  "top": [\n' + ",\n".join(f"    [{x}, {y}]" for x, y in top) + "\n  ]" if top else '  "top": []']
    if lanes_enabled:
        blocks = []
        for lane in lanes:
            if not lane["points"]:
                continue
            zone = ", ".join(f"[{x}, {y}]" for x, y in lane["points"])
            (x1, y1), (x2, y2) = lane["direction"]
            blocks.append("    {\n"
                          f'      "name": {json.dumps(lane["name"])},\n'
                          f'      "zone": [{zone}],\n'
                          f'      "direction": [[{x1}, {y1}], [{x2}, {y2}]]\n'
                          "    }")
        parts.append('  "lanes": [\n' + ",\n".join(blocks) + "\n  ]" if blocks else '  "lanes": []')
    for k, v in extra.items():
        parts.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


# ============================================================
# Editor (OpenCV)
# ============================================================
class Editor:
    def __init__(self, location, save_path, lanes_enabled):
        self.location = location
        self.save_path = save_path
        self.lanes_enabled = lanes_enabled
        self.top, self.lanes, self.extra = [], [], {}
        self.active = 0            # 0 = TOP, i >= 1 = lanes[i - 1]
        self.paused = False
        self.show_help = True
        self.arrow_pts = None      # list saat mode panah (D)
        self.naming = None         # {"buffer": str, "index": int} saat mengetik nama
        self.confirm_delete = False
        self.mouse = (0, 0)
        self.flash = ""            # pesan singkat di layar

    # ---------- data ----------
    def say(self, msg):
        print(msg)
        self.flash = msg

    def load(self):
        if not os.path.exists(self.save_path):
            self.say(f"[INFO] {self.save_path} belum ada, mulai dari kosong")
            return
        with open(self.save_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.top, self.lanes, self.extra = parse_zones(data, self.lanes_enabled)
        self.active = 0
        if self.lanes_enabled and "lanes" not in data and self.lanes:
            print("[INFO] Format lama (left/right) diubah jadi lajur 'kiri'/'kanan'. Tekan S untuk menyimpan format baru.")
        self.say(f"[OK] Loaded {os.path.basename(self.save_path)}: TOP {len(self.top)} titik, {len(self.lanes)} lajur")

    def save(self):
        errors = validate(self.top, self.lanes)
        if errors:
            for e in errors:
                print("[GAGAL]", e)
            self.flash = "[GAGAL] " + errors[0] + " - tidak disimpan"
            return False
        with open(self.save_path, "w", encoding="utf-8") as f:
            f.write(serialize(self.top, self.lanes, self.extra, self.lanes_enabled))
        self.say(f"[OK] Tersimpan. Jalankan ulang main.py {self.location} untuk memakai zona baru")
        return True

    def points(self):
        return self.top if self.active == 0 else self.lanes[self.active - 1]["points"]

    def active_lane(self):
        return self.lanes[self.active - 1] if self.active > 0 else None

    def active_name(self):
        return "TOP" if self.active == 0 else self.active_lane()["name"]

    # ---------- mouse ----------
    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_MOUSEMOVE:
            self.mouse = (x, y)
            return
        if self.naming:
            return
        if self.arrow_pts is not None:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.arrow_pts.append((x, y))
                if len(self.arrow_pts) == 2:
                    a, b = self.arrow_pts
                    if math.dist(a, b) < 10:
                        self.say("[GAGAL] Panah terlalu pendek, ulangi D")
                    else:
                        self.active_lane()["direction"] = [a, b]
                        self.say(f"[OK] Panah {self.active_name()}: {a} -> {b} (S untuk simpan)")
                    self.arrow_pts = None
            elif event == cv2.EVENT_RBUTTONDOWN:
                self.arrow_pts = None
                self.say("[INFO] Batal gambar panah")
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points().append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and self.points():
            self.points().pop()

    # ---------- keyboard ----------
    def on_key(self, k):
        """Kembalikan False jika editor harus ditutup."""
        if k == 255:
            return True
        if self.naming:
            self._on_name_key(k)
            return True
        if self.confirm_delete:
            self.confirm_delete = False
            if k in (ord("h"), ord("H")):
                gone = self.lanes.pop(self.active - 1)
                self.active = min(self.active, len(self.lanes))
                self.say(f"[OK] Lajur '{gone['name']}' dihapus (belum tersimpan, L = batalkan)")
            else:
                self.say("[INFO] Batal hapus lajur")
            return True
        if k == 27:
            if self.arrow_pts is not None:
                self.arrow_pts = None
                self.say("[INFO] Batal gambar panah")
                return True
            return False
        ch = chr(k).lower() if k < 128 else ""
        if ch == "1":
            self._select(0)
        elif ch and ch in "23456789":
            idx = int(ch) - 1
            if self.lanes_enabled and idx <= len(self.lanes):
                self._select(idx)
            elif self.lanes_enabled:
                self.say(f"[INFO] Lajur ke-{idx} belum ada, tekan N untuk tambah")
        elif ch == " ":
            self.paused = not self.paused
        elif ch == "i":
            self.show_help = not self.show_help
        elif ch == "r":
            self.points().clear()
            self.say(f"[OK] Titik {self.active_name()} direset")
        elif ch == "s":
            self.save()
        elif ch == "l":
            self.load()
        elif ch in ("n", "e", "h", "d", "x"):
            if not self.lanes_enabled:
                self.say(f"[INFO] Lokasi {self.location} belum memakai deteksi lawan arah")
            elif ch == "n":
                if len(self.lanes) >= MAX_LANES:
                    self.say(f"[GAGAL] Maksimal {MAX_LANES} lajur")
                else:
                    self.lanes.append({"name": f"lajur-{len(self.lanes) + 1}", "points": [], "direction": None})
                    self._select(len(self.lanes))
                    self.naming = {"buffer": "", "index": self.active}
            elif self.active == 0:
                self.say("[INFO] Pilih lajur dulu (tombol 2..9)")
            elif ch == "e":
                self.naming = {"buffer": "", "index": self.active}
            elif ch == "h":
                self.confirm_delete = True
                self.flash = f"Hapus lajur '{self.active_name()}'? Tekan H lagi untuk ya, tombol lain = batal"
            elif ch == "d":
                self.arrow_pts = []
                self.say(f"[INFO] Panah {self.active_name()}: klik EKOR lalu KEPALA (searah lalu lintas yang benar)")
            elif ch == "x":
                self.active_lane()["direction"] = None
                self.say(f"[OK] Panah {self.active_name()} dihapus")
        return True

    def _select(self, idx):
        self.active = idx
        self.arrow_pts = None

    def _on_name_key(self, k):
        buf = self.naming["buffer"]
        lane = self.lanes[self.naming["index"] - 1]
        if k in (13, 10):
            if buf:
                lane["name"] = buf
            self.say(f"[OK] Nama lajur: {lane['name']}")
            self.naming = None
        elif k == 27:
            self.naming = None
            self.say(f"[INFO] Nama tetap: {lane['name']}")
        elif k in (8, 127):
            self.naming["buffer"] = buf[:-1]
        elif k < 128 and chr(k).lower() in NAME_CHARS and len(buf) < 24:
            self.naming["buffer"] = buf + chr(k).lower()
        elif k == 32 and len(buf) < 24:
            self.naming["buffer"] = buf + "-"

    # ---------- gambar ----------
    def help_lines(self):
        lines = ["Klik kiri: tambah titik   Klik kanan: hapus titik terakhir"]
        if self.lanes_enabled:
            lines += ["1=TOP   2..9=lajur   N=lajur baru   E=ganti nama   H=hapus lajur",
                      "D=panah arah (klik ekor lalu kepala)   X=hapus panah"]
        lines.append("R=reset titik  SPASI=pause  S=simpan  L=load  I=sembunyikan  ESC=keluar")
        if self.lanes_enabled:
            names = "  ".join(f"[{i + 2}] {l['name']}" for i, l in enumerate(self.lanes)) or "(belum ada, tekan N)"
            lines.append("Lajur: " + names)
        return lines

    def render(self, frame):
        draw_zone(frame, self.top, TOP_COLOR, self.active == 0, "TOP")
        for i, lane in enumerate(self.lanes):
            is_active = self.active == i + 1
            color = lane_color(i)
            draw_zone(frame, lane["points"], color, is_active, lane["name"])
            if lane["direction"]:
                a, b = lane["direction"]
                cv2.arrowedLine(frame, a, b, (255, 255, 255), 7 if is_active else 5, tipLength=0.25)
                cv2.arrowedLine(frame, a, b, color, 4 if is_active else 2, tipLength=0.25)
            elif len(lane["points"]) >= 3:
                cx, cy = centroid(lane["points"])
                put_text_bg(frame, "BELUM ADA PANAH (D)", (int(cx), int(cy)), 0.5, (0, 0, 255), center=True)
        if self.arrow_pts is not None and self.arrow_pts:
            cv2.arrowedLine(frame, self.arrow_pts[0], self.mouse, lane_color(self.active - 1), 2, tipLength=0.2)

        lines = self.help_lines() if self.show_help else []
        color = TOP_COLOR if self.active == 0 else lane_color(self.active - 1)
        status = f"{self.location} | AKTIF: {self.active_name()} ({len(self.points())} titik)"
        if self.arrow_pts is not None:
            status += f"  [PANAH: klik {'EKOR' if not self.arrow_pts else 'KEPALA'}]"
        if self.paused:
            status += "  [PAUSE]"
        # panel selebar teks saja, supaya tidak menutupi jalan
        width = max([cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0][0]]
                    + [cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0] for t in lines])
        panel = frame.copy()
        cv2.rectangle(panel, (10, 8), (30 + width, 44 + len(lines) * 22), (0, 0, 0), -1)
        cv2.addWeighted(panel, 0.6, frame, 0.4, 0, frame)
        cv2.putText(frame, status, (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
        for i, t in enumerate(lines):
            cv2.putText(frame, t, (20, 58 + i * 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        if self.flash:
            put_text_bg(frame, self.flash[:110], (20, frame.shape[0] - 20), 0.6, (255, 255, 255))
        if self.naming:
            h, w = frame.shape[:2]
            box = frame.copy()
            cv2.rectangle(box, (w // 2 - 330, h // 2 - 60), (w // 2 + 330, h // 2 + 60), (0, 0, 0), -1)
            cv2.addWeighted(box, 0.85, frame, 0.15, 0, frame)
            lane = self.lanes[self.naming["index"] - 1]
            cv2.putText(frame, f"Nama lajur: {self.naming['buffer'] or lane['name']}_", (w // 2 - 310, h // 2 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, lane_color(self.naming["index"] - 1), 2, cv2.LINE_AA)
            cv2.putText(frame, "ketik a-z 0-9 -   Enter = simpan   ESC = batal", (w // 2 - 310, h // 2 + 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        return frame


def put_text_bg(frame, text, org, scale, color, center=False):
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = org
    if center:
        x -= tw // 2
    cv2.rectangle(frame, (x - 6, y - th - 6), (x + tw + 6, y + base + 4), (0, 0, 0), -1)
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def draw_zone(frame, pts, color, is_active, name):
    if not pts:
        return
    pts_np = np.array(pts, dtype=np.int32)
    if len(pts) >= 3:
        overlay = frame.copy()
        cv2.fillPoly(overlay, [pts_np], color)
        cv2.addWeighted(overlay, 0.25 if is_active else 0.10, frame, 0.75 if is_active else 0.90, 0, frame)
    if len(pts) >= 2:
        cv2.polylines(frame, [pts_np], isClosed=len(pts) >= 3, color=color, thickness=3 if is_active else 1)
    for i, (x, y) in enumerate(pts):
        cv2.circle(frame, (x, y), 5 if is_active else 3, color, -1)
        if is_active:
            cv2.putText(frame, str(i + 1), (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    if len(pts) >= 3:  # nama di bawah zona, di tengah
        cx = int(sum(p[0] for p in pts) / len(pts))
        put_text_bg(frame, name, (cx, max(p[1] for p in pts) + 20), 0.5, color, center=True)


def main():
    location = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOCATION
    if location not in LOCATIONS:
        print(f"Lokasi '{location}' tidak dikenal. Pilih salah satu: {', '.join(LOCATIONS)}")
        raise SystemExit(1)
    url = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL
    print(f"[INFO] Edit zona untuk: {location}")
    ed = Editor(location, os.path.join(BASE_DIR, location, "zones.json"), LOCATIONS[location]["lanes"])
    ed.load()

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("Gagal buka stream:", url)
        raise SystemExit(1)

    win = f"Zone Editor - {location}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, *FRAME_SIZE)
    cv2.setMouseCallback(win, ed.on_mouse)

    last_frame = None
    while True:
        if not ed.paused or last_frame is None:
            ret, raw = cap.read()
            if not ret:
                print("Frame tidak terbaca.")
                break
            last_frame = cv2.resize(raw, FRAME_SIZE, interpolation=cv2.INTER_AREA)
        cv2.imshow(win, ed.render(last_frame.copy()))
        if not ed.on_key(cv2.waitKey(30) & 0xFF):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
