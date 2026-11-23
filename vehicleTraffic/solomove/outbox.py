"""Antrean kirim-ulang lokal (SQLite) supaya data tidak hilang saat Core API mati / jaringan putus.

- Traffic count disimpan per kunci bucket (cctv, bucketStart, vehicleType). Upsert menimpa
  payload dengan nilai kumulatif terbaru dan menaikkan `version`; baris hanya dihapus jika
  version-nya sama dengan yang dikirim (nilai yang lebih baru tidak ikut terhapus).
- Pelanggaran disimpan per externalId beserta gambar .jpg.
- Baris yang ditolak permanen oleh Core (4xx non-retryable) ditandai `dead` untuk diperiksa manual.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

KIND_TRAFFIC = "traffic"
KIND_VIOLATION = "violation"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT    NOT NULL,
    dedupe_key      TEXT    NOT NULL UNIQUE,
    payload         TEXT    NOT NULL,
    blob            BLOB,
    version         INTEGER NOT NULL DEFAULT 1,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL    NOT NULL DEFAULT 0,
    dead            INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (dead, kind, next_attempt_at);
"""


@dataclass
class OutboxRow:
    id: int
    kind: str
    payload: dict
    blob: Optional[bytes]
    version: int
    attempts: int


class Outbox:
    def __init__(self, path: str, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    # ---------- tulis ----------
    def upsert_traffic(self, items: Iterable[dict]) -> int:
        now = self._clock()
        rows = [(KIND_TRAFFIC, _traffic_key(it), json.dumps(it, separators=(",", ":")), now, now) for it in items]
        if not rows:
            return 0
        with self._lock:
            self._db.executemany(
                """INSERT INTO outbox (kind, dedupe_key, payload, next_attempt_at, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(dedupe_key) DO UPDATE SET
                     payload = excluded.payload, version = outbox.version + 1,
                     attempts = 0, next_attempt_at = excluded.next_attempt_at, dead = 0, last_error = NULL""",
                rows,
            )
        return len(rows)

    def add_violation(self, meta: dict, image_jpeg: bytes) -> bool:
        now = self._clock()
        with self._lock:
            cur = self._db.execute(
                """INSERT OR IGNORE INTO outbox (kind, dedupe_key, payload, blob, next_attempt_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (KIND_VIOLATION, "v:" + meta["externalId"], json.dumps(meta, separators=(",", ":")),
                 sqlite3.Binary(image_jpeg), now, now),
            )
        return cur.rowcount == 1

    # ---------- baca ----------
    def due(self, kind: str, limit: int) -> List[OutboxRow]:
        with self._lock:
            cur = self._db.execute(
                """SELECT id, kind, payload, blob, version, attempts FROM outbox
                   WHERE dead = 0 AND kind = ? AND next_attempt_at <= ?
                   ORDER BY id LIMIT ?""",
                (kind, self._clock(), limit),
            )
            return [OutboxRow(r[0], r[1], json.loads(r[2]), r[3], r[4], r[5]) for r in cur.fetchall()]

    def counts(self) -> dict:
        with self._lock:
            cur = self._db.execute("SELECT kind, dead, COUNT(*) FROM outbox GROUP BY kind, dead")
            out = {}
            for kind, dead, n in cur.fetchall():
                out[f"{kind}{'_dead' if dead else ''}"] = n
            return out

    # ---------- hasil kirim ----------
    def mark_sent(self, rows: Iterable[OutboxRow]) -> None:
        with self._lock:
            self._db.executemany("DELETE FROM outbox WHERE id = ? AND version = ?",
                                 [(r.id, r.version) for r in rows])

    def mark_retry(self, rows: Iterable[OutboxRow], error: str, max_backoff: float = 300.0) -> None:
        now = self._clock()
        with self._lock:
            self._db.executemany(
                """UPDATE outbox SET attempts = attempts + 1, last_error = ?,
                   next_attempt_at = ? WHERE id = ? AND version = ?""",
                [(error[:500], now + min(max_backoff, 2.0 ** min(r.attempts + 1, 16)), r.id, r.version)
                 for r in rows],
            )

    def mark_dead(self, rows: Iterable[OutboxRow], error: str) -> None:
        with self._lock:
            self._db.executemany(
                "UPDATE outbox SET dead = 1, last_error = ? WHERE id = ? AND version = ?",
                [(error[:500], r.id, r.version) for r in rows],
            )

    def prune(self, older_than_sec: float = 7 * 24 * 3600) -> int:
        """Buang baris (termasuk dead) yang lebih tua dari batas, agar file tidak tumbuh tanpa batas."""
        with self._lock:
            cur = self._db.execute("DELETE FROM outbox WHERE created_at < ?", (self._clock() - older_than_sec,))
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _traffic_key(item: dict) -> str:
    return f"t:{item['cctvCode']}|{item['bucketStart']}|{item['vehicleType']}"
