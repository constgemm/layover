#!/usr/bin/env python3
"""
Unit tests for the Layover Phase-1 deterministic parser.

Fixtures are small, pseudonymous reconstructions of the three real email formats
(no personal data, no real ticket numbers) — enough to pin the parsing contract.
Run:  python3 -m unittest discover -s tests   (from the repo root)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import airtrail  # noqa: E402
import flightparse  # noqa: E402


def write(tmp, name, text):
    d = os.path.join(tmp, "acct")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    open(p, "w", encoding="utf-8").write(text)
    return p


# --- fixture 1: SWISS boarding pass with schema.org JSON-LD (round trip) ------
SWISS_JSONLD = '''Account: acct
Folder: INBOX
UID: 1
Date: Wed, 16 Mar 2022 11:30:00 -0000
From: Swiss International Air Lines <info@noti.swiss.com>
Subject: Ihre Bordkarte(n)
PDFs: LX_1246_20220317_TESTER_ALEX.pdf

 "@context": "http://schema.org",
 "@type": "FlightReservation",
 "reservationStatus": "http://schema.org/Confirmed",
 "reservationFor": {
 "@type": "Flight",
 "flightNumber": "1246",
 "airline": { "@type": "Airline", "name": "SWISS", "iataCode": "LX" },
 "departureAirport": { "@type": "Airport", "name": "Zurich", "iataCode": "ZRH" },
 "departureTime": "2022-03-17T08:00",
 "arrivalAirport": { "@type": "Airport", "name": "Arlanda", "iataCode": "ARN" },
 "arrivalTime": "2022-03-17T10:30"
 },
 "airplaneSeat": "12A ",
 "airplaneSeatClass": { "@type": "AirplaneSeatClass", "name": "Economy" }

 "@type": "FlightReservation",
 "reservationStatus": "http://schema.org/Confirmed",
 "reservationFor": {
 "@type": "Flight",
 "flightNumber": "1251",
 "airline": { "@type": "Airline", "name": "SWISS", "iataCode": "LX" },
 "departureAirport": { "@type": "Airport", "name": "Arlanda", "iataCode": "ARN" },
 "departureTime": "2022-03-21T15:30",
 "arrivalAirport": { "@type": "Airport", "name": "Zurich", "iataCode": "ZRH" },
 "arrivalTime": "2022-03-21T18:00"
 },
 "airplaneSeatClass": { "@type": "AirplaneSeatClass", "name": "Business" }
'''

# --- fixture 2: Lufthansa "Sie sind eingecheckt" boarding confirmation --------
LH_CHECKIN = '''Account: acct
Folder: INBOX
UID: 2
Date: Tue, 25 Aug 2020 16:26:29 +0200
From: bordkarte@lufthansa.com
Subject: Sie sind eingecheckt: LH1769, LCA-MUC, 26AUG20, 15:35, Gate ...., Sitz 8D, Bordkarte
PDFs:

Lufthansa Mobile Bordkarte
Flug LCA-MUC LH1769 Datum 26AUG20 Sitz 8D
Buchungscode TWYNUI
'''

# --- fixture 3: British Airways e-ticket receipt (EN; terminal placement varies)
BA_ETICKET = '''Account: acct
Folder: INBOX
UID: 3
Date: Thu, 13 Feb 2020 18:13:34 +0000
From: British Airways e-ticket <BA.e-ticket@email.ba.com>
Subject: Your e-ticket receipt PP4G3A: 14 Feb 2020 18:05
PDFs:

Your Itinerary
BA0745
British Airways | Euro Traveller | Confirmed
14 Feb 2020
18:05
Zurich
14 Feb 2020
19:00
Heathrow (London)
Terminal 5
BA0720
British Airways | Euro Traveller | Confirmed
17 Feb 2020
19:20
Heathrow (London)
Terminal 5
17 Feb 2020
22:10
Zurich
Passenger MR ALEX TESTER
'''


class TestJsonLd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_swiss_roundtrip(self):
        p = write(self.tmp, "1_swiss.txt", SWISS_JSONLD)
        cs = [c.to_dict() for c in flightparse.parse_file(p)]
        self.assertEqual(len(cs), 2)
        out = cs[0]
        self.assertEqual(out["flightNumber"], "LX1246")
        self.assertEqual(out["airline"], "SWR")          # LX -> SWR (ICAO)
        self.assertEqual((out["from"], out["to"]), ("LSZH", "ESSA"))  # ZRH, ARN
        self.assertEqual(out["date"], "2022-03-17")
        self.assertEqual(out["departure"], "2022-03-17T08:00")
        self.assertEqual(out["seatNumber"], "12A")
        self.assertEqual(out["seat"], "window")          # A -> window
        self.assertEqual(out["seatClass"], "economy")
        self.assertEqual(out["flightReason"], "leisure")
        self.assertEqual(out["confidence"], "high")
        self.assertEqual(cs[1]["flightNumber"], "LX1251")
        self.assertEqual(cs[1]["seatClass"], "business")

    def test_offset_time(self):
        text = SWISS_JSONLD.replace('"2022-03-17T08:00"',
                                    '"2025-05-28T17:50:00-0600"')
        p = write(self.tmp, "2_off.txt", text)
        c = [c.to_dict() for c in flightparse.parse_file(p)][0]
        self.assertEqual(c["date"], "2025-05-28")
        self.assertTrue(c["departure"].startswith("2025-05-28T17:50"))


class TestLhCheckin(unittest.TestCase):
    def test_subject(self):
        tmp = tempfile.mkdtemp()
        p = write(tmp, "2_lh.txt", LH_CHECKIN)
        cs = [c.to_dict() for c in flightparse.parse_file(p)]
        self.assertEqual(len(cs), 1)
        c = cs[0]
        self.assertEqual(c["flightNumber"], "LH1769")
        self.assertEqual(c["airline"], "DLH")
        self.assertEqual((c["from"], c["to"]), ("LCLK", "EDDM"))  # LCA, MUC
        self.assertEqual(c["date"], "2020-08-26")
        self.assertEqual(c["departure"], "2020-08-26T15:35")
        self.assertEqual(c["seatNumber"], "8D")
        self.assertEqual(c["confidence"], "high")

    def test_fwd_prefix(self):
        tmp = tempfile.mkdtemp()
        p = write(tmp, "2b.txt", LH_CHECKIN.replace(
            "Subject: Sie", "Subject: Fwd: Sie"))
        cs = [c.to_dict() for c in flightparse.parse_file(p)]
        self.assertEqual(cs[0]["flightNumber"], "LH1769")


class TestBaEticket(unittest.TestCase):
    def test_two_segments(self):
        tmp = tempfile.mkdtemp()
        p = write(tmp, "3_ba.txt", BA_ETICKET)
        cs = [c.to_dict() for c in flightparse.parse_file(p)]
        self.assertEqual(len(cs), 2)
        self.assertEqual(cs[0]["flightNumber"], "BA745")
        self.assertEqual(cs[0]["airline"], "BAW")
        self.assertEqual((cs[0]["from"], cs[0]["to"]), ("LSZH", "EGLL"))
        self.assertEqual(cs[0]["date"], "2020-02-14")
        # second segment has the terminal token between dep place and arr date
        self.assertEqual(cs[1]["flightNumber"], "BA720")
        self.assertEqual((cs[1]["from"], cs[1]["to"]), ("EGLL", "LSZH"))
        self.assertEqual(cs[1]["date"], "2020-02-17")


class TestDedupAndKeys(unittest.TestCase):
    def test_flight_key_normalisation(self):
        self.assertEqual(airtrail.flight_key("BA0745", "2020-02-14"),
                         airtrail.flight_key("BA745", "2020-02-14"))
        self.assertEqual(airtrail.flight_key("LH 1769", "2020-08-26"),
                         ("LH1769", "2020-08-26"))

    def test_existing_keys_from_departure(self):
        keys = airtrail.existing_keys([
            {"flightNumber": "BA0745", "departure": "2020-02-14T18:05:00+01:00"}])
        self.assertIn(("BA745", "2020-02-14"), keys)

    def test_dedup_keeps_most_complete(self):
        a = {"flightNumber": "LX1246", "date": "2022-03-17", "confidence": "high",
             "arrival": None, "seatNumber": None, "issues": []}
        b = {"flightNumber": "LX1246", "date": "2022-03-17", "confidence": "high",
             "arrival": "2022-03-17T10:30", "seatNumber": "12A", "issues": []}
        kept = flightparse.dedup_candidates([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["seatNumber"], "12A")


if __name__ == "__main__":
    unittest.main()
