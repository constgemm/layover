#!/usr/bin/env python3
"""
Unit tests for populate.py orchestration logic — classify, rebooking detection,
and the auto-write safety filter (feature/auto-write checklist: only high +
non-rebooking NEW flights are writable; dry-run is the default).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import airtrail  # noqa: E402
import populate  # noqa: E402


def cand(fn, date, conf="high", issues=None, **kw):
    base = {"flightNumber": fn, "date": date, "from": "LSZH", "to": "ESSA",
            "departure": f"{date}T08:00", "confidence": conf,
            "issues": issues or []}
    base.update(kw)
    return base


class TestClassify(unittest.TestCase):
    def test_new_duplicate_uncertain_unverified(self):
        existing = {airtrail.flight_key("LX1246", "2022-03-17")}
        cands = [
            cand("LX1246", "2022-03-17"),                 # in AirTrail -> duplicate
            cand("LX1251", "2022-03-21"),                 # not present -> new
            cand("BA1", "2020-01-01", conf="uncertain"),  # low conf -> uncertain
        ]
        populate.classify(cands, existing)
        self.assertEqual([c["status"] for c in cands],
                         ["duplicate", "new", "uncertain"])

    def test_unverified_when_airtrail_absent(self):
        cands = [cand("LX1251", "2022-03-21")]
        populate.classify(cands, None)
        self.assertEqual(cands[0]["status"], "unverified")


class TestRebookingFlag(unittest.TestCase):
    def test_same_route_date_different_number_flagged(self):
        cands = [cand("LX1025", "2024-06-30"), cand("LX1027", "2024-06-30")]
        warnings = populate.find_rebookings(cands)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(any("rebooking" in i for i in cands[0]["issues"]))


class TestAutoWriteSafety(unittest.TestCase):
    def test_only_high_new_nonrebooking_writable(self):
        good = cand("LX1251", "2022-03-21"); good["status"] = "new"
        low = cand("BA1", "2020-01-01", conf="uncertain"); low["status"] = "uncertain"
        dup = cand("LX9", "2020-02-02"); dup["status"] = "duplicate"
        rebook = cand("LX1025", "2024-06-30",
                      issues=["same route+date as LX1027 — possible rebooking"])
        rebook["status"] = "new"
        writable = populate.auto_writable([good, low, dup, rebook])
        self.assertEqual([c["flightNumber"] for c in writable], ["LX1251"])

    def test_blocked_reason_flags_rebooking_and_confidence(self):
        rebook = cand("LX1025", "2024-06-30",
                      issues=["possible rebooking/cancellation"])
        self.assertIn("rebooking", populate._blocked_reason(rebook).lower())
        low = cand("BA1", "2020-01-01", conf="uncertain")
        self.assertEqual(populate._blocked_reason(low), "not high-confidence")
        ok = cand("LX1251", "2022-03-21")
        self.assertIsNone(populate._blocked_reason(ok))


if __name__ == "__main__":
    unittest.main()
