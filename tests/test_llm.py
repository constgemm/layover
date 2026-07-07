#!/usr/bin/env python3
"""
Unit tests for llm.py — the Phase 2 local-LLM fallback parser.

No real network and no real model: llm.chat is monkeypatched to return canned
replies. The point of these tests is the *contract*, not model quality:
  * replies parse out of prose / code fences,
  * candidates carry LLM provenance and uncertain confidence (so they can never
    reach the unattended auto-write path),
  * the fallback stays off unless explicitly enabled.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm          # noqa: E402
import populate     # noqa: E402


class TestParseJsonFlights(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(llm.parse_json_flights('[{"a": 1}]'), [{"a": 1}])

    def test_code_fence(self):
        text = 'Sure!\n```json\n[{"flight_number": "1834"}]\n```\nHope that helps.'
        self.assertEqual(llm.parse_json_flights(text), [{"flight_number": "1834"}])

    def test_prose_wrapped_array(self):
        text = 'Here is the data: [{"x": 2}] and nothing else.'
        self.assertEqual(llm.parse_json_flights(text), [{"x": 2}])

    def test_lone_object_wrapped(self):
        self.assertEqual(llm.parse_json_flights('{"x": 1}'), [{"x": 1}])

    def test_garbage_is_empty(self):
        self.assertEqual(llm.parse_json_flights("no json here"), [])
        self.assertEqual(llm.parse_json_flights(""), [])


class TestToCandidate(unittest.TestCase):
    def test_maps_and_resolves_icao(self):
        obj = {"airline_iata": "fr", "flight_number": "1834",
               "from_iata": "STN", "to_iata": "DUB", "date": "2019-06-01",
               "departure": "2019-06-01T07:10", "seat_number": "12a"}
        c = llm.to_candidate(obj, "acct/x.txt")
        self.assertEqual(c["flightNumber"], "FR1834")
        self.assertEqual(c["airline"], "RYR")     # IATA FR -> ICAO
        self.assertEqual(c["from"], "EGSS")        # STN -> ICAO
        self.assertEqual(c["to"], "EIDW")          # DUB -> ICAO
        self.assertEqual(c["seatNumber"], "12A")
        self.assertEqual(c["seat"], "window")      # A -> window
        self.assertEqual(c["extractor"], "llm")

    def test_confidence_is_never_high(self):
        obj = {"airline_iata": "FR", "flight_number": "1", "from_iata": "STN",
               "to_iata": "DUB", "date": "2019-06-01"}
        c = llm.to_candidate(obj, "x")
        self.assertNotEqual(c["confidence"], "high")
        self.assertTrue(any("verify" in i.lower() for i in c["issues"]))

    def test_cancelled_flagged(self):
        c = llm.to_candidate({"airline_iata": "FR", "flight_number": "1",
                              "from_iata": "STN", "to_iata": "DUB",
                              "date": "2019-06-01", "status": "Cancelled"}, "x")
        self.assertTrue(any("ghost" in i.lower() for i in c["issues"]))


class TestExtractFlights(unittest.TestCase):
    def setUp(self):
        self._orig = llm.chat
        llm.chat = lambda url, model, key, sys_, usr, timeout=60: (
            '[{"airline_iata":"W6","flight_number":"2310","from_iata":"LTN",'
            '"to_iata":"OTP","date":"2021-08-14","departure":"2021-08-14T06:00"}]')

    def tearDown(self):
        llm.chat = self._orig

    def test_returns_uncertain_candidate(self):
        cands = llm.extract_flights("some ryanair-ish email body", "acct/e.txt",
                                    url="http://x:3000", model="m")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["flightNumber"], "W62310")
        self.assertEqual(cands[0]["confidence"], "uncertain")
        self.assertEqual(cands[0]["extractor"], "llm")

    def test_no_config_is_noop(self):
        # no url/model -> no call, empty result (env not set in test)
        self.assertEqual(llm.extract_flights("body", "s", url=None, model=None), [])

    def test_drops_contentless_rows(self):
        llm.chat = lambda *a, **k: '[{"seat_number": "1A"}]'  # no route, no number
        self.assertEqual(
            llm.extract_flights("b", "s", url="http://x", model="m"), [])


class TestEnabledGating(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in
                     ("LLM_FALLBACK", "LLM_URL", "LLM_MODEL")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_off_by_default(self):
        for k in ("LLM_FALLBACK", "LLM_URL", "LLM_MODEL"):
            os.environ.pop(k, None)
        self.assertFalse(llm.enabled())

    def test_needs_flag_and_endpoint(self):
        os.environ["LLM_FALLBACK"] = "1"
        os.environ.pop("LLM_URL", None)
        self.assertFalse(llm.enabled())          # flag but no endpoint
        os.environ["LLM_URL"] = "http://x:3000"
        os.environ["LLM_MODEL"] = "m"
        self.assertTrue(llm.enabled())


class TestPopulateIntegration(unittest.TestCase):
    """parse_candidates should call the fallback only for emails the deterministic
    extractors miss, and surface the result as an uncertain candidate."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        acct = os.path.join(self.tmp, "acct")
        os.makedirs(acct)
        # an email the three templates don't recognise
        with open(os.path.join(acct, "1_ryanair.txt"), "w") as fh:
            fh.write("From: noreply@ryanair.com\nSubject: Your trip\n\n"
                     "Thanks for booking. STN to DUB on 1 June.\n")
        self._orig = llm.extract_file
        llm.extract_file = lambda path, *a, **k: [llm.to_candidate(
            {"airline_iata": "FR", "flight_number": "1834", "from_iata": "STN",
             "to_iata": "DUB", "date": "2019-06-01"}, "acct/1_ryanair.txt")]

    def tearDown(self):
        llm.extract_file = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fallback_used_when_templates_miss(self):
        cands = populate.parse_candidates(self.tmp, use_llm=True)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["extractor"], "llm")
        # and it classifies as uncertain (needs a human), never new/auto-writable
        populate.classify(cands, existing=set())
        self.assertEqual(cands[0]["status"], "uncertain")
        self.assertEqual(populate.auto_writable(cands), [])

    def test_fallback_skipped_when_off(self):
        cands = populate.parse_candidates(self.tmp, use_llm=False)
        self.assertEqual(cands, [])


if __name__ == "__main__":
    unittest.main()
