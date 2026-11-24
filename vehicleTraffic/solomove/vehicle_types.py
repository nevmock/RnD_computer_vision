"""Mapping kelas deteksi (COCO) ke enum VehicleType di Core API SoloMove."""
from __future__ import annotations

from typing import Optional

VEHICLE_TYPES = ("RODA_DUA", "RODA_TIGA", "RODA_EMPAT", "TRUK_BUS")

# COCO id → VehicleType. RODA_TIGA belum ada di COCO (perlu model custom).
COCO_TO_VEHICLE_TYPE = {
    3: "RODA_DUA",    # motorcycle
    2: "RODA_EMPAT",  # car
    5: "TRUK_BUS",    # bus
    7: "TRUK_BUS",    # truck
}

# Kelas yang perlu dideteksi model untuk SoloMove (dipakai sebagai `classes=` di YOLO).
COCO_CLASSES_FILTER = sorted(COCO_TO_VEHICLE_TYPE)


def coco_to_vehicle_type(class_id: int) -> Optional[str]:
    """Kembalikan VehicleType untuk kelas COCO, atau None jika tidak dipakai."""
    return COCO_TO_VEHICLE_TYPE.get(int(class_id))
