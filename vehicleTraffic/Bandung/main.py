import cv2
import numpy as np
from ultralytics import YOLO
from db_writer import start_db_thread, send_to_db
from datetime import datetime
from ws import start_websocket_thread, send_frame_to_clients
import os
import sys

# Integrasi Core API SoloMove (aktif jika env SOLOMOVE_CORE_URL & SOLOMOVE_API_KEY diisi)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from solomove import TrafficReporter, COCO_CLASSES_FILTER, coco_to_vehicle_type

reporter = TrafficReporter.from_env(cctv_code="DEV-CIKUTRA").start()


start_db_thread()
start_websocket_thread()

# =========================
# POLYGON ZONES (dari kamu)
# =========================
ZONE_LEFT = np.array([
    (583, 162),
    (712, 164),
    (612, 524),
    (522, 715),
    (13, 708),
    (12, 547),
    (584, 164),
], dtype=np.int32)

ZONE_RIGHT = np.array([
    (795, 165),
    (918, 162),
    (1276, 394),
    (1273, 700),
    (1016, 699),
    (925, 510),
    (792, 161),
], dtype=np.int32)

ZONE_TOP = np.array([
    (3,329),
    (140,233),
    (301,208),
    (463,169),
    (560,139),
    (596,119),
    (863,166),
    (1192,229),
    (1207,321),
    (1241,372),
    (1268,386),
    (1267,708),
    (2,708),
], dtype=np.int32)


# =========================
# ZONA CUSTOM: kalau ada zones.json (dibuat dengan customzone.py),
# zona di atas diganti dengan isi file itu.
# =========================
import json as _json, os as _os
_ZONES_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "zones.json")
if _os.path.exists(_ZONES_FILE):
    with open(_ZONES_FILE, encoding="utf-8") as _f:
        _z = _json.load(_f)
    if len(_z.get("top", [])) >= 3:
        ZONE_TOP = np.array(_z["top"], dtype=np.int32)
    if len(_z.get("left", [])) >= 3:
        ZONE_LEFT = np.array(_z["left"], dtype=np.int32)
    if len(_z.get("right", [])) >= 3:
        ZONE_RIGHT = np.array(_z["right"], dtype=np.int32)
    print("Zona dimuat dari", _ZONES_FILE)


direction_history = {}
MOVEMENT_THRESHOLD = 5
MIN_FRAMES = 6

zone_entry_position = {}
MIN_DISPLACEMENT = 25   # pixel minimum agar dianggap benar-benar bergerak

def inside_poly(cx, cy, poly):
    # True jika titik di dalam / tepi polygon
    return cv2.pointPolygonTest(poly, (float(cx), float(cy)), False) >= 0

# =========================
# MODEL + TRACKER
# =========================
model = YOLO("../yolo11n.pt")

# car, motorcycle, bus, truck (mapping ke VehicleType SoloMove ada di solomove/vehicle_types.py)
VEH_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
CLASSES_FILTER = COCO_CLASSES_FILTER

# =========================
# VIDEO
# =========================
# url = "https://cctv.atcs-dishubkbb.id/memfs/8c6a68c4-5fa5-44b6-8be5-72860ba26ba8.m3u8"  # mati (balas HTML, bukan video)
# Bisa diganti tanpa edit kode: VIDEO_SOURCE=../dataset/malam.mp4 python main.py
url = os.environ.get("VIDEO_SOURCE", "https://atcs-dishub.bandung.go.id:1990/Cikutra/index.m3u8")

# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# VIDEO_SOURCE = os.path.join(BASE_DIR, "sample.mp4")

cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
# cap = cv2.VideoCapture(VIDEO_SOURCE)
print("Video opened:", cap.isOpened())

if not cap.isOpened():
    print("Gagal buka stream:", url)
    print("Cek apakah URL masih hidup (buka di VLC / curl -I), atau pakai file lokal:")
    print("  VIDEO_SOURCE=../dataset/malam.mp4 python main.py")
    raise SystemExit

# overlay
def draw_transparent_polygon(img, poly, color=(0, 255, 255), alpha=0.25):
    """
    img   : frame BGR
    poly  : np.array shape (N,2) int32
    color : warna fill polygon (BGR)
    alpha : 0.0 - 1.0 (makin besar makin pekat)
    """
    overlay = img.copy()
    cv2.fillPoly(overlay, [poly], color)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)


# =========================
# COUNTING STATE
# =========================
prev_inside = {
    "top": {},      # track_id -> bool (sebelumnya inside atau tidak)
}
counted = {
    "top": set(),   # track_id yang sudah di-count
}
total = {"top": 0}

# =========================
# CONTRA FLOW STATE
# =========================
prev_position = {}
contra_ids = set()
contra_total = 0

def main():
    global contra_total

    def mouse_callback(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            print(f"({x}, {y}),")

    cv2.namedWindow("debug")
    cv2.setMouseCallback("debug", mouse_callback)

    while True:
        ret, frame = cap.read()

        if not ret:
            print("Frame tidak terbaca.")
            break

        frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
        clean_frame = frame.copy()  # untuk foto bukti pelanggaran (tanpa overlay)

        # h, w = frame.shape[:2]
        h, w = frame.shape[:2]

        LEFT_LANE_MIN_X = 0
        LEFT_LANE_MAX_X = 640

        RIGHT_LANE_MIN_X = 640
        RIGHT_LANE_MAX_X = 1280

        LEFT_LINE_Y = 360
        RIGHT_LINE_Y = 360

        # cv2.line(frame, (LEFT_LANE_MIN_X, LEFT_LINE_Y),
        #          (LEFT_LANE_MAX_X, LEFT_LINE_Y), (255, 0, 0), 2)
        #
        # cv2.line(frame, (RIGHT_LANE_MIN_X, RIGHT_LINE_Y),
        #          (RIGHT_LANE_MAX_X, RIGHT_LINE_Y), (255, 0, 0), 2)


        # if not ret:
        #     print("Frame tidak terbaca.")
        #     break
        # Track (deteksi + ByteTrack)
        r = model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=0.3,
            iou=0.6,
            classes=CLASSES_FILTER,
            verbose=False
        )[0]

        # draw_transparent_polygon(frame, ZONE_TOP, color=(0, 0, 255), alpha=0.20)
        draw_transparent_polygon(frame, ZONE_LEFT, color=(255, 0, 0), alpha=0.08)
        draw_transparent_polygon(frame, ZONE_RIGHT, color=(0, 255, 0), alpha=0.08)
        #
        cv2.polylines(frame, [ZONE_LEFT], True, (255,0,0), 2)
        cv2.polylines(frame, [ZONE_RIGHT], True, (0,255,0), 2)

        # Draw polygon zones
        # cv2.polylines(frame, [ZONE_TOP], True, (0,255,255), 2)
        cv2.putText(frame, "COUNT ZONE", (60, 370), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)


        boxes = r.boxes
        if boxes is not None and boxes.id is not None:
            xyxy = boxes.xyxy.cpu().numpy()
            cls = boxes.cls.cpu().numpy().astype(int)
            conf = boxes.conf.cpu().numpy()
            ids = boxes.id.cpu().numpy().astype(int)

            for (x1,y1,x2,y2), c, cf, tid in zip(xyxy, cls, conf, ids):
                if c not in VEH_CLASSES:
                    continue

                x1,y1,x2,y2 = map(int, [x1,y1,x2,y2])

                # titik untuk cek zona: bottom-center lebih stabil untuk kendaraan
                cx = (x1 + x2) // 2
                cy = y2

                prev_cy = prev_position.get(tid)

                in_left = inside_poly(cx, cy, ZONE_LEFT)
                in_right = inside_poly(cx, cy, ZONE_RIGHT)

                current_zone = None
                if in_left:
                    current_zone = "left"
                elif in_right:
                    current_zone = "right"

                # =========================
                # Saat pertama kali masuk zona
                # =========================
                if current_zone and tid not in zone_entry_position:
                    zone_entry_position[tid] = cy

                # =========================
                # Jika sudah punya entry point
                # =========================
                if current_zone and tid in zone_entry_position:

                    entry_cy = zone_entry_position[tid]
                    displacement = cy - entry_cy

                    wrong_direction = False

                    # JALUR KIRI → harus naik (cy makin kecil)
                    if current_zone == "left":
                        if displacement > MIN_DISPLACEMENT:
                            wrong_direction = True

                    # JALUR KANAN → harus turun (cy makin besar)
                    elif current_zone == "right":
                        if displacement < -MIN_DISPLACEMENT:
                            wrong_direction = True

                    if wrong_direction and tid not in contra_ids:
                        contra_ids.add(tid)
                        contra_total += 1

                        # Bukti pelanggaran → Core SoloMove (dikirim di thread latar belakang)
                        vtype = coco_to_vehicle_type(c)
                        if vtype:
                            evidence = clean_frame.copy()
                            cv2.rectangle(evidence, (x1, y1), (x2, y2), (0, 0, 255), 3)
                            ok, jpg = cv2.imencode(".jpg", evidence, [cv2.IMWRITE_JPEG_QUALITY, 85])
                            if ok:
                                lane = "kiri" if current_zone == "left" else "kanan"
                                reporter.report_violation(
                                    vtype, jpg.tobytes(),
                                    description=f"Melaju berlawanan arah di jalur {lane}",
                                    confidence=float(cf),
                                )

                # reset kalau keluar zona
                if not current_zone and tid in zone_entry_position:
                    del zone_entry_position[tid]

                prev_position[tid] = cy

                # ====== cek inside
                in_top = inside_poly(cx, cy, ZONE_TOP)

                vehicle_type = VEH_CLASSES[c]

                # ====== event "baru masuk TOP"
                was_top = prev_inside["top"].get(tid, False)
                if in_top and (not was_top) and (tid not in counted["top"]):
                    counted["top"].add(tid)
                    total["top"] += 1
                    send_to_db(
                                "Simpang Cikutra",          
                                "utara",          
                                datetime.now(),  
                                vehicle_type,          
                                1,               
                            )
                    reporter.record_coco(c)


                prev_inside["top"][tid] = in_top

                # Draw bbox
                # box_color = (0, 255, 0)
                if tid in contra_ids:
                    box_color = (0, 0, 255)
                else:
                    box_color = (0, 255, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 1)

                # label + ID (text putih, background hijau)
                label = f"{VEH_CLASSES[c]} {cf:.2f}"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.6
                thickness = 2

                # hitung ukuran text
                (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)

                # posisi label (di atas bbox kalau muat, kalau tidak taruh di dalam bbox)
                tx = x1
                ty = y1 - 6
                if ty - th - baseline < 0:
                    ty = y1 + th + baseline + 6  # pindah ke bawah/inside

                # background rectangle (filled)
                bg_tl = (tx, ty - th - baseline)      # top-left
                bg_br = (tx + tw, ty + baseline)      # bottom-right
                cv2.rectangle(frame, bg_tl, bg_br, box_color, -1)

                # teks putih di atas background
                cv2.putText(frame, label, (tx, ty), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

                # draw point
                cv2.circle(frame, (cx, cy), 3, (0, 0, 255), -1)


        # show totals (background merah, text putih)
        text = f"COUNT: {total['top']}"
        pos = (30, 120)  # (x, y) baseline text

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 1.0
        thickness = 2

        (tw, th), baseline = cv2.getTextSize(text, font, font_scale, thickness)

        x, y = pos
        bg_tl = (x - 8, y - th - baseline - 8)
        bg_br = (x + tw + 8, y + baseline + 8)

        # kotak merah (filled)
        cv2.rectangle(frame, bg_tl, bg_br, (0, 0, 255), -1)

        # teks putih
        cv2.putText(frame, text, (x, y), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

        contra_text = f"CONTRA: {contra_total}"
        pos2 = (30, 170)

        (tw2, th2), baseline2 = cv2.getTextSize(contra_text, font, font_scale, thickness)

        bg_tl2 = (pos2[0] - 8, pos2[1] - th2 - baseline2 - 8)
        bg_br2 = (pos2[0] + tw2 + 8, pos2[1] + baseline2 + 8)

        cv2.rectangle(frame, bg_tl2, bg_br2, (0, 0, 255), -1)
        cv2.putText(frame, contra_text, pos2, font, font_scale,
                    (255,255,255), thickness, cv2.LINE_AA)

        send_frame_to_clients(frame)
        # cv2.imshow("debug", frame)
        # if cv2.waitKey(1) & 0xFF == 27:
        #     break

        # cv2.imshow("Count Enter Zone (ByteTrack)", frame)
        # if cv2.waitKey(1) & 0xFF == 27:
        #     break

if __name__ == "__main__":
    try:
        main()
    finally:
        reporter.stop()  # kirim sisa data sebelum keluar
