#!/usr/bin/env python3
"""
Unit tests for dawarich.py — Phase 3 location-history validation.

No network: point-fetching is injected. The contract under test is the safety
logic — confirm on proximity, contradict ONLY when tracking was active and clearly
elsewhere, and treat any absence of data as "unknown" (never a false alarm).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import airdata      # noqa: E402
import dawarich     # noqa: E402


def cand(**kw):
    base = {"flightNumber": "LX1246", "date": "2022-03-17",
            "from": "LSZH", "to": "ESSA", "from_iata": "ZRH", "to_iata": "ARN",
            "issues": []}
    base.update(kw)
    return base


ZRH = airdata.airport_latlon("ZRH")     # (47.46, 8.55)
ARN = airdata.airport_latlon("ARN")     # (59.65, 17.92)


class TestGeo(unittest.TestCase):
    def test_haversine_known_distance(self):
        # ZRH -> ARN great circle is ~1490 km; allow a generous tolerance
        self.assertAlmostEqual(dawarich.haversine_km(ZRH, ARN), 1490, delta=60)

    def test_coords_lookup_by_icao(self):
        self.assertEqual(airdata.airport_latlon("LSZH"), ZRH)


class TestClassifyLocation(unittest.TestCase):
    def test_confirmed_near_destination(self):
        pts = [(59.60, 17.90)]                     # right by ARN
        self.assertEqual(dawarich.classify_location(cand(), pts), "confirmed")

    def test_confirmed_near_origin(self):
        pts = [(47.40, 8.60)]                      # by ZRH
        self.assertEqual(dawarich.classify_location(cand(), pts), "confirmed")

    def test_contradicted_when_active_but_far(self):
        pts = [(40.0, -3.5)] * 6                   # Madrid, plenty of points
        self.assertEqual(dawarich.classify_location(cand(), pts), "contradicted")

    def test_unknown_when_few_points(self):
        pts = [(40.0, -3.5)]                        # far but only one point
        self.assertEqual(dawarich.classify_location(cand(), pts), "unknown")

    def test_unknown_when_no_coords(self):
        # airport not in the coordinate table -> can't tell -> unknown
        c = cand(**{"from": None, "to": None, "from_iata": "XXX", "to_iata": "YYY"})
        self.assertEqual(dawarich.classify_location(c, [(0, 0)] * 10), "unknown")


class TestValidate(unittest.TestCase):
    def test_contradiction_adds_issue(self):
        cands = [cand()]
        dawarich.validate(cands, url="http://d", api_key="k",
                          fetch=lambda u, k, s, e: [(40.0, -3.5)] * 6)
        self.assertEqual(cands[0]["location"], "contradicted")
        self.assertTrue(any("rebooking" in i.lower() for i in cands[0]["issues"]))

    def test_confirmed_no_issue(self):
        cands = [cand()]
        dawarich.validate(cands, url="http://d", api_key="k",
                          fetch=lambda u, k, s, e: [(59.6, 17.9)])
        self.assertEqual(cands[0]["location"], "confirmed")
        self.assertEqual(cands[0]["issues"], [])

    def test_no_config_is_unknown(self):
        cands = [cand()]
        dawarich.validate(cands, url=None, api_key=None)
        self.assertEqual(cands[0]["location"], "unknown")

    def test_fetch_failure_is_swallowed(self):
        def boom(u, k, s, e):
            raise RuntimeError("dawarich down")
        cands = [cand()]
        dawarich.validate(cands, url="http://d", api_key="k", fetch=boom)
        self.assertEqual(cands[0]["location"], "unknown")   # must not raise

    def test_bad_date_is_unknown(self):
        cands = [cand(date=None)]
        dawarich.validate(cands, url="http://d", api_key="k",
                          fetch=lambda *a: [(59.6, 17.9)])
        self.assertEqual(cands[0]["location"], "unknown")


class TestCoordParsing(unittest.TestCase):
    def test_flat_latlon(self):
        p = dawarich._coords([{"latitude": 47.4, "longitude": 8.5}])
        self.assertEqual(p, [(47.4, 8.5)])

    def test_wrapped_and_geojson(self):
        payload = {"data": [
            {"lat": 59.6, "lng": 17.9},
            {"geometry": {"coordinates": [8.5, 47.4]}},   # GeoJSON lon,lat
            {"latitude": None, "longitude": None},        # dropped
        ]}
        self.assertEqual(dawarich._coords(payload), [(59.6, 17.9), (47.4, 8.5)])


class TestEnabledGating(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("DAWARICH_VALIDATE", "DAWARICH_URL", "DAWARICH_API_KEY")}

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_off_by_default(self):
        for k in ("DAWARICH_VALIDATE", "DAWARICH_URL", "DAWARICH_API_KEY"):
            os.environ.pop(k, None)
        self.assertFalse(dawarich.enabled())

    def test_on_when_configured(self):
        os.environ.update({"DAWARICH_VALIDATE": "1", "DAWARICH_URL": "http://d",
                           "DAWARICH_API_KEY": "k"})
        self.assertTrue(dawarich.enabled())


if __name__ == "__main__":
    unittest.main()
