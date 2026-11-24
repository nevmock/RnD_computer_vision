# solomove — integrasi worker AI ke Core API SoloMove

Modul ini mengirim hasil AI ke **Internal Ingest API** milik Core (Express + PostgreSQL).
Worker **tidak** menulis ke database Core secara langsung.

| Data | Endpoint | Kapan |
|---|---|---|
| Jumlah kendaraan per bucket 5 menit per jenis | `POST /internal/v1/ingest/traffic-counts` | Tiap 30 detik (nilai **kumulatif** bucket berjalan, Core menimpa/upsert) |
| Bukti lawan arah (.jpg + meta) | `POST /internal/v1/ingest/violations` (multipart) | Saat terdeteksi |
| Heartbeat CCTV | `POST /internal/v1/ingest/heartbeat` | Tiap 60 detik |

Semua pengiriman berjalan di thread latar belakang. Jika Core mati atau jaringan putus, data
disimpan di antrean SQLite lokal (`solomove_outbox.sqlite3`) dan dikirim ulang otomatis
(backoff sampai 5 menit). Data yang ditolak permanen (HTTP 4xx validasi) ditandai `dead`.

## Environment variable

| Variabel | Wajib | Contoh |
|---|---|---|
| `SOLOMOVE_CORE_URL` | ya | `http://localhost:3001` |
| `SOLOMOVE_API_KEY` | ya | sama dengan `INTERNAL_API_KEY` di Core |
| `SOLOMOVE_CCTV_CODE` | tidak* | `SKA-001` (*default dari `main.py`, mis. `DEV-PAHLAWAN`) |
| `SOLOMOVE_BUCKET_MINUTES` | tidak | `5` |
| `SOLOMOVE_FLUSH_INTERVAL_SEC` | tidak | `30` |
| `SOLOMOVE_HEARTBEAT_INTERVAL_SEC` | tidak | `60` |
| `SOLOMOVE_OUTBOX_PATH` | tidak | `solomove_outbox.sqlite3` |

Jika `SOLOMOVE_CORE_URL` / `SOLOMOVE_API_KEY` kosong, `main.py` tetap jalan seperti sebelumnya
(reporter nonaktif).

## Coba lokal tanpa backend siswa

```bash
cd vehicleTraffic
python tools/mock_core.py --port 3001 --api-key dev-key --cctv DEV-PAHLAWAN

# terminal lain
cd vehicleTraffic/Bandung
SOLOMOVE_CORE_URL=http://localhost:3001 SOLOMOVE_API_KEY=dev-key python main.py
```
Lihat data yang masuk: http://localhost:3001/mock/state · foto bukti: `vehicleTraffic/mock_core_uploads/`.

## Pakai di kode

```python
from solomove import TrafficReporter, coco_to_vehicle_type

reporter = TrafficReporter.from_env(cctv_code="DEV-PAHLAWAN").start()
reporter.record_coco(class_id)                                   # kendaraan masuk zona hitung
reporter.report_violation("RODA_DUA", jpg_bytes,                 # lawan arah
                          description="Melaju berlawanan arah di jalur kiri", confidence=0.87)
reporter.stop()                                                  # saat keluar: kirim sisa data
```

Mapping kelas COCO → `VehicleType`: motorcycle → `RODA_DUA`, car → `RODA_EMPAT`,
bus & truck → `TRUK_BUS`. `RODA_TIGA` belum ada (perlu model custom).

## Deteksi lawan arah (`contraflow.py`)

Tiap lajur punya **arah seharusnya** (vektor, boleh miring). Kendaraan dianggap melawan arah jika:

1. jarak tempuh sejak masuk lajur ≥ `min_distance_px` (default 40 px di frame 1280×720),
2. arah gerak total **dan** arah gerak 5 update terakhir sama-sama ≥ 135° dari arah seharusnya
   (`max_angle_deg` = 45 → toleransi dari arah kebalikan),
3. kondisi itu bertahan `min_frames` update berturut-turut (default 5).

Gerak menyamping (berbelok ~90°), kendaraan diam/antre, dan goyangan bbox tidak dihitung.
Satu kendaraan (track ID) dilaporkan sekali. State track yang hilang > 90 frame dibuang.

```python
from solomove import ContraflowDetector

det = ContraflowDetector.from_zones(zones)          # isi zones.json
event = det.update(track_id, (cx, cy), frame_idx)   # titik bawah-tengah bbox, tiap frame
if event:
    reporter.report_violation(vtype, jpg, description=event.description, confidence=conf)
if frame_idx % 30 == 0:
    det.prune(frame_idx)
```

Format zona yang diterima:

```jsonc
// format customzone.py (lama, tetap jalan)
{"left": [[x, y], ...], "right": [[x, y], ...],
 "left_direction": [[x1, y1], [x2, y2]],      // opsional: panah ekor → kepala (tombol D)
 "contraflow": {"min_distance_px": 60}}        // opsional: override ambang
// format aiConfig Core (banyak lajur)
{"lanes": [{"name": "kiri", "zone": [[x, y], ...], "direction": "up" | [dx, dy] | [[x1, y1], [x2, y2]]}]}
```
Tanpa `*_direction`: `left` = naik, `right` = turun. Jika `lanes` diisi, `left`/`right` diabaikan.

## Tes

```bash
cd vehicleTraffic
python -m unittest discover -s tests -v
```
Tidak butuh GPU, model, atau video.
