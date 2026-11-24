"""Deteksi lawan arah berbasis vektor arah per lajur.

Versi lama hanya mengecek gerak naik/turun di layar (perpindahan y > 25 px). Di kamera yang
menyorot simpang, kendaraan yang berbelok dengan sah ikut tertangkap sebagai pelanggar.

Versi ini:
- tiap lajur punya ARAH SEHARUSNYA berupa vektor (bisa miring, mengikuti jalan di layar);
- kendaraan dianggap melawan arah jika gerak totalnya sejak masuk lajur DAN gerak terbarunya
  sama-sama mengarah berlawanan (menyimpang <= max_angle_deg dari arah kebalikan),
  jarak tempuhnya >= min_distance_px, dan kondisi itu bertahan min_frames update berturut-turut;
- gerak menyamping (kendaraan berbelok/menyeberang lajur) tidak dihitung pelanggaran;
- state track yang lama tidak terlihat dibuang (memori tidak tumbuh terus).

Modul ini tidak bergantung pada OpenCV supaya bisa dites tanpa GPU/video.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

DIRECTION_ALIASES = {
    "up": (0.0, -1.0),      # sumbu y gambar ke bawah, jadi "naik" = y mengecil
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
}
# Zona lama di zones.json: left = harus naik, right = harus turun
LEGACY_LANES = {"left": ("up", "kiri"), "right": ("down", "kanan")}


def normalize(vec: Sequence[float]) -> Point:
    x, y = float(vec[0]), float(vec[1])
    n = math.hypot(x, y)
    if n == 0:
        raise ValueError("Vektor arah tidak boleh nol")
    return (x / n, y / n)


def parse_direction(value) -> Point:
    """Terima: "up"/"down"/"left"/"right", vektor [dx, dy], atau panah [[x1, y1], [x2, y2]] (ekor → kepala)."""
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in DIRECTION_ALIASES:
            raise ValueError(f"Arah tidak dikenal: {value!r}")
        return DIRECTION_ALIASES[key]
    if isinstance(value, dict) and "from" in value and "to" in value:
        (x1, y1), (x2, y2) = value["from"], value["to"]
        return normalize((x2 - x1, y2 - y1))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        a, b = value
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return normalize((b[0] - a[0], b[1] - a[1]))
        return normalize((a, b))
    raise ValueError(f"Format arah tidak dikenal: {value!r}")


def point_in_polygon(x: float, y: float, polygon: Sequence[Point]) -> bool:
    """Ray casting. Titik tepat di tepi dianggap di dalam."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        # di tepi segmen?
        cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
        if abs(cross) < 1e-9 and min(xi, xj) <= x <= max(xi, xj) and min(yi, yj) <= y <= max(yi, yj):
            return True
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


@dataclass(frozen=True)
class Lane:
    name: str
    polygon: Tuple[Point, ...]
    direction: Point           # vektor satuan arah seharusnya
    label: str = ""            # untuk deskripsi bukti, mis. "kiri"

    def contains(self, x: float, y: float) -> bool:
        return point_in_polygon(x, y, self.polygon)

    def centroid(self) -> Point:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return (sum(xs) / len(xs), sum(ys) / len(ys))


def lanes_from_zones(zones: dict) -> List[Lane]:
    """Bangun daftar lajur dari isi zones.json / aiConfig.

    Format baru (dipakai aiConfig Core):
        {"lanes": [{"name": "kiri", "zone": [[x, y], ...], "direction": "up" | [dx, dy] | [[x1, y1], [x2, y2]]}]}
    Format lama (customzone.py):
        {"left": [[x, y], ...], "right": [...], "left_direction": [[x1, y1], [x2, y2]] (opsional)}
        Tanpa *_direction: left = naik, right = turun.
    Jika "lanes" ada isinya, hanya "lanes" yang dipakai (left/right diabaikan).
    """
    lanes: List[Lane] = []
    for i, item in enumerate(zones.get("lanes") or []):
        poly = tuple((float(x), float(y)) for x, y in item.get("zone", []))
        if len(poly) < 3:
            continue
        name = str(item.get("name") or f"lajur-{i + 1}")
        lanes.append(Lane(name, poly, parse_direction(item.get("direction", "up")), name))
    if lanes:
        return lanes
    for key, (default_dir, label) in LEGACY_LANES.items():
        poly = tuple((float(x), float(y)) for x, y in zones.get(key, []) or [])
        if len(poly) < 3:
            continue
        direction = zones.get(f"{key}_direction") or default_dir
        lanes.append(Lane(key, poly, parse_direction(direction), label))
    return lanes


@dataclass
class ContraflowConfig:
    min_distance_px: float = 40.0   # jarak tempuh minimum (piksel frame 1280x720) ke arah salah
    max_angle_deg: float = 45.0     # toleransi dari arah kebalikan: gerak >= 135° dari arah seharusnya
    min_frames: int = 5             # harus terdeteksi salah arah N update berturut-turut
    window: int = 5                 # gerak terbaru dihitung dari posisi N update sebelumnya
    min_step_px: float = 2.0        # gerak terbaru lebih kecil dari ini = kendaraan diam
    history: int = 30               # posisi yang disimpan per track
    ttl_frames: int = 90            # state dibuang jika track tidak terlihat selama ini


@dataclass
class ContraflowEvent:
    track_id: int
    lane: Lane
    point: Point
    distance_px: float
    angle_deg: float                # sudut gerak vs arah seharusnya (180 = berlawanan penuh)

    @property
    def description(self) -> str:
        lane = self.lane.label or self.lane.name
        return f"Melaju berlawanan arah di jalur {lane}"


@dataclass
class _TrackState:
    lane: Optional[str] = None
    entry: Optional[Point] = None
    points: Deque[Point] = field(default_factory=deque)
    streak: int = 0
    last_seen: int = 0


def _angle_deg(v: Point, d: Point) -> float:
    n = math.hypot(v[0], v[1])
    if n == 0:
        return 0.0
    cos = max(-1.0, min(1.0, (v[0] * d[0] + v[1] * d[1]) / n))
    return math.degrees(math.acos(cos))


class ContraflowDetector:
    def __init__(self, lanes: Iterable[Lane], config: Optional[ContraflowConfig] = None):
        self.lanes = list(lanes)
        self.config = config or ContraflowConfig()
        self._tracks: Dict[int, _TrackState] = {}
        self._flagged: Dict[int, int] = {}   # track_id -> frame terakhir terlihat
        self.total = 0

    def lane_at(self, x: float, y: float) -> Optional[Lane]:
        for lane in self.lanes:
            if lane.contains(x, y):
                return lane
        return None

    def is_flagged(self, track_id: int) -> bool:
        return int(track_id) in self._flagged

    def update(self, track_id: int, point: Point, frame_index: int) -> Optional[ContraflowEvent]:
        """Panggil sekali per track per frame dengan titik bawah-tengah bbox."""
        tid = int(track_id)
        x, y = float(point[0]), float(point[1])
        cfg = self.config
        if tid in self._flagged:
            self._flagged[tid] = frame_index
            return None

        state = self._tracks.setdefault(tid, _TrackState())
        state.last_seen = frame_index
        lane = self.lane_at(x, y)
        if lane is None:
            state.lane, state.entry, state.streak = None, None, 0
            state.points.clear()
            return None
        if state.lane != lane.name:                      # baru masuk / pindah lajur
            state.lane, state.entry, state.streak = lane.name, (x, y), 0
            state.points = deque(maxlen=max(cfg.history, cfg.window + 1))

        state.points.append((x, y))
        total = (x - state.entry[0], y - state.entry[1])
        ref = state.points[-cfg.window - 1] if len(state.points) > cfg.window else state.entry
        recent = (x - ref[0], y - ref[1])

        limit = 180.0 - cfg.max_angle_deg
        total_angle = _angle_deg(total, lane.direction)
        wrong_now = (
            math.hypot(*total) >= cfg.min_distance_px
            and total_angle >= limit
            and math.hypot(*recent) >= cfg.min_step_px
            and _angle_deg(recent, lane.direction) >= limit
        )
        state.streak = state.streak + 1 if wrong_now else 0
        if state.streak < cfg.min_frames:
            return None

        self._flagged[tid] = frame_index
        self._tracks.pop(tid, None)
        self.total += 1
        return ContraflowEvent(tid, lane, (x, y), round(math.hypot(*total), 1), round(total_angle, 1))

    def prune(self, frame_index: int) -> int:
        """Buang state track yang tidak terlihat > ttl_frames. Kembalikan jumlah yang dibuang."""
        ttl = self.config.ttl_frames
        stale = [t for t, s in self._tracks.items() if frame_index - s.last_seen > ttl]
        for t in stale:
            del self._tracks[t]
        stale_flags = [t for t, seen in self._flagged.items() if frame_index - seen > ttl]
        for t in stale_flags:
            del self._flagged[t]
        return len(stale) + len(stale_flags)

    def tracked(self) -> int:
        return len(self._tracks) + len(self._flagged)

    @classmethod
    def from_zones(cls, zones: dict, config: Optional[ContraflowConfig] = None) -> "ContraflowDetector":
        cfg = config or ContraflowConfig()
        for key, value in (zones.get("contraflow") or {}).items():   # override opsional dari zones.json / aiConfig
            if hasattr(cfg, key):
                setattr(cfg, key, type(getattr(cfg, key))(value))
        return cls(lanes_from_zones(zones), cfg)
