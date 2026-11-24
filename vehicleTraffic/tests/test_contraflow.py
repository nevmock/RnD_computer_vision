"""Tes deteksi lawan arah (tanpa GPU / video). Jalankan dari folder vehicleTraffic:

    python -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from solomove.contraflow import (ContraflowConfig, ContraflowDetector, Lane,  # noqa: E402
                                 lanes_from_zones, parse_direction, point_in_polygon)

LEFT = [[0, 0], [400, 0], [400, 720], [0, 720]]       # jalur kiri, harus naik
RIGHT = [[800, 0], [1280, 0], [1280, 720], [800, 720]]  # jalur kanan, harus turun


def run(detector, track_id, points, start_frame=0):
    """Umpankan lintasan, kembalikan daftar event."""
    events = []
    for i, p in enumerate(points):
        ev = detector.update(track_id, p, start_frame + i)
        if ev:
            events.append((start_frame + i, ev))
    return events


def line(x0, y0, dx, dy, n):
    return [(x0 + dx * i, y0 + dy * i) for i in range(n)]


class TestParsing(unittest.TestCase):
    def test_legacy_zones_default_directions(self):
        lanes = {l.name: l for l in lanes_from_zones({"top": LEFT, "left": LEFT, "right": RIGHT})}
        self.assertEqual(set(lanes), {"left", "right"})
        self.assertEqual(lanes["left"].direction, (0.0, -1.0))
        self.assertEqual(lanes["right"].direction, (0.0, 1.0))
        self.assertEqual(lanes["left"].label, "kiri")

    def test_legacy_direction_override_arrow(self):
        lanes = lanes_from_zones({"left": LEFT, "left_direction": [[100, 600], [400, 200]]})
        dx, dy = lanes[0].direction
        self.assertAlmostEqual(dx, 0.6)
        self.assertAlmostEqual(dy, -0.8)

    def test_lanes_format_takes_priority(self):
        lanes = lanes_from_zones({
            "left": LEFT,
            "lanes": [{"name": "utara", "zone": RIGHT, "direction": [1, 0]}],
        })
        self.assertEqual([l.name for l in lanes], ["utara"])
        self.assertEqual(lanes[0].direction, (1.0, 0.0))

    def test_zone_with_less_than_3_points_ignored(self):
        self.assertEqual(lanes_from_zones({"left": [[0, 0], [1, 1]], "right": []}), [])

    def test_parse_direction_formats(self):
        self.assertEqual(parse_direction("UP"), (0.0, -1.0))
        self.assertEqual(parse_direction([0, 5]), (0.0, 1.0))
        self.assertEqual(parse_direction({"from": [0, 0], "to": [-3, 0]}), (-1.0, 0.0))
        with self.assertRaises(ValueError):
            parse_direction([0, 0])
        with self.assertRaises(ValueError):
            parse_direction("serong")

    def test_point_in_polygon(self):
        tri = [(0, 0), (10, 0), (0, 10)]
        self.assertTrue(point_in_polygon(1, 1, tri))
        self.assertTrue(point_in_polygon(5, 0, tri))      # di tepi
        self.assertFalse(point_in_polygon(9, 9, tri))
        self.assertFalse(point_in_polygon(1, 1, tri[:2]))

    def test_from_zones_config_override(self):
        det = ContraflowDetector.from_zones({"left": LEFT, "contraflow": {"min_distance_px": 80, "foo": 1}})
        self.assertEqual(det.config.min_distance_px, 80.0)
        self.assertEqual(len(det.lanes), 1)


class TestDetection(unittest.TestCase):
    def setUp(self):
        self.det = ContraflowDetector.from_zones({"left": LEFT, "right": RIGHT})

    def test_correct_direction_no_event(self):
        self.assertEqual(run(self.det, 1, line(200, 700, 0, -6, 100)), [])   # kiri naik
        self.assertEqual(run(self.det, 2, line(1000, 10, 0, 6, 100)), [])    # kanan turun

    def test_wrong_direction_single_event(self):
        events = run(self.det, 1, line(200, 100, 0, 6, 80))                  # kiri malah turun
        self.assertEqual(len(events), 1)
        frame, ev = events[0]
        self.assertEqual(ev.lane.name, "left")
        self.assertEqual(ev.description, "Melaju berlawanan arah di jalur kiri")
        self.assertGreaterEqual(ev.distance_px, self.det.config.min_distance_px)
        self.assertGreaterEqual(ev.angle_deg, 170)
        # jarak 40 px dicapai di update ke-7 (6 px/frame), lalu harus bertahan 5 update
        self.assertEqual(frame, 11)
        self.assertTrue(self.det.is_flagged(1))
        self.assertEqual(self.det.total, 1)

    def test_turning_across_lane_no_event(self):
        # gerak menyamping (belok) = 90° dari arah seharusnya → bukan pelanggaran
        self.assertEqual(run(self.det, 1, line(10, 400, 6, 0, 60)), [])
        # belok serong 120° (masih di bawah 135°) → bukan pelanggaran
        self.assertEqual(run(self.det, 2, line(10, 300, 5.2, 3, 70)), [])

    def test_jitter_stationary_no_event(self):
        rnd = random.Random(7)
        pts = [(200 + rnd.uniform(-4, 4), 400 + rnd.uniform(-4, 4)) for _ in range(300)]
        self.assertEqual(run(self.det, 1, pts), [])

    def test_short_backward_roll_no_event(self):
        # antre lalu mundur sedikit (30 px) → di bawah jarak minimum
        pts = [(200, 400)] * 20 + line(200, 400, 0, 3, 10) + [(200, 430)] * 20
        self.assertEqual(run(self.det, 1, pts), [])

    def test_wrong_then_reverse_within_streak_no_event(self):
        # turun 42 px lalu langsung kembali naik → kondisi salah arah tidak bertahan 5 update
        pts = line(200, 300, 0, 6, 8) + line(200, 342, 0, -6, 20)
        self.assertEqual(run(self.det, 1, pts), [])

    def test_leaving_lane_resets_entry(self):
        # 30 px salah arah di jalur kiri, keluar zona, masuk lagi 30 px → tidak dijumlah
        pts = line(390, 100, 0, 6, 6) + [(600, 200)] * 3 + line(390, 300, 0, 6, 6)
        self.assertEqual(run(self.det, 1, pts), [])

    def test_lane_switch_uses_new_lane_direction(self):
        # turun di jalur kanan (benar), lalu pindah ke jalur kiri dan tetap turun (salah)
        pts = line(1000, 100, 0, 6, 20) + line(200, 220, 0, 6, 30)
        events = run(self.det, 1, pts)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1].lane.name, "left")

    def test_diagonal_lane(self):
        lane = Lane("serong", ((300, 720), (700, 720), (1100, 0), (800, 0)),
                    parse_direction([[500, 700], [950, 50]]), "serong")
        det = ContraflowDetector([lane])
        self.assertEqual(run(det, 1, line(520, 680, 4.5, -6.5, 80)), [])        # searah jalan
        self.assertEqual(len(run(det, 2, line(930, 80, -4.5, 6.5, 80))), 1)     # melawan
        # "turun lurus" di layar = 145° dari arah serong → dianggap melawan
        self.assertEqual(len(run(det, 3, line(900, 150, 0, 6, 60))), 1)

    def test_independent_tracks(self):
        self.assertEqual(len(run(self.det, 1, line(100, 100, 0, 6, 40))), 1)
        self.assertEqual(len(run(self.det, 2, line(300, 100, 0, 6, 40))), 1)
        self.assertEqual(self.det.total, 2)

    def test_prune_stale_tracks(self):
        run(self.det, 1, line(200, 600, 0, -6, 10))            # track normal, frame 0..9
        run(self.det, 2, line(200, 100, 0, 6, 40))             # track pelanggar, frame 0..39
        self.assertEqual(self.det.tracked(), 2)
        self.assertEqual(self.det.prune(100), 1)               # track 1 hilang > 90 frame (ttl)
        self.assertEqual(self.det.prune(200), 1)               # flag track 2 juga dibuang
        self.assertEqual(self.det.tracked(), 0)
        self.assertFalse(self.det.is_flagged(2))

    def test_custom_config(self):
        det = ContraflowDetector.from_zones({"left": LEFT}, ContraflowConfig(min_distance_px=100, min_frames=1))
        self.assertEqual(run(det, 1, line(200, 100, 0, 6, 16)), [])            # 90 px
        self.assertEqual(len(run(det, 2, line(200, 100, 0, 6, 18))), 1)        # 102 px


if __name__ == "__main__":
    unittest.main()
