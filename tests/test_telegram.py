#!/usr/bin/env python3
"""
Unit tests for telegram.py — Phase 4 interactive approval.

No network: the Telegram Bot API is a scripted fake injected as `api`, and AirTrail
post/fetch are injected too. Contract under test: cards render every field (missing
flagged), Approve writes + confirms with the new count, Skip/timeout write nothing,
and only eligible (new, not Dawarich-contradicted) flights are ever offered.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram    # noqa: E402


def cand(**kw):
    base = {"status": "new", "location": "confirmed", "flightNumber": "LX1246",
            "airline": "SWR", "airline_iata": "LX", "from": "LSZH", "to": "ESSA",
            "from_iata": "ZRH", "to_iata": "ARN", "date": "2022-03-17",
            "departure": "2022-03-17T08:00", "arrival": None, "seatNumber": "12A",
            "seat": "window", "seatClass": None, "source_file": "acct/x.txt",
            "extractor": "jsonld", "issues": []}
    base.update(kw)
    return base


class FakeAPI:
    """Scripted Telegram transport. `update_batches` are returned by successive
    getUpdates calls (after the first priming call)."""

    def __init__(self, update_batches=None):
        self.sent = []
        self.answered = []
        self._batches = list(update_batches or [])
        self._primed = False

    def __call__(self, token, method, params=None, timeout=35):
        if method == "sendMessage":
            self.sent.append(params)
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        if method == "getUpdates":
            if not self._primed:            # first call = prime (drain)
                self._primed = True
                return {"result": []}
            batch = self._batches.pop(0) if self._batches else []
            return {"result": batch}
        if method == "answerCallbackQuery":
            self.answered.append(params)
            return {"ok": True}
        return {"ok": True, "result": {}}


def cb(update_id, idx, verb="ok"):
    return {"update_id": update_id,
            "callback_query": {"id": f"q{update_id}",
                               "data": f"{telegram.PREFIX}:{verb}:{idx}"}}


class TestRender(unittest.TestCase):
    def test_missing_fields_flagged(self):
        text = telegram.format_candidate(cand(arrival=None, seatClass=None))
        self.assertIn("Arrival:    ⚠ missing", text)
        self.assertIn("Class:      ⚠ missing", text)
        self.assertIn("LX1246", text)
        self.assertIn("ZRH", text)          # from_iata shown alongside ICAO

    def test_keyboard_data(self):
        kb = telegram.approval_keyboard(3)
        row = kb["inline_keyboard"][0]
        self.assertEqual(row[0]["callback_data"], "lay:ok:3")
        self.assertEqual(row[1]["callback_data"], "lay:no:3")


class TestEligible(unittest.TestCase):
    def test_filters_to_new_non_contradicted(self):
        cands = [cand(), cand(status="duplicate"), cand(status="uncertain"),
                 cand(location="contradicted")]
        elig = telegram.eligible(cands)
        self.assertEqual(len(elig), 1)
        self.assertEqual(elig[0]["status"], "new")


class TestRunApprovals(unittest.TestCase):
    def _run(self, batches, **kw):
        api = FakeAPI(batches)
        writes = []
        post = kw.get("post", lambda u, k, p: (writes.append(p) or (200, "ok")))
        fetch = kw.get("fetch", lambda u, k: [0] * 42)
        n = telegram.run_approvals(
            [cand()], "http://at", "key", "TOKEN", "CHAT",
            timeout_s=5, poll=1, api=api, post_flight=post, fetch_flights=fetch,
            build_payload=lambda c, user_id=None: {"flightNumber": c["flightNumber"]})
        return n, api, writes

    def test_approve_writes_and_confirms_count(self):
        n, api, writes = self._run([[cb(10, 0, "ok")]])
        self.assertEqual(n, 1)
        self.assertEqual(len(writes), 1)
        # a confirmation mentioning the new total was sent
        self.assertTrue(any("42 flights" in s.get("text", "") for s in api.sent))
        self.assertTrue(api.answered)       # the tap was acknowledged

    def test_skip_writes_nothing(self):
        n, api, writes = self._run([[cb(11, 0, "no")]])
        self.assertEqual(n, 0)
        self.assertEqual(writes, [])
        self.assertTrue(any("Skipped" in s.get("text", "") for s in api.sent))

    def test_timeout_writes_nothing(self):
        n, api, writes = self._run([])      # no taps ever arrive
        self.assertEqual(n, 0)
        self.assertEqual(writes, [])
        self.assertTrue(any("No response" in s.get("text", "") for s in api.sent))

    def test_write_failure_reported_not_raised(self):
        def boom(u, k, p):
            raise RuntimeError("airtrail 500")
        n, api, _ = self._run([[cb(12, 0, "ok")]], post=boom)
        self.assertEqual(n, 0)
        self.assertTrue(any("Failed" in s.get("text", "") for s in api.sent))

    def test_nothing_eligible_is_noop(self):
        api = FakeAPI()
        n = telegram.run_approvals([cand(status="duplicate")], "u", "k", "T", "C",
                                   api=api)
        self.assertEqual(n, 0)
        self.assertEqual(api.sent, [])      # no messages at all


class TestEnabledGating(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("TELEGRAM_APPROVE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_off_by_default(self):
        for k in ("TELEGRAM_APPROVE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            os.environ.pop(k, None)
        self.assertFalse(telegram.enabled())

    def test_on_when_configured(self):
        os.environ.update({"TELEGRAM_APPROVE": "1", "TELEGRAM_BOT_TOKEN": "t",
                           "TELEGRAM_CHAT_ID": "c"})
        self.assertTrue(telegram.enabled())


if __name__ == "__main__":
    unittest.main()
