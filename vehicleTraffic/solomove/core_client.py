"""Klien HTTP untuk Internal Ingest API Core SoloMove (/internal/v1).

Kontrak: docs/03-api-contract.md §9 di Project SoloMove.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional, Sequence

import requests

from .aggregator import iso_z, utc_now

# 4xx yang layak dicoba ulang: timeout, rate limit, dan kredensial (mungkin sedang diperbaiki).
# 409 tidak termasuk: untuk pelanggaran artinya externalId sudah tersimpan (dianggap terkirim).
RETRYABLE_4XX = {401, 403, 408, 425, 429}


class CoreError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def retryable(self) -> bool:
        if self.status is None:          # error jaringan / timeout
            return True
        if self.status >= 500:
            return True
        return self.status in RETRYABLE_4XX


class CoreClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0,
                 session: Optional[requests.Session] = None):
        self.base = base_url.rstrip("/") + "/internal/v1"
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": api_key, "User-Agent": "solomove-ai-worker/0.1"})

    # ---------- endpoint ----------
    def get_cctvs(self, ai_enabled: bool = True) -> List[dict]:
        data = self._request("GET", "/cctvs", params={"aiEnabled": str(ai_enabled).lower()})
        return data if isinstance(data, list) else []

    def post_traffic_counts(self, items: Sequence[dict]) -> dict:
        return self._request("POST", "/ingest/traffic-counts", json={"items": list(items)}) or {}

    def post_heartbeat(self, cctv_codes: Sequence[str], at: Optional[datetime] = None) -> dict:
        body = {"cctvCodes": list(cctv_codes), "at": iso_z(at or utc_now())}
        return self._request("POST", "/ingest/heartbeat", json=body) or {}

    def post_violation(self, meta: dict, image_jpeg: bytes) -> dict:
        files = {"image": (f"{meta['externalId']}.jpg", image_jpeg, "image/jpeg")}
        data = {"meta": json.dumps(meta, separators=(",", ":"))}
        return self._request("POST", "/ingest/violations", data=data, files=files) or {}

    # ---------- internal ----------
    def _request(self, method: str, path: str, **kwargs):
        try:
            resp = self.session.request(method, self.base + path, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise CoreError(f"{method} {path} gagal: {exc}") from exc
        if resp.status_code >= 400:
            raise CoreError(f"{method} {path} → HTTP {resp.status_code}", resp.status_code, resp.text[:500])
        if not resp.content:
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        # Envelope Core: {code, status, message, pagination, data, errors}
        if isinstance(payload, dict) and "data" in payload and "code" in payload:
            return payload["data"]
        return payload
