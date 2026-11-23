"""Konfigurasi pengirim data ke Core API SoloMove (dibaca dari environment variable)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Settings:
    core_url: str                       # mis. http://localhost:3001 (tanpa /internal/v1)
    api_key: str                        # sama dengan INTERNAL_API_KEY di Core
    cctv_code: str                      # Cctv.code di Core, mis. "SKA-001"
    bucket_minutes: int = 5
    flush_interval_sec: float = 30.0    # seberapa sering bucket berjalan dikirim ulang (kumulatif)
    heartbeat_interval_sec: float = 60.0
    outbox_path: str = "solomove_outbox.sqlite3"
    request_timeout_sec: float = 10.0

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, cctv_code: Optional[str] = None) -> "Settings":
        env = os.environ if env is None else env
        core_url = (env.get("SOLOMOVE_CORE_URL") or "").rstrip("/")
        api_key = env.get("SOLOMOVE_API_KEY") or ""
        code = env.get("SOLOMOVE_CCTV_CODE") or cctv_code or ""
        missing = [name for name, value in (
            ("SOLOMOVE_CORE_URL", core_url),
            ("SOLOMOVE_API_KEY", api_key),
            ("SOLOMOVE_CCTV_CODE", code),
        ) if not value]
        if missing:
            raise ValueError("Env belum diisi: " + ", ".join(missing))
        return cls(
            core_url=core_url,
            api_key=api_key,
            cctv_code=code,
            bucket_minutes=int(env.get("SOLOMOVE_BUCKET_MINUTES", 5)),
            flush_interval_sec=float(env.get("SOLOMOVE_FLUSH_INTERVAL_SEC", 30)),
            heartbeat_interval_sec=float(env.get("SOLOMOVE_HEARTBEAT_INTERVAL_SEC", 60)),
            outbox_path=env.get("SOLOMOVE_OUTBOX_PATH", "solomove_outbox.sqlite3"),
            request_timeout_sec=float(env.get("SOLOMOVE_TIMEOUT_SEC", 10)),
        )
