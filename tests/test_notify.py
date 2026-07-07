#!/usr/bin/env python3
"""
Unit tests for notify.py — the optional webhook / notification sink.

Covers the feature/notify-webhook checklist: fires only for `new` candidates,
no-op when nothing new or no sink, and the payload shape. No real network — the
webhook transport is monkeypatched.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notify  # noqa: E402


def cand(status, fn="LX1246", **kw):
    base = {"status": status, "flightNumber": fn, "date": "2022-03-17",
            "from": "LSZH", "to": "ESSA", "from_iata": "ZRH", "to_iata": "ARN",
            "departure": "2022-03-17T08:00", "airline": "SWR",
            "seatNumber": None, "confidence": "high", "issues": []}
    base.update(kw)
    return base


class TestBuildNotification(unittest.TestCase):
    def test_counts_and_new_only(self):
        cands = [cand("new"), cand("new", "LX1251", seatNumber="12A"),
                 cand("duplicate", "LX999"), cand("uncertain", "BA1")]
        p = notify.build_notification(cands)
        self.assertEqual(p["counts"], {"new": 2, "uncertain": 1, "duplicate": 1})
        self.assertEqual(len(p["new"]), 2)                      # new only
        self.assertTrue(all(x["flightNumber"].startswith("LX12") for x in p["new"]))
        self.assertIn("2 new flight(s), 1 uncertain", p["summary"])
        self.assertIn("LX1251", p["text"])
        self.assertIn("seat 12A", p["text"])

    def test_json_serialisable(self):
        p = notify.build_notification([cand("new")])
        json.dumps(p)  # must not raise


class TestNotifyGating(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._orig = notify.send_webhook
        notify.send_webhook = lambda url, payload, timeout=15: (
            self.sent.append((url, payload)) or (200, "ok"))

    def tearDown(self):
        notify.send_webhook = self._orig

    def test_noop_when_no_new(self):
        fired = notify.notify_new([cand("duplicate"), cand("uncertain")],
                                  webhook_url="https://x/y")
        self.assertFalse(fired)
        self.assertEqual(self.sent, [])

    def test_noop_when_no_sink(self):
        fired = notify.notify_new([cand("new")])       # new, but no url/cmd
        self.assertFalse(fired)
        self.assertEqual(self.sent, [])

    def test_fires_for_new(self):
        fired = notify.notify_new([cand("new")], webhook_url="https://x/y")
        self.assertTrue(fired)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "https://x/y")
        self.assertEqual(self.sent[0][1]["counts"]["new"], 1)

    def test_webhook_failure_is_swallowed(self):
        def boom(url, payload, timeout=15):
            raise RuntimeError("network down")
        notify.send_webhook = boom
        # must not raise even though the sink fails
        notify.notify_new([cand("new")], webhook_url="https://x/y")


class TestSendWebhookRequest(unittest.TestCase):
    def test_builds_post_json(self):
        captured = {}

        class FakeResp:
            status = 204
            def read(self): return b""
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=15):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["ctype"] = req.headers.get("Content-type")
            captured["body"] = req.data
            return FakeResp()

        orig = notify.urllib.request.urlopen
        notify.urllib.request.urlopen = fake_urlopen
        try:
            status, _ = notify.send_webhook("https://hook/x", {"a": 1})
        finally:
            notify.urllib.request.urlopen = orig
        self.assertEqual(status, 204)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["ctype"], "application/json")
        self.assertEqual(json.loads(captured["body"]), {"a": 1})


if __name__ == "__main__":
    unittest.main()
