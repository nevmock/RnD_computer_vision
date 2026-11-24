"""Tes editor zona (tanpa jendela / video). Jalankan dari folder vehicleTraffic:

    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    import cv2  # noqa: F401
    import numpy as np
    import customzone as cz
except ImportError as e:  # pragma: no cover
    cz = None
    SKIP = str(e)

from solomove.contraflow import lanes_from_zones  # noqa: E402

SQ = [[0, 0], [100, 0], [100, 100], [0, 100]]
SQ2 = [[200, 0], [300, 0], [300, 100], [200, 100]]


@unittest.skipIf(cz is None, "opencv/numpy belum terpasang")
class TestZonesFile(unittest.TestCase):
    def test_legacy_left_right_become_lanes(self):
        top, lanes, extra = cz.parse_zones({"top": SQ, "left": SQ, "right": SQ2,
                                            "left_direction": [[50, 90], [50, 10]], "contraflow": {"min_frames": 3}})
        self.assertEqual([l["name"] for l in lanes], ["kiri", "kanan"])
        self.assertEqual(lanes[0]["direction"], [(50, 90), (50, 10)])
        self.assertEqual(lanes[1]["direction"], [(250, 0), (250, 100)])     # default turun, dari titik tengah
        self.assertEqual(extra, {"contraflow": {"min_frames": 3}})
        self.assertEqual(len(top), 4)

    def test_lanes_format_and_direction_variants(self):
        _, lanes, _ = cz.parse_zones({"lanes": [
            {"name": "a", "zone": SQ, "direction": [[10, 90], [10, 10]]},
            {"name": "b", "zone": SQ2, "direction": "down"},
            {"name": "c", "zone": SQ2, "direction": "serong"},
        ]})
        self.assertEqual(lanes[0]["direction"], [(10, 90), (10, 10)])
        self.assertEqual(lanes[1]["direction"], [(250, 0), (250, 100)])
        self.assertIsNone(lanes[2]["direction"])

    def test_lanes_key_wins_over_legacy(self):
        _, lanes, extra = cz.parse_zones({"left": SQ, "lanes": []})
        self.assertEqual(lanes, [])
        self.assertEqual(extra, {})            # key lama dibuang saat disimpan ulang

    def test_lanes_disabled_keeps_other_keys(self):
        top, lanes, extra = cz.parse_zones({"top": SQ, "left": SQ}, lanes_enabled=False)
        self.assertEqual(lanes, [])
        self.assertEqual(extra, {"left": SQ})
        out = json.loads(cz.serialize(top, lanes, extra, lanes_enabled=False))
        self.assertNotIn("lanes", out)
        self.assertEqual(out["left"], SQ)

    def test_validate(self):
        lane = lambda n, pts, d=((0, 0), (0, 50)): {"name": n, "points": pts, "direction": d}
        self.assertEqual(cz.validate([], []), [])
        self.assertTrue(cz.validate([(0, 0), (1, 1)], []))                         # TOP 2 titik
        self.assertTrue(cz.validate([], [lane("a", [(0, 0), (1, 1)])]))            # lajur 2 titik
        self.assertTrue(cz.validate([], [lane("a", SQ, None)]))                    # tanpa panah
        self.assertTrue(cz.validate([], [lane("a", SQ), lane("a", SQ2)]))          # nama dobel
        self.assertEqual(cz.validate([], [lane("a", []), lane("a", SQ)]), [])      # lajur kosong diabaikan

    def test_serialize_roundtrip_with_detector(self):
        lanes = [{"name": "atas-keluar", "points": [tuple(p) for p in SQ], "direction": [(50, 90), (80, 10)]},
                 {"name": "kosong", "points": [], "direction": None},
                 {"name": "atas-masuk", "points": [tuple(p) for p in SQ2], "direction": [(250, 10), (240, 90)]}]
        text = cz.serialize([tuple(p) for p in SQ], lanes, {"contraflow": {"min_distance_px": 60}})
        data = json.loads(text)
        self.assertEqual([l["name"] for l in data["lanes"]], ["atas-keluar", "atas-masuk"])
        self.assertEqual(data["top"], SQ)
        self.assertEqual(data["contraflow"], {"min_distance_px": 60})
        parsed = lanes_from_zones(data)
        self.assertEqual([l.name for l in parsed], ["atas-keluar", "atas-masuk"])
        self.assertLess(parsed[0].direction[1], 0)       # naik
        self.assertGreater(parsed[1].direction[1], 0)    # turun
        # buka lagi di editor → hasil sama
        top2, lanes2, extra2 = cz.parse_zones(data)
        self.assertEqual(cz.serialize(top2, lanes2, extra2), text)

    def test_serialize_no_lanes(self):
        data = json.loads(cz.serialize([], [], {}))
        self.assertEqual(data, {"top": [], "lanes": []})
        self.assertEqual(lanes_from_zones(data), [])


@unittest.skipIf(cz is None, "opencv/numpy belum terpasang")
class TestEditorFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "zones.json")
        self.ed = cz.Editor("Bandung", self.path, True)

    def tearDown(self):
        self.tmp.cleanup()

    def keys(self, text):
        for ch in text:
            self.assertTrue(self.ed.on_key(ord(ch)))

    def click(self, *pts, right=False):
        ev = cv2.EVENT_RBUTTONDOWN if right else cv2.EVENT_LBUTTONDOWN
        for x, y in pts:
            self.ed.on_mouse(ev, x, y, 0, None)

    def test_add_lane_name_points_arrow_save(self):
        self.keys("n")
        self.keys("atas keluar")
        self.ed.on_key(13)
        self.click((0, 0), (100, 0), (100, 100), (0, 100), (5, 5))
        self.click((0, 0), right=True)                     # hapus titik ke-5
        self.keys("d")
        self.click((50, 90), (60, 10))
        self.ed.render(np.zeros((720, 1280, 3), np.uint8))  # tidak boleh error
        self.keys("s")
        data = json.load(open(self.path))
        self.assertEqual(data["lanes"][0]["name"], "atas-keluar")
        self.assertEqual(len(data["lanes"][0]["zone"]), 4)
        self.assertEqual(data["lanes"][0]["direction"], [[50, 90], [60, 10]])

    def test_save_refused_without_arrow(self):
        self.keys("n")
        self.ed.on_key(13)
        self.click((0, 0), (100, 0), (100, 100))
        self.keys("s")
        self.assertFalse(os.path.exists(self.path))
        self.assertIn("panah", self.ed.flash)

    def test_delete_lane_needs_confirmation(self):
        self.keys("n")
        self.ed.on_key(13)
        self.keys("h")
        self.keys("r")                                     # bukan H → batal
        self.assertEqual(len(self.ed.lanes), 1)
        self.keys("hh")
        self.assertEqual(self.ed.lanes, [])
        self.assertEqual(self.ed.active, 0)

    def test_select_and_escape(self):
        self.keys("nn")                                    # N kedua = huruf nama lajur-1
        self.ed.on_key(13)
        self.assertEqual(self.ed.lanes[0]["name"], "n")
        self.keys("1")
        self.assertEqual(self.ed.active, 0)
        self.keys("2")
        self.assertEqual(self.ed.active, 1)
        self.keys("3")                                     # lajur ke-2 belum ada
        self.assertEqual(self.ed.active, 1)
        self.keys("d")
        self.assertTrue(self.ed.on_key(27))                # ESC pertama: batal panah
        self.assertIsNone(self.ed.arrow_pts)
        self.assertFalse(self.ed.on_key(27))               # ESC kedua: keluar

    def test_top_only_location(self):
        ed = cz.Editor("Bali", self.path, False)
        ed.on_key(ord("n"))
        self.assertEqual(ed.lanes, [])
        ed.on_mouse(cv2.EVENT_LBUTTONDOWN, 1, 1, 0, None)
        self.assertEqual(ed.top, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
