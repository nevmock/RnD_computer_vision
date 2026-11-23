"""Integrasi AI worker → Core API SoloMove (Internal Ingest API).

Lihat solomove/README.md untuk cara pakai.
"""
from .aggregator import BucketAggregator
from .config import Settings
from .core_client import CoreClient, CoreError
from .outbox import Outbox
from .reporter import NullReporter, TrafficReporter
from .vehicle_types import COCO_CLASSES_FILTER, COCO_TO_VEHICLE_TYPE, VEHICLE_TYPES, coco_to_vehicle_type

__all__ = [
    "BucketAggregator", "Settings", "CoreClient", "CoreError", "Outbox",
    "TrafficReporter", "NullReporter",
    "COCO_CLASSES_FILTER", "COCO_TO_VEHICLE_TYPE", "VEHICLE_TYPES", "coco_to_vehicle_type",
]
