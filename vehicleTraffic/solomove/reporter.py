"""TrafficReporter: satu-satunya objek yang dipanggil dari main.py.

    reporter = TrafficReporter.from_env(cctv_code="DEV-CIKUTRA")   # NullReporter jika env belum diisi
    reporter.start()
    ...
    reporter.record_coco(class_id)                       # tiap kendaraan terhitung
    reporter.report_violation("RODA_DUA", jpg_bytes, description="Lawan arah di jalur kiri")

Semua pengiriman HTTP terjadi di thread latar belakang, jadi loop video tidak pernah tertahan
oleh jaringan. Jika Core mati, data menunggu di outbox SQLite dan dikirim ulang otomatis.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

from .aggregator import BucketAggregator, iso_z, utc_now
from .config import Settings
from .core_client import CoreClient, CoreError
from .outbox import KIND_TRAFFIC, KIND_VIOLATION, Outbox
from .vehicle_types import VEHICLE_TYPES, coco_to_vehicle_type

log = logging.getLogger("solomove")

TRAFFIC_BATCH = 500
VIOLATION_BATCH = 20


class TrafficReporter:
    def __init__(self, settings: Settings, client: Optional[CoreClient] = None,
                 outbox: Optional[Outbox] = None, aggregator: Optional[BucketAggregator] = None,
                 clock=time.time):
        self.settings = settings
        self.client = client or CoreClient(settings.core_url, settings.api_key, settings.request_timeout_sec)
        self.outbox = outbox or Outbox(settings.outbox_path, clock=clock)
        self.aggregator = aggregator or BucketAggregator(settings.bucket_minutes)
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_flush = 0.0
        self._next_heartbeat = 0.0
        self._next_prune = 0.0

    # ---------- pembuatan ----------
    @classmethod
    def from_env(cls, cctv_code: Optional[str] = None):
        """Reporter aktif jika SOLOMOVE_CORE_URL & SOLOMOVE_API_KEY diisi; selain itu NullReporter."""
        try:
            settings = Settings.from_env(cctv_code=cctv_code)
        except ValueError as exc:
            log.warning("SoloMove reporter nonaktif: %s", exc)
            print(f"[SoloMove] reporter nonaktif ({exc})", flush=True)
            return NullReporter()
        print(f"[SoloMove] kirim ke {settings.core_url} sebagai CCTV {settings.cctv_code}", flush=True)
        return cls(settings)

    # ---------- API untuk main.py ----------
    @property
    def enabled(self) -> bool:
        return True

    def start(self) -> "TrafficReporter":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="solomove-reporter", daemon=True)
            self._thread.start()
        return self

    def stop(self, flush: bool = True, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        if flush:
            self.flush_now()
        self.outbox.close()

    def record_vehicle(self, vehicle_type: str, at: Optional[datetime] = None,
                       speed_kmh: Optional[float] = None) -> None:
        self.aggregator.add(self.settings.cctv_code, vehicle_type, at=at, speed_kmh=speed_kmh)

    def record_coco(self, class_id: int, at: Optional[datetime] = None,
                    speed_kmh: Optional[float] = None) -> Optional[str]:
        vehicle_type = coco_to_vehicle_type(class_id)
        if vehicle_type:
            self.record_vehicle(vehicle_type, at=at, speed_kmh=speed_kmh)
        return vehicle_type

    def report_violation(self, vehicle_type: str, image_jpeg: bytes, occurred_at: Optional[datetime] = None,
                         description: Optional[str] = None, confidence: Optional[float] = None,
                         plate_number: Optional[str] = None, violation_type: str = "CONTRAFLOW",
                         external_id: Optional[str] = None) -> str:
        if vehicle_type not in VEHICLE_TYPES:
            raise ValueError(f"vehicle_type tidak dikenal: {vehicle_type}")
        at = occurred_at or utc_now()
        external_id = external_id or (
            f"{self.settings.cctv_code}-{at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}")
        meta = {
            "externalId": external_id,
            "cctvCode": self.settings.cctv_code,
            "type": violation_type,
            "vehicleType": vehicle_type,
            "occurredAt": iso_z(at),
            "plateNumber": plate_number,
            "description": description,
            "confidence": None if confidence is None else round(float(confidence), 3),
        }
        self.outbox.add_violation(meta, image_jpeg)
        return external_id

    # ---------- loop latar belakang ----------
    def flush_now(self) -> None:
        """Pindahkan bucket ke outbox lalu coba kirim semua yang jatuh tempo (dipakai saat stop & test)."""
        self.outbox.upsert_traffic(self.aggregator.drain())
        self._send_traffic()
        self._send_violations()

    def tick(self) -> None:
        now = self._clock()
        if now >= self._next_flush:
            self.outbox.upsert_traffic(self.aggregator.drain())
            self._next_flush = now + self.settings.flush_interval_sec
        if now >= self._next_heartbeat:
            self._send_heartbeat()
            self._next_heartbeat = now + self.settings.heartbeat_interval_sec
        if now >= self._next_prune:
            self.outbox.prune()
            self._next_prune = now + 3600
        self._send_traffic()
        self._send_violations()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # loop tidak boleh mati karena satu error
                log.exception("SoloMove reporter tick gagal")
            self._stop.wait(1.0)

    def _send_heartbeat(self) -> None:
        try:
            self.client.post_heartbeat([self.settings.cctv_code])
        except CoreError as exc:
            log.warning("Heartbeat gagal: %s", exc)

    def _send_traffic(self) -> None:
        rows = self.outbox.due(KIND_TRAFFIC, TRAFFIC_BATCH)
        if not rows:
            return
        try:
            result = self.client.post_traffic_counts([r.payload for r in rows])
        except CoreError as exc:
            self._on_error(rows, exc)
            return
        rejected = result.get("rejected") if isinstance(result, dict) else None
        if rejected:
            log.warning("Core menolak %d item traffic: %s", len(rejected), str(rejected)[:300])
        self.outbox.mark_sent(rows)

    def _send_violations(self) -> None:
        for row in self.outbox.due(KIND_VIOLATION, VIOLATION_BATCH):
            try:
                self.client.post_violation(row.payload, row.blob or b"")
            except CoreError as exc:
                if exc.status == 409:          # externalId sudah ada di Core
                    self.outbox.mark_sent([row])
                    continue
                self._on_error([row], exc)
                if exc.retryable:              # Core/jaringan bermasalah: tunggu tick berikutnya
                    return
                continue
            self.outbox.mark_sent([row])

    def _on_error(self, rows, exc: CoreError) -> None:
        detail = f"{exc} {exc.body}".strip()
        if exc.retryable:
            log.warning("Kirim ke Core gagal, dicoba ulang nanti: %s", detail)
            self.outbox.mark_retry(rows, detail)
        else:
            log.error("Core menolak permanen (%d baris ditandai dead): %s", len(rows), detail)
            self.outbox.mark_dead(rows, detail)


class NullReporter:
    """Dipakai saat env SoloMove belum diisi: semua method tidak melakukan apa-apa."""

    enabled = False

    def start(self):
        return self

    def stop(self, *args, **kwargs):
        pass

    def record_vehicle(self, *args, **kwargs):
        pass

    def record_coco(self, class_id, *args, **kwargs):
        return coco_to_vehicle_type(class_id)

    def report_violation(self, *args, **kwargs):
        return None

    def flush_now(self):
        pass

    def tick(self):
        pass
