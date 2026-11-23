"""Mock Core API SoloMove untuk menguji worker AI tanpa menunggu backend siswa.

Menjalankan endpoint Internal Ingest API (docs/03 §9) di memori + validasi payload.

    python tools/mock_core.py --port 3001 --api-key dev-key --cctv DEV-CIKUTRA
    # lalu di terminal lain (folder Bandung):
    SOLOMOVE_CORE_URL=http://localhost:3001 SOLOMOVE_API_KEY=dev-key SOLOMOVE_CCTV_CODE=DEV-CIKUTRA python main.py

Cek isi: http://localhost:3001/mock/state
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VEHICLE_TYPES = {"RODA_DUA", "RODA_TIGA", "RODA_EMPAT", "TRUK_BUS"}
VIOLATION_TYPES = {"CONTRAFLOW"}


class MockCoreState:
    def __init__(self, api_key: str, cctv_codes, upload_dir: str = None):
        self.api_key = api_key
        self.cctvs = [{"id": i + 1, "code": code, "name": code, "streamUrl": "", "streamType": "HLS",
                       "aiConfig": None} for i, code in enumerate(cctv_codes)]
        self.traffic = {}       # (code, bucketStart, type) -> item (ditimpa = upsert)
        self.violations = {}    # externalId -> meta
        self.heartbeats = {}    # code -> at
        self.requests = 0
        self.upload_dir = upload_dir
        self.lock = threading.Lock()

    def summary(self) -> dict:
        with self.lock:
            return {
                "requests": self.requests,
                "trafficRows": len(self.traffic),
                "traffic": sorted(self.traffic.values(), key=lambda x: (x["bucketStart"], x["cctvCode"], x["vehicleType"])),
                "violations": list(self.violations.values()),
                "heartbeats": self.heartbeats,
            }


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_traffic_item(item: dict, known_codes) -> str:
    """Kembalikan pesan error, atau '' jika valid (aturan sama dengan yang harus dibuat di Core)."""
    if item.get("cctvCode") not in known_codes:
        return "cctvCode tidak dikenal"
    if item.get("vehicleType") not in VEHICLE_TYPES:
        return "vehicleType tidak valid"
    count = item.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return "count harus integer >= 0"
    minutes = item.get("bucketMinutes", 5)
    try:
        start = _parse_iso(item["bucketStart"])
    except (KeyError, ValueError, AttributeError):
        return "bucketStart bukan ISO 8601"
    if start.tzinfo is None:
        return "bucketStart harus UTC (akhiran Z)"
    if start.second or start.microsecond or start.minute % minutes:
        return f"bucketStart harus kelipatan {minutes} menit"
    speed = item.get("avgSpeedKmh")
    if speed is not None and not isinstance(speed, (int, float)):
        return "avgSpeedKmh harus angka"
    return ""


def make_handler(state: MockCoreState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print("[mock-core]", self.address_string(), fmt % args, flush=True)

        def _send(self, status: int, data=None, message: str = "OK"):
            body = json.dumps({"code": status, "status": "OK" if status < 400 else "ERROR", "message": message,
                               "pagination": None, "data": data if status < 400 else None,
                               "errors": None if status < 400 else {"message": message}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            with state.lock:
                state.requests += 1
            if self.headers.get("x-api-key") != state.api_key:
                self._send(401, message="API key tidak valid")
                return False
            return True

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/", "/mock/state"):      # untuk dibuka di browser, tanpa API key
                return self._send(200, state.summary())
            if not self._authorized():
                return
            if path == "/internal/v1/cctvs":
                return self._send(200, state.cctvs)
            self._send(404, message="Route not found")

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            if not self._authorized():
                return
            if path == "/internal/v1/ingest/traffic-counts":
                return self._traffic()
            if path == "/internal/v1/ingest/heartbeat":
                return self._heartbeat()
            if path == "/internal/v1/ingest/violations":
                return self._violation()
            self._send(404, message="Route not found")

        def _traffic(self):
            try:
                items = json.loads(self._body() or b"{}").get("items")
            except ValueError:
                return self._send(400, message="Body bukan JSON")
            if not isinstance(items, list) or not items:
                return self._send(422, message="items wajib berupa array tidak kosong")
            known = {c["code"] for c in state.cctvs}
            accepted, rejected = 0, []
            with state.lock:
                for i, item in enumerate(items):
                    err = validate_traffic_item(item, known)
                    if err:
                        rejected.append({"index": i, "reason": err})
                        continue
                    key = (item["cctvCode"], item["bucketStart"], item["vehicleType"])
                    state.traffic[key] = item       # upsert: timpa dengan nilai kumulatif terbaru
                    state.heartbeats[item["cctvCode"]] = datetime.now(timezone.utc).isoformat()
                    accepted += 1
            print(f"[mock-core] traffic-counts accepted={accepted} rejected={len(rejected)}", flush=True)
            self._send(200, {"accepted": accepted, "rejected": rejected})

        def _heartbeat(self):
            try:
                body = json.loads(self._body() or b"{}")
            except ValueError:
                return self._send(400, message="Body bukan JSON")
            codes = body.get("cctvCodes")
            if not isinstance(codes, list):
                return self._send(422, message="cctvCodes wajib array")
            with state.lock:
                for code in codes:
                    state.heartbeats[code] = body.get("at")
            self._send(200, {"updated": len(codes)})

        def _violation(self):
            ctype = self.headers.get("Content-Type", "")
            if not ctype.startswith("multipart/form-data"):
                return self._send(400, message="Harus multipart/form-data")
            raw = b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + self._body()
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            fields = {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                fields[name] = part.get_payload(decode=True)
            if "meta" not in fields or "image" not in fields:
                return self._send(422, message="Field meta dan image wajib ada")
            try:
                meta = json.loads(fields["meta"])
            except ValueError:
                return self._send(422, message="meta bukan JSON")
            image = fields["image"] or b""
            missing = [k for k in ("externalId", "cctvCode", "type", "vehicleType", "occurredAt") if not meta.get(k)]
            if missing:
                return self._send(422, message="meta kurang: " + ", ".join(missing))
            if meta["vehicleType"] not in VEHICLE_TYPES or meta["type"] not in VIOLATION_TYPES:
                return self._send(422, message="vehicleType/type tidak valid")
            if not image.startswith(b"\xff\xd8"):
                return self._send(422, message="image bukan JPEG")
            with state.lock:
                if meta["externalId"] in state.violations:
                    return self._send(200, {"duplicate": True})
                state.violations[meta["externalId"]] = dict(meta, imageBytes=len(image))
            if state.upload_dir:
                os.makedirs(state.upload_dir, exist_ok=True)
                with open(os.path.join(state.upload_dir, meta["externalId"] + ".jpg"), "wb") as f:
                    f.write(image)
            print(f"[mock-core] violation {meta['externalId']} {meta['vehicleType']} ({len(image)} B)", flush=True)
            self._send(201, {"duplicate": False, "externalId": meta["externalId"]})

    return Handler


def serve(port: int, api_key: str, cctv_codes, upload_dir=None, host: str = "127.0.0.1"):
    state = MockCoreState(api_key, cctv_codes, upload_dir)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    return server, state


def main():
    parser = argparse.ArgumentParser(description="Mock Core API SoloMove (Internal Ingest API)")
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--api-key", default=os.environ.get("SOLOMOVE_API_KEY", "dev-key"))
    parser.add_argument("--cctv", action="append", default=None, help="Kode CCTV yang dikenal (boleh berulang)")
    parser.add_argument("--upload-dir", default="mock_core_uploads")
    args = parser.parse_args()
    codes = args.cctv or ["DEV-CIKUTRA", "DEV-DIPONEGORO"]
    server, _ = serve(args.port, args.api_key, codes, args.upload_dir, args.host)
    print(f"[mock-core] jalan di http://{args.host}:{args.port}  api-key={args.api_key}  cctv={codes}", flush=True)
    print(f"[mock-core] lihat data masuk di browser: http://localhost:{args.port}/mock/state", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
