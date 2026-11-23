"""
Editor area deteksi (polygon) untuk main.py.

Cara pakai:
  - Dari editor: atur DEFAULT_LOCATION di bawah, lalu klik tombol Play.
  - Dari terminal (folder vehicleTraffic):
    .venv/bin/python customzone.py Bandung
    .venv/bin/python customzone.py Bali
    .venv/bin/python customzone.py Bali "https://url-stream-lain.m3u8"   # opsional: URL lain

Zona disimpan ke <Lokasi>/zones.json, lalu otomatis dipakai main.py saat dijalankan ulang.
"""
import os
import sys
import json
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://atcs-dishub.bandung.go.id:1990/Cikutra/index.m3u8"
FRAME_SIZE = (1280, 720)  # harus sama dengan resize di main.py

# Zona yang dipakai tiap lokasi
#   top   = area HITUNG kendaraan
#   left  = jalur kiri (cek lawan arah, kendaraan harus bergerak ke atas)
#   right = jalur kanan (cek lawan arah, kendaraan harus bergerak ke bawah)
ZONE_NAMES = {
    "Bandung": ["top", "left", "right"],
    "Bali": ["top"],
}
COLORS = {"top": (0, 255, 255), "left": (255, 0, 0), "right": (0, 255, 0)}

# >>> Kalau dijalankan dari tombol Play di editor, ganti lokasi di sini <<<
DEFAULT_LOCATION = "Bandung"   # "Bandung" atau "Bali"

LOCATION = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOCATION
if LOCATION not in ZONE_NAMES:
    print(f"Lokasi '{LOCATION}' tidak dikenal. Pilih salah satu: {', '.join(ZONE_NAMES)}")
    raise SystemExit(1)
STREAM_URL = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_URL
print(f"[INFO] Edit zona untuk: {LOCATION}")
SAVE_PATH = os.path.join(BASE_DIR, LOCATION, "zones.json")
names = ZONE_NAMES[LOCATION]

zones = {n: [] for n in names}
active = names[0]
paused = False


def load_zones():
    if not os.path.exists(SAVE_PATH):
        print(f"[INFO] {SAVE_PATH} belum ada, mulai dari kosong")
        return
    with open(SAVE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for n in names:
        zones[n] = [(int(x), int(y)) for x, y in data.get(n, [])]
    print(f"[OK] Loaded {SAVE_PATH}")


def save_zones():
    for n in names:
        if 0 < len(zones[n]) < 3:
            print(f"[GAGAL] Zona {n.upper()} baru {len(zones[n])} titik, minimal 3. Tidak disimpan.")
            return
    parts = []
    for n in names:
        pts = ",\n".join(f"    [{x}, {y}]" for x, y in zones[n])
        parts.append(f'  "{n}": [\n{pts}\n  ]' if pts else f'  "{n}": []')
    with open(SAVE_PATH, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(parts) + "\n}\n")
    print(f"[OK] Saved {SAVE_PATH}  -> jalankan ulang main.py {LOCATION} untuk memakai zona baru")


def draw_zone(frame, name, pts, color, is_active):
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


def mouse_cb(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        zones[active].append((x, y))
    elif event == cv2.EVENT_RBUTTONDOWN and zones[active]:
        zones[active].pop()


load_zones()

cap = cv2.VideoCapture(STREAM_URL, cv2.CAP_FFMPEG)
if not cap.isOpened():
    print("Gagal buka stream:", STREAM_URL)
    raise SystemExit(1)

win = f"Zone Editor - {LOCATION}"
cv2.namedWindow(win, cv2.WINDOW_NORMAL)
cv2.resizeWindow(win, *FRAME_SIZE)
cv2.setMouseCallback(win, mouse_cb)

help_text = [
    "Klik kiri  : tambah titik",
    "Klik kanan : hapus titik terakhir",
    " / ".join(f"{i + 1}={n.upper()}" for i, n in enumerate(names)) + " : pilih zona",
    "R : reset zona aktif   SPASI : pause video",
    "S : simpan   L : load ulang   ESC : keluar",
]

last_frame = None
while True:
    if not paused or last_frame is None:
        ret, raw = cap.read()
        if not ret:
            print("Frame tidak terbaca.")
            break
        last_frame = cv2.resize(raw, FRAME_SIZE, interpolation=cv2.INTER_AREA)

    frame = last_frame.copy()
    for n in names:
        draw_zone(frame, n, zones[n], COLORS[n], n == active)

    cv2.putText(frame, f"{LOCATION} | ZONA AKTIF: {active.upper()} ({len(zones[active])} titik)"
                + ("  [PAUSE]" if paused else ""),
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLORS[active], 2)
    for i, t in enumerate(help_text):
        cv2.putText(frame, t, (20, 60 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.imshow(win, frame)
    k = cv2.waitKey(30) & 0xFF
    if k == 27:
        break
    elif ord("1") <= k < ord("1") + len(names):
        active = names[k - ord("1")]
    elif k == ord(" "):
        paused = not paused
    elif k in (ord("r"), ord("R")):
        zones[active] = []
        print(f"[OK] Reset {active}")
    elif k in (ord("s"), ord("S")):
        save_zones()
    elif k in (ord("l"), ord("L")):
        load_zones()

cap.release()
cv2.destroyAllWindows()
