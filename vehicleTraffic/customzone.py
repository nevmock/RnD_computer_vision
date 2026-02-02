import cv2
import json
import numpy as np

STREAM_URL = "https://atcs-dishub.bandung.go.id:1990/Cikutra/index.m3u8"
SAVE_PATH = "zones.json"

zones = {
    "top": [],     # list of (x,y)
    "bottom": []
}
active = "top"     # zone yang sedang diedit

def to_int_tuple_list(points):
    return [(int(x), int(y)) for (x, y) in points]

def draw_zone(frame, name, pts, color, thickness=2):
    if len(pts) == 0:
        return
    pts_np = np.array(pts, dtype=np.int32).reshape((-1, 1, 2))
    # garis polygon (kalau >=2 titik)
    if len(pts) >= 2:
        cv2.polylines(frame, [pts_np], isClosed=(len(pts) >= 3), color=color, thickness=thickness)
    # titik-titik
    for i, (x, y) in enumerate(pts):
        cv2.circle(frame, (x, y), 4, color, -1)
        cv2.putText(frame, f"{name[0].upper()}{i+1}", (x+6, y-6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

def mouse_cb(event, x, y, flags, param):
    global zones, active
    if event == cv2.EVENT_LBUTTONDOWN:
        zones[active].append((x, y))
    elif event == cv2.EVENT_RBUTTONDOWN:
        # hapus titik terakhir
        if zones[active]:
            zones[active].pop()

def save_zones(path):
    data = {
        "top": zones["top"],
        "bottom": zones["bottom"]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[OK] Saved to {path}")

def load_zones(path):
    global zones
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    zones["top"] = to_int_tuple_list(data.get("top", []))
    zones["bottom"] = to_int_tuple_list(data.get("bottom", []))
    print(f"[OK] Loaded from {path}")

cap = cv2.VideoCapture(STREAM_URL, cv2.CAP_FFMPEG)
if not cap.isOpened():
    print("Gagal buka stream. Kalau m3u8 tidak stabil, coba pakai file mp4 lokal dulu.")
    raise SystemExit

win = "Polygon Designer"
cv2.namedWindow(win, cv2.WINDOW_NORMAL)
cv2.setMouseCallback(win, mouse_cb)

help_text = [
    "Left click  : add point",
    "Right click : undo last point",
    "Key 1       : edit TOP zone",
    "Key 2       : edit BOTTOM zone",
    "Key R       : reset active zone",
    "Key S       : save zones.json",
    "Key L       : load zones.json",
    "ESC         : exit",
]

while True:
    ret, frame = cap.read()
    h, w = frame.shape[:2]
    print("Frame size:", w, "x", h)
    frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
    if not ret:
        print("Frame tidak terbaca.")
        break

    # draw zones (warna beda)
    draw_zone(frame, "TOP", zones["top"], (0, 255, 255), 2)
    draw_zone(frame, "BOTTOM", zones["bottom"], (255, 255, 0), 2)

    # UI text
    cv2.putText(frame, f"ACTIVE ZONE: {active.upper()}   (Press 1/2 to switch)",
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)

    y0 = 60
    for i, t in enumerate(help_text):
        cv2.putText(frame, t, (20, y0 + i*25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 2)

    cv2.imshow(win, frame)

    k = cv2.waitKey(1) & 0xFF
    if k == 27:  # ESC
        break
    elif k == ord('1'):
        active = "top"
    elif k == ord('2'):
        active = "bottom"
    elif k in (ord('r'), ord('R')):
        zones[active] = []
        print(f"[OK] Reset {active}")
    elif k in (ord('s'), ord('S')):
        save_zones(SAVE_PATH)
    elif k in (ord('l'), ord('L')):
        try:
            load_zones(SAVE_PATH)
        except Exception as e:
            print("Load error:", e)

cap.release()
cv2.destroyAllWindows()
