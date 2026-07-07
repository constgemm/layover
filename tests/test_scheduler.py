#!/usr/bin/env python3
"""
Unit tests for the Layover scheduler — both scheduling modes.

Covers the feature/continuous-scan checklist: interval mode picks the next slot,
env parsing is robust, and weekly mode is unchanged.
Run:  python3 -m unittest discover -s tests   (from the repo root)
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler  # noqa: E402


class TestIntervalMode(unittest.TestCase):
    def test_next_interval_run_adds_interval(self):
        now = datetime(2026, 7, 7, 17, 16, 0)
        self.assertEqual(scheduler.next_interval_run(now, 30),
                         datetime(2026, 7, 7, 17, 46, 0))

    def test_interval_is_strictly_forward(self):
        now = datetime(2026, 7, 7, 17, 16, 0)
        self.assertGreater(scheduler.next_interval_run(now, 5), now)

    def test_interval_floor_one_minute(self):
        now = datetime(2026, 7, 7, 17, 16, 0)
        for bad in (0, -10):
            self.assertEqual(scheduler.next_interval_run(now, bad),
                             datetime(2026, 7, 7, 17, 17, 0))

    def test_crosses_midnight(self):
        now = datetime(2026, 7, 7, 23, 50, 0)
        self.assertEqual(scheduler.next_interval_run(now, 30),
                         datetime(2026, 7, 8, 0, 20, 0))


class TestWeeklyUnchanged(unittest.TestCase):
    def test_next_run_forward_to_monday(self):
        # Wed 2026-07-08 09:00 -> Mon 2026-07-13 07:00
        self.assertEqual(scheduler.next_run(datetime(2026, 7, 8, 9, 0), 0, 7, 0),
                         datetime(2026, 7, 13, 7, 0))

    def test_next_run_same_day_before_slot(self):
        self.assertEqual(scheduler.next_run(datetime(2026, 7, 13, 6, 0), 0, 7, 0),
                         datetime(2026, 7, 13, 7, 0))

    def test_next_run_on_slot_rolls_a_week(self):
        self.assertEqual(scheduler.next_run(datetime(2026, 7, 13, 7, 0), 0, 7, 0),
                         datetime(2026, 7, 20, 7, 0))


class TestEnvParsing(unittest.TestCase):
    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_env_int(self):
        os.environ["X"] = "45"
        self.assertEqual(scheduler.env_int("X", 30), 45)
        os.environ["X"] = "not-a-number"
        self.assertEqual(scheduler.env_int("X", 30), 30)      # falls back
        self.assertEqual(scheduler.env_int("MISSING", 30), 30)

    def test_env_bool(self):
        for v in ("true", "1", "yes", "on", "TRUE", "On"):
            os.environ["B"] = v
            self.assertTrue(scheduler.env_bool("B", False))
        for v in ("false", "0", "no", ""):
            os.environ["B"] = v
            self.assertFalse(scheduler.env_bool("B", True))
        self.assertTrue(scheduler.env_bool("MISSING", True))   # default honoured


if __name__ == "__main__":
    unittest.main()
