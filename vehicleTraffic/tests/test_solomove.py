"""Tes modul solomove (tanpa GPU / video). Jalankan dari folder vehicleTraffic:

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from solomove import (BucketAggregator, CoreClient, CoreError, NullReporter, Outbox,  # noqa: E402
                      Settings, TrafficReporter, coco_to_vehicle_type)
from solomove.outbox import KIND_TRAFFIC, KIND_VIOLATION  # noqa: E402
import mock_core  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9"
T0 = datetime(2026, 11, 23, 5, 52, 31, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now


class MockCoreMixin:
    def start_core(self, codes=("DEV-1",), api_key="k"):
        self.server, self.state = mock_core.serve(0, api_key, list(codes))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop_core(self):
        self.server.shutdown()
        self.server.server_close()


class TestVehicleTypes(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(coco_to_vehicle_type(3), "RODA_DUA")
        self.assertEqual(coco_to_vehicle_type(2), "RODA_EMPAT")
        self.assertEqual(coco_to_vehicle_type(5), "TRUK_BUS")
        self.assertEqual(coco_to_vehicle_type(7), "TRUK_BUS")
        self.assertIsNone(coco_to_vehicle_type(0))


class TestSettings(unittest.TestCase):
    def test_missing_env(self):
        with self.assertRaises(ValueError):
            Settings.from_env({})

    def test_env_and_fallback_code(self):
        s = Settings.from_env({"SOLOMOVE_CORE_URL": "http://x/", "SOLOMOVE_API_KEY": "k"}, cctv_code="DEV-1")
        self.assertEqual((s.core_url, s.cctv_code, s.bucket_minutes), ("http://x", "DEV-1", 5))

    def test_from_env_without_config_is_null(self):
        old = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SOLOMOVE_")}
        try:
            self.assertIsInstance(TrafficReporter.from_env(cctv_code="DEV-1"), NullReporter)
        finally:
            os.environ.update(old)


class TestAggregator(unittest.TestCase):
    def test_bucket_floor_utc(self):
        agg = BucketAggregator(5)
        wib = timezone(timedelta(hours=7))
        self.assertEqual(agg.bucket_start(datetime(2026, 11, 23, 12, 52, 31, tzinfo=wib)),
                         datetime(2026, 11, 23, 5, 50, tzinfo=timezone.utc))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            BucketAggregator(5).add("DEV-1", "RODA_DUA", at=datetime(2026, 1, 1))

    def test_cumulative_and_dirty(self):
        agg = BucketAggregator(5)
        agg.add("DEV-1", "RODA_DUA", at=T0)
        agg.add("DEV-1", "RODA_DUA", at=T0, speed_kmh=20)
        agg.add("DEV-1", "RODA_DUA", at=T0, speed_kmh=30)
        agg.add("DEV-1", "TRUK_BUS", at=T0)
        items = agg.drain(now=T0)
        self.assertEqual(len(items), 2)
        roda2 = next(i for i in items if i["vehicleType"] == "RODA_DUA")
        self.assertEqual(roda2, {"cctvCode": "DEV-1", "bucketStart": "2026-11-23T05:50:00.000Z",
                                 "bucketMinutes": 5, "vehicleType": "RODA_DUA", "count": 3, "avgSpeedKmh": 25.0})
        self.assertNotIn("avgSpeedKmh", next(i for i in items if i["vehicleType"] == "TRUK_BUS"))
        # tidak ada perubahan → drain kosong
        self.assertEqual(agg.drain(now=T0), [])
        # tambah 1 lagi → dikirim nilai kumulatif (4), bukan selisih
        agg.add("DEV-1", "RODA_DUA", at=T0)
        self.assertEqual(agg.drain(now=T0)[0]["count"], 4)

    def test_closed_bucket_pruned(self):
        agg = BucketAggregator(5)
        agg.add("DEV-1", "RODA_DUA", at=T0)
        agg.drain(now=T0)
        self.assertEqual(agg.pending_keys(), 1)
        agg.drain(now=T0 + timedelta(minutes=5))
        self.assertEqual(agg.pending_keys(), 0)

    def test_invalid_type(self):
        with self.assertRaises(ValueError):
            BucketAggregator(5).add("DEV-1", "SEPEDA", at=T0)


class TestOutbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.box = Outbox(os.path.join(self.tmp.name, "o.sqlite3"), clock=self.clock)

    def tearDown(self):
        self.box.close()
        self.tmp.cleanup()

    def item(self, count):
        return {"cctvCode": "DEV-1", "bucketStart": "2026-11-23T05:50:00.000Z", "bucketMinutes": 5,
                "vehicleType": "RODA_DUA", "count": count}

    def test_upsert_keeps_latest_and_version_guard(self):
        self.box.upsert_traffic([self.item(3)])
        rows = self.box.due(KIND_TRAFFIC, 10)
        self.assertEqual(rows[0].payload["count"], 3)
        self.box.upsert_traffic([self.item(5)])          # nilai baru datang saat sedang dikirim
        self.box.mark_sent(rows)                          # konfirmasi versi lama tidak menghapus versi baru
        rows2 = self.box.due(KIND_TRAFFIC, 10)
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0].payload["count"], 5)
        self.box.mark_sent(rows2)
        self.assertEqual(self.box.due(KIND_TRAFFIC, 10), [])

    def test_retry_backoff_and_dead(self):
        self.box.upsert_traffic([self.item(1)])
        rows = self.box.due(KIND_TRAFFIC, 10)
        self.box.mark_retry(rows, "HTTP 503")
        self.assertEqual(self.box.due(KIND_TRAFFIC, 10), [])     # menunggu backoff
        self.clock.now += 3
        rows = self.box.due(KIND_TRAFFIC, 10)
        self.assertEqual(rows[0].attempts, 1)
        self.box.mark_dead(rows, "HTTP 422")
        self.clock.now += 1000
        self.assertEqual(self.box.due(KIND_TRAFFIC, 10), [])
        self.assertEqual(self.box.counts(), {"traffic_dead": 1})

    def test_violation_dedupe(self):
        meta = {"externalId": "x-1"}
        self.assertTrue(self.box.add_violation(meta, JPEG))
        self.assertFalse(self.box.add_violation(meta, JPEG))
        rows = self.box.due(KIND_VIOLATION, 10)
        self.assertEqual(rows[0].blob, JPEG)

    def test_prune(self):
        self.box.upsert_traffic([self.item(1)])
        self.clock.now += 8 * 24 * 3600
        self.assertEqual(self.box.prune(), 1)


class TestCoreClient(MockCoreMixin, unittest.TestCase):
    def setUp(self):
        self.start_core()

    def tearDown(self):
        self.stop_core()

    def test_get_cctvs_unwraps_envelope(self):
        cctvs = CoreClient(self.url, "k").get_cctvs()
        self.assertEqual(cctvs[0]["code"], "DEV-1")

    def test_bad_key_is_retryable(self):
        with self.assertRaises(CoreError) as ctx:
            CoreClient(self.url, "salah").get_cctvs()
        self.assertEqual(ctx.exception.status, 401)
        self.assertTrue(ctx.exception.retryable)

    def test_validation_error_not_retryable(self):
        with self.assertRaises(CoreError) as ctx:
            CoreClient(self.url, "k").post_traffic_counts([])
        self.assertEqual(ctx.exception.status, 422)
        self.assertFalse(ctx.exception.retryable)

    def test_network_error_retryable(self):
        with self.assertRaises(CoreError) as ctx:
            CoreClient("http://127.0.0.1:9", "k", timeout=1).get_cctvs()
        self.assertIsNone(ctx.exception.status)
        self.assertTrue(ctx.exception.retryable)

    def test_violation_multipart(self):
        meta = {"externalId": "DEV-1-a", "cctvCode": "DEV-1", "type": "CONTRAFLOW", "vehicleType": "RODA_DUA",
                "occurredAt": "2026-11-23T05:51:12.000Z", "plateNumber": None, "description": "uji", "confidence": 0.9}
        client = CoreClient(self.url, "k")
        self.assertEqual(client.post_violation(meta, JPEG)["duplicate"], False)
        self.assertEqual(client.post_violation(meta, JPEG)["duplicate"], True)
        self.assertEqual(self.state.violations["DEV-1-a"]["imageBytes"], len(JPEG))


class TestReporterEndToEnd(MockCoreMixin, unittest.TestCase):
    def setUp(self):
        self.start_core()
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()

    def tearDown(self):
        self.stop_core()
        self.tmp.cleanup()

    def make(self, url=None, code="DEV-1"):
        settings = Settings(core_url=url or self.url, api_key="k", cctv_code=code,
                            outbox_path=os.path.join(self.tmp.name, "o.sqlite3"))
        return TrafficReporter(settings, clock=self.clock)

    def test_counts_heartbeat_violation(self):
        rep = self.make()
        for cls in (3, 3, 2, 7, 0):                       # 0 (person) diabaikan
            rep.record_coco(cls)
        ext = rep.report_violation("RODA_DUA", JPEG, description="Lawan arah", confidence=0.87)
        rep.tick()
        snap = self.state.summary()
        counts = {t["vehicleType"]: t["count"] for t in snap["traffic"]}
        self.assertEqual(counts, {"RODA_DUA": 2, "RODA_EMPAT": 1, "TRUK_BUS": 1})
        self.assertIn("DEV-1", snap["heartbeats"])
        self.assertIn(ext, self.state.violations)
        self.assertEqual(rep.outbox.counts(), {})
        # kendaraan baru di bucket yang sama → nilai kumulatif menimpa di Core
        rep.record_coco(3)
        self.clock.now += 31
        rep.tick()
        counts = {t["vehicleType"]: t["count"] for t in self.state.summary()["traffic"]}
        self.assertEqual(counts["RODA_DUA"], 3)
        self.assertEqual(len(self.state.summary()["traffic"]), 3)   # tetap 3 baris (upsert)
        rep.stop(flush=False)

    def test_core_down_then_recovers(self):
        port = self.server.server_address[1]
        self.stop_core()                                   # Core mati
        rep = self.make(url=f"http://127.0.0.1:{port}")
        rep.client.timeout = 1
        rep.record_coco(3)
        rep.report_violation("RODA_EMPAT", JPEG)
        rep.tick()
        self.assertEqual(rep.outbox.counts(), {"traffic": 1, "violation": 1})   # tertahan di outbox
        # Core hidup lagi di port yang sama
        self.server, self.state = mock_core.serve(port, "k", ["DEV-1"])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.clock.now += 600                              # lewati backoff
        rep.tick()
        self.assertEqual(rep.outbox.counts(), {})
        self.assertEqual(len(self.state.violations), 1)
        self.assertEqual(self.state.summary()["traffic"][0]["count"], 1)
        rep.stop(flush=False)

    def test_unknown_cctv_rejected_not_retried(self):
        rep = self.make(code="TIDAK-ADA")
        rep.record_coco(3)
        rep.tick()
        # Core membalas 200 dengan rejected → baris tidak diulang terus
        self.assertEqual(rep.outbox.counts(), {})
        self.assertEqual(self.state.summary()["trafficRows"], 0)
        rep.stop(flush=False)


if __name__ == "__main__":
    unittest.main()
