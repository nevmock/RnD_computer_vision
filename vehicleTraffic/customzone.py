"""
Editor area deteksi (polygon) untuk main.py.

Cara pakai:
  - Dari editor: atur DEFAULT_LOCATION di bawah, lalu klik tombol Play.
  - Dari terminal (folder vehicleTraffic):
    .venv/bin/python customzone.py Bandung
    .venv/bin/python customzone.py Bali
    .venv/bin/python customzone.py Bali "https://url-stream-lain.m3u8"   # opsional: URL lain

Zona disimpan ke <Lokasi>/zones.json, lalu otomatis dipakai main.py saat dijalankan ulang.

Arah lajur (zona LEFT/RIGHT): pilih zonanya, tekan D, lalu klik 2 titik di jalan:
ekor → kepala panah, searah lalu lintas yang BENAR. Tanpa panah, LEFT = naik, RIGHT = turun.
"""
import os
import sys
import json
import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://atcs-dishub.bandung.go.id:1990/Pahlawan/index.m3u8"  # Cikutra sudah mati
FRAME_SIZE = (1280, 720)  # harus sama dengan resize di main.py

# Zona yang dipakai tiap lokasi
#   top   = area HITUNG kendaraan
#   left  = jalur kiri (cek lawan arah; arah default: naik, bisa diganti dengan panah D)
#   right = jalur kanan (cek lawan arah; arah default: turun, bisa diganti dengan panah D)
ZONE_NAMES = {
    "Bandung": ["top", "left", "right"],
    "Bali": ["top"],
}
COLORS = {"top": (0, 255, 255), "left": (255, 0, 0), "right": (0, 255, 0)}
DEFAULT_DIRECTION = {"left": (0, -1), "right": (0, 1)}   # sama dengan solomove/contraflow.py

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

DIRECTION_ZONES = [n for n in names if n in DEFAULT_DIRECTION]

zones = {n: [] for n in names}
directions = {}        # "left" -> [(x1, y1), (x2, y2)]  (ekor -> kepala panah)
extra = {}             # key lain di zones.json (mis. "contraflow") tetap disimpan apa adanya
active = names[0]
paused = False
dir_mode = False       # True setelah tekan D: 2 klik berikutnya = panah arah
dir_pts = []
mouse_pos = (0, 0)


def load_zones():
    if not os.path.exists(SAVE_PATH):
        print(f"[INFO] {SAVE_PATH} belum ada, mulai dari kosong")
        return
    with open(SAVE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for n in names:
        zones[n] = [(int(x), int(y)) for x, y in data.get(n, [])]
    directions.clear()
    for n in DIRECTION_ZONES:
        arrow = data.get(f"{n}_direction")
        if arrow and len(arrow) == 2 and all(len(p) == 2 for p in arrow):
            directions[n] = [(int(arrow[0][0]), int(arrow[0][1])), (int(arrow[1][0]), int(arrow[1][1]))]
    managed = set(names) | {f"{n}_direction" for n in DIRECTION_ZONES}
    extra.clear()
    extra.update({k: v for k, v in data.items() if k not in managed})
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
    for n in DIRECTION_ZONES:
        if n in directions:
            (x1, y1), (x2, y2) = directions[n]
            parts.append(f'  "{n}_direction": [[{x1}, {y1}], [{x2}, {y2}]]')
    for k, v in extra.items():
        parts.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}")
    with open(SAVE_PATH, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(parts) + "\n}\n")
    print(f"[OK] Saved {SAVE_PATH}  -> jalankan ulang main.py {LOCATION} untuk memakai zona baru")


def zone_center(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return sum(xs) // len(xs), sum(ys) // len(ys)


def draw_direction(frame, name, color):
    """Panah arah seharusnya. Tebal = panah dari kamu (D), tipis + 'default' = arah bawaan."""
    if name in directions:
        tail, head = directions[name]
        cv2.arrowedLine(frame, tail, head, color, 4, tipLength=0.2)
        return
    if len(zones[name]) < 3:
        return
    cx, cy = zone_center(zones[name])
    dx, dy = DEFAULT_DIRECTION[name]
    cv2.arrowedLine(frame, (cx - dx * 50, cy - dy * 50), (cx + dx * 50, cy + dy * 50), color, 2, tipLength=0.3)
    cv2.putText(frame, "default", (cx + 10, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


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
    global dir_mode, mouse_pos
    if event == cv2.EVENT_MOUSEMOVE:
        mouse_pos = (x, y)
    elif dir_mode:
        if event == cv2.EVENT_LBUTTONDOWN:
            dir_pts.append((x, y))
            if len(dir_pts) == 2:
                if dir_pts[0] == dir_pts[1]:
                    print("[GAGAL] Ekor dan kepala panah tidak boleh di titik yang sama")
                else:
                    directions[active] = list(dir_pts)
                    print(f"[OK] Arah {active.upper()}: {dir_pts[0]} -> {dir_pts[1]} (tekan S untuk simpan)")
                dir_mode = False
                dir_pts.clear()
        elif event == cv2.EVENT_RBUTTONDOWN:
            dir_mode = False
            dir_pts.clear()
            print("[INFO] Batal gambar arah")
    elif event == cv2.EVENT_LBUTTONDOWN:
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
if DIRECTION_ZONES:
    help_text.append("D : panah arah lajur aktif (klik ekor lalu kepala)   X : hapus panah")

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
    for n in DIRECTION_ZONES:
        draw_direction(frame, n, COLORS[n])
    if dir_mode and dir_pts:
        cv2.arrowedLine(frame, dir_pts[0], mouse_pos, COLORS[active], 2, tipLength=0.2)

    # latar gelap di belakang teks bantuan supaya terbaca
    panel = frame.copy()
    cv2.rectangle(panel, (10, 8), (760, 45 + len(help_text) * 25), (0, 0, 0), -1)
    cv2.addWeighted(panel, 0.55, frame, 0.45, 0, frame)

    status = f"{LOCATION} | ZONA AKTIF: {active.upper()} ({len(zones[active])} titik)"
    if dir_mode:
        status += f"  [ARAH: klik {'EKOR' if not dir_pts else 'KEPALA'} panah, klik kanan = batal]"
    if paused:
        status += "  [PAUSE]"
    cv2.putText(frame, status, (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLORS[active], 2)
    for i, t in enumerate(help_text):
        cv2.putText(frame, t, (20, 60 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.imshow(win, frame)
    k = cv2.waitKey(30) & 0xFF
    if k == 27:
        break
    elif ord("1") <= k < ord("1") + len(names):
        active = names[k - ord("1")]
        dir_mode = False
        dir_pts.clear()
    elif k == ord(" "):
        paused = not paused
    elif k in (ord("r"), ord("R")):
        zones[active] = []
        directions.pop(active, None)
        print(f"[OK] Reset {active}")
    elif k in (ord("d"), ord("D")):
        if active in DIRECTION_ZONES:
            dir_mode = True
            dir_pts.clear()
            print(f"[INFO] Gambar arah {active.upper()}: klik EKOR lalu KEPALA panah (searah lalu lintas yang benar)")
        else:
            print("[INFO] Panah arah hanya untuk zona LEFT / RIGHT")
    elif k in (ord("x"), ord("X")):
        if directions.pop(active, None):
            print(f"[OK] Panah {active.upper()} dihapus, kembali ke arah default")
    elif k in (ord("s"), ord("S")):
        save_zones()
    elif k in (ord("l"), ord("L")):
        load_zones()

cap.release()
cv2.destroyAllWindows()
