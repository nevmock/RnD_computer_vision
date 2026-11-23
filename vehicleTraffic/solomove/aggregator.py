"""Penjumlah kendaraan per bucket waktu (default 5 menit) per CCTV per jenis kendaraan.

Nilai yang dikirim ke Core selalu **total kumulatif** bucket tersebut. Core melakukan upsert
per (cctvId, bucketStart, vehicleType) dan MENIMPA nilai lama, sehingga bucket yang masih
berjalan boleh dikirim berulang kali (dashboard jadi mendekati real-time) dan aman dikirim ulang.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .vehicle_types import VEHICLE_TYPES

BucketKey = Tuple[str, datetime, str]  # (cctvCode, bucketStart UTC, vehicleType)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("Waktu harus timezone-aware (pakai datetime.now(timezone.utc))")
    return at.astimezone(timezone.utc)


def iso_z(at: datetime) -> str:
    """Format ISO 8601 UTC dengan akhiran Z, presisi milidetik."""
    at = to_utc(at)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


@dataclass
class _Bucket:
    count: int = 0
    speed_sum: float = 0.0
    speed_n: int = 0


class BucketAggregator:
    def __init__(self, bucket_minutes: int = 5):
        if bucket_minutes <= 0 or 60 % bucket_minutes != 0:
            raise ValueError("bucket_minutes harus pembagi 60 (1, 2, 3, 5, 10, 15, 30, 60)")
        self.bucket_minutes = bucket_minutes
        self._buckets: Dict[BucketKey, _Bucket] = {}
        self._dirty: set = set()
        self._lock = threading.Lock()

    def bucket_start(self, at: datetime) -> datetime:
        at = to_utc(at)
        minute = at.minute - (at.minute % self.bucket_minutes)
        return at.replace(minute=minute, second=0, microsecond=0)

    def add(self, cctv_code: str, vehicle_type: str, at: Optional[datetime] = None,
            speed_kmh: Optional[float] = None) -> None:
        if vehicle_type not in VEHICLE_TYPES:
            raise ValueError(f"vehicle_type tidak dikenal: {vehicle_type}")
        key = (cctv_code, self.bucket_start(at or utc_now()), vehicle_type)
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            bucket.count += 1
            if speed_kmh is not None:
                bucket.speed_sum += float(speed_kmh)
                bucket.speed_n += 1
            self._dirty.add(key)

    def drain(self, now: Optional[datetime] = None) -> List[dict]:
        """Ambil item untuk bucket yang berubah sejak drain terakhir (nilai kumulatif),
        lalu buang bucket yang sudah tertutup dan sudah terkirim."""
        now = to_utc(now or utc_now())
        width = timedelta(minutes=self.bucket_minutes)
        with self._lock:
            items = [self._to_item(key, self._buckets[key]) for key in sorted(self._dirty, key=_sort_key)]
            self._dirty.clear()
            closed = [key for key in self._buckets if key[1] + width <= now]
            for key in closed:
                del self._buckets[key]
        return items

    def pending_keys(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _to_item(self, key: BucketKey, bucket: _Bucket) -> dict:
        cctv_code, start, vehicle_type = key
        item = {
            "cctvCode": cctv_code,
            "bucketStart": iso_z(start),
            "bucketMinutes": self.bucket_minutes,
            "vehicleType": vehicle_type,
            "count": bucket.count,
        }
        if bucket.speed_n:
            item["avgSpeedKmh"] = round(bucket.speed_sum / bucket.speed_n, 1)
        return item


def _sort_key(key: BucketKey):
    return (key[1], key[0], key[2])
