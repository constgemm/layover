#!/usr/bin/env python3
"""
flightparse — turn Layover-saved emails into candidate flights (deterministic).

Phase 1 of Layover auto-population. No LLM, no network: three hand-written
extractors cover the formats that dominate the mailboxes and are stable enough
to trust without review of the *parser* (the flights themselves are still
reviewed before anything is written to AirTrail — see populate.py).

Extractors, in order of reliability:
  1. jsonld      — schema.org FlightReservation markup embedded by SWISS / Edelweiss
                   / TAP / airBaltic check-in + boarding-pass emails. Structured,
                   multi-segment aware, carries airline + both airports + times.
  2. lh_checkin  — Lufthansa "Sie sind eingecheckt: LH1769, LCA-MUC, 26AUG20, 15:35,
                   ... Sitz 8D" boarding confirmations. Everything is in the subject.
  3. ba_eticket  — British Airways "Your Itinerary" / "Reiseplan" e-ticket receipts,
                   both the English (one-field-per-line) and German (concatenated)
                   layouts. BA prints friendly airport *names*, mapped via airdata.

A candidate is a plain dict (see Candidate.to_dict) with ICAO from/to + airline,
a canonical date, local departure/arrival timestamps, seat when present, and QA
provenance (source_file, extractor, confidence, issues). flightReason is always
"leisure" per Constantin's rule.

Usage:
    python3 flightparse.py flight-mail-out/            # -> candidates on stdout (JSON)
    python3 flightparse.py flight-mail-out/ -o cand.json
    python3 flightparse.py flight-mail-out/ionos/16587_*.txt   # single file(s)
"""

import glob
import json
import os
import re
import sys
from datetime import datetime

import airdata

# ---------------------------------------------------------------------------
# email file loading
# ---------------------------------------------------------------------------

HEADER_KEYS = ("Account", "Folder", "UID", "Date", "From", "Subject", "PDFs")


def load_email(path):
    """Split a Layover .txt into ({header: value}, body)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    header, body = {}, raw
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(%s):\s?(.*)$" % "|".join(HEADER_KEYS), line)
        if m:
            header[m.group(1)] = m.group(2)
        elif line == "" and header:
            body = "\n".join(lines[i + 1:])
            break
    return header, body


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------

def norm_flight_number(airline_iata, number):
    """'BA', '0713' -> 'BA713'  ·  'LH', '1769' -> 'LH1769'."""
    number = re.sub(r"^0+(?=\d)", "", str(number).strip())
    return f"{airline_iata.upper()}{number}"


# 6-abreast narrowbody heuristic (A320/A321/737). Wide bodies differ; treated as
# a hint only, and left null when the letter is ambiguous.
_SEAT_LETTER = {"A": "window", "F": "window", "C": "aisle", "D": "aisle",
                "B": "middle", "E": "middle"}


def seat_type(seat_number):
    if not seat_number:
        return None
    m = re.search(r"([A-K])\s*$", seat_number.strip().upper())
    return _SEAT_LETTER.get(m.group(1)) if m else None


def seat_class(name):
    if not name:
        return None
    n = name.strip().lower()
    if "business" in n:
        return "business"
    if "first" in n:
        return "first"
    if "premium" in n or "economy+" in n or "eco flex" in n:
        return "economy+"
    if "eco" in n or "economy" in n or "traveller" in n:
        return "economy"
    return None


class Candidate:
    """One parsed flight leg, with QA provenance."""

    def __init__(self, source_file, extractor):
        self.source_file = source_file
        self.extractor = extractor
        self.airline_iata = None
        self.flight_number = None
        self.from_iata = None
        self.to_iata = None
        self.date = None            # YYYY-MM-DD canonical anchor
        self.departure = None       # airport-local ISO (naive) when known
        self.arrival = None
        self.seat_number = None
        self.seat_class = None
        self.status = None          # Confirmed / Cancelled ...
        self.issues = []

    def _icaos(self):
        return (airdata.airport_icao(self.from_iata),
                airdata.airport_icao(self.to_iata))

    @property
    def confidence(self):
        f, t = self._icaos()
        ok = (self.flight_number and self.date and f and t
              and (self.status or "").lower() not in ("cancelled", "canceled",
                                                       "storniert"))
        return "high" if ok and not self.issues else "uncertain"

    def to_dict(self):
        f_icao, t_icao = self._icaos()
        issues = list(self.issues)
        if self.from_iata and not f_icao:
            issues.append(f"unknown departure airport {self.from_iata!r}")
        if self.to_iata and not t_icao:
            issues.append(f"unknown arrival airport {self.to_iata!r}")
        if (self.status or "").lower() in ("cancelled", "canceled", "storniert"):
            issues.append(f"reservation status {self.status!r} — verify not a ghost")
        return {
            "flightNumber": self.flight_number,
            "airline": airdata.airline_icao(self.airline_iata),
            "airline_iata": self.airline_iata,
            "from": f_icao,
            "to": t_icao,
            "from_iata": self.from_iata,
            "to_iata": self.to_iata,
            "date": self.date,
            "departure": self.departure,
            "arrival": self.arrival,
            "seatNumber": self.seat_number,
            "seat": seat_type(self.seat_number),
            "seatClass": self.seat_class,
            "flightReason": "leisure",
            "source_file": self.source_file,
            "extractor": self.extractor,
            "confidence": "uncertain" if issues else self.confidence,
            "issues": issues,
        }


# ---------------------------------------------------------------------------
# extractor 1 — schema.org FlightReservation JSON-LD
# ---------------------------------------------------------------------------

def _first(pattern, text, flags=re.S):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def extract_jsonld(body, source_file):
    """SWISS / Edelweiss / TAP / airBaltic embed one FlightReservation block per
    leg. Blocks are frequently malformed JSON (duplicate keys, trailing commas),
    so fields are pulled with scoped regexes rather than json.loads."""
    if '"FlightReservation"' not in body:
        return []
    # Split so each chunk holds exactly one reservation's fields.
    chunks = re.split(r'"@type"\s*:\s*"FlightReservation"', body)[1:]
    out = []
    for chunk in chunks:
        if '"reservationFor"' not in chunk:
            continue
        # reservationFor Flight sub-object (airline + airports + times live here)
        rf = chunk.split('"reservationFor"', 1)[1]
        flight_no = _first(r'"flightNumber"\s*:\s*"([A-Z0-9]{1,4})"', rf)
        airline = _first(r'"airline"\s*:\s*\{.*?"iataCode"\s*:\s*"([A-Z0-9]{2})"', rf)
        dep = _first(r'"departureAirport"\s*:\s*\{.*?"iataCode"\s*:\s*"([A-Z]{3})"', rf)
        arr = _first(r'"arrivalAirport"\s*:\s*\{.*?"iataCode"\s*:\s*"([A-Z]{3})"', rf)
        if not (flight_no and airline and dep and arr):
            continue
        c = Candidate(source_file, "jsonld")
        c.airline_iata = airline
        c.flight_number = norm_flight_number(airline, flight_no)
        c.from_iata, c.to_iata = dep, arr
        isot = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+\-]\d{2}:?\d{2}|Z)?)'
        c.departure = _first(r'"departureTime"\s*:\s*"%s"' % isot, rf, 0)
        c.arrival = _first(r'"arrivalTime"\s*:\s*"%s"' % isot, rf, 0)
        c.date = (c.departure or "")[:10] or None
        seat = _first(r'"airplaneSeat"\s*:\s*"([^"]*)"', chunk, 0)
        c.seat_number = seat.strip() if seat and seat.strip() else None
        c.seat_class = seat_class(
            _first(r'"airplaneSeatClass"\s*:\s*\{.*?"name"\s*:\s*"([^"]+)"', chunk))
        c.status = _first(r'"reservationStatus"\s*:\s*"[^"]*?/(\w+)"', chunk, 0)
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# extractor 2 — Lufthansa "Sie sind eingecheckt" boarding confirmation
# ---------------------------------------------------------------------------

LH_CHECKIN_RE = re.compile(
    r"Sie sind eingecheckt:\s*"
    r"([A-Z]{2})\s?(\d{1,4}),\s*"          # airline + number
    r"([A-Z]{3})-([A-Z]{3}),\s*"           # from-to IATA
    r"(\d{2}[A-Za-z]{3}\d{2}),\s*"         # date 26AUG20
    r"(\d{1,2}:\d{2})",                     # boarding/dep time
    re.I,
)


def extract_lh_checkin(header, body, source_file):
    subject = header.get("Subject", "")
    m = LH_CHECKIN_RE.search(subject)
    if not m:
        return []
    airline, number, dep, arr, ddate, dtime = m.groups()
    c = Candidate(source_file, "lh_checkin")
    c.airline_iata = airline.upper()
    c.flight_number = norm_flight_number(airline, number)
    c.from_iata, c.to_iata = dep.upper(), arr.upper()
    try:
        d = datetime.strptime(ddate.upper(), "%d%b%y")
        c.date = d.strftime("%Y-%m-%d")
        c.departure = f"{c.date}T{dtime.zfill(5)}"
    except ValueError:
        c.issues.append(f"could not parse date {ddate!r}")
    seat = re.search(r"Sitz\s*(\d{1,3}[A-K])", subject, re.I) \
        or re.search(r"^Sitz\s*\n?\s*(\d{1,3}[A-K])\s*$", body, re.I | re.M)
    if seat:
        c.seat_number = seat.group(1).upper()
    return [c]


# ---------------------------------------------------------------------------
# extractor 3 — British Airways e-ticket receipt itinerary
# ---------------------------------------------------------------------------

# Works on whitespace-collapsed body. Handles both the English one-field-per-line
# layout and the German fully-concatenated layout. Place names are letters/spaces/
# parens/dots/dashes up to the next date, "Terminal", next "BAnnnn", or Passenger.
# Terminal tokens ("Terminal 5", "Terminal N") appear inconsistently — sometimes
# after the arrival place, sometimes between departure place and arrival date — so
# an optional terminal is allowed after each place.
_TERM = r"(?:Terminal\s*\w{0,4}\s*)?"
BA_SEG_RE = re.compile(
    r"(BA)\s?(\d{3,4})\s*"
    r"British Airways[^0-9]*?(Confirmed|Bestätigt|Cancelled|Storniert|Canceled)\s*"
    r"(\d{1,2}\s\w{3}\s\d{4})\s*(\d{1,2}:\d{2})\s*"          # dep date/time
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.'()\- ]*?)\s*" + _TERM +       # dep place (+ opt terminal)
    r"(\d{1,2}\s\w{3}\s\d{4})\s*(\d{1,2}:\d{2})\s*"          # arr date/time
    r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.'()\- ]*?)\s*" + _TERM +       # arr place (+ opt terminal)
    r"(?=BA\s?\d{3,4}|Passenger|Passagier|Baggage|Gep\b|$)",
    re.S,
)


def _ba_date(s):
    for fmt in ("%d %b %Y",):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def extract_ba_eticket(header, body, source_file):
    frm = header.get("From", "").lower()
    if "ba.com" not in frm and "british airways" not in frm:
        return []
    if "Your Itinerary" not in body and "Reiseplan" not in body:
        return []
    flat = re.sub(r"\s+", " ", body)
    out = []
    for m in BA_SEG_RE.finditer(flat):
        _, number, status, ddate, dtime, dplace, adate, atime, aplace = m.groups()
        c = Candidate(source_file, "ba_eticket")
        c.airline_iata = "BA"
        c.flight_number = norm_flight_number("BA", number)
        c.status = status
        c.from_iata = airdata.name_to_iata(dplace)
        c.to_iata = airdata.name_to_iata(aplace)
        if not c.from_iata:
            c.issues.append(f"unmapped BA airport name {dplace.strip()!r}")
            c.from_iata = dplace.strip()
        if not c.to_iata:
            c.issues.append(f"unmapped BA airport name {aplace.strip()!r}")
            c.to_iata = aplace.strip()
        c.date = _ba_date(ddate)
        if c.date:
            c.departure = f"{c.date}T{dtime.zfill(5)}"
        adate_p = _ba_date(adate)
        if adate_p:
            c.arrival = f"{adate_p}T{atime.zfill(5)}"
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def parse_file(path):
    header, body = load_email(path)
    rel = os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
    cands = []
    cands += extract_jsonld(body, rel)
    cands += extract_lh_checkin(header, body, rel)
    cands += extract_ba_eticket(header, body, rel)
    return cands


def dedup_candidates(cands):
    """Collapse the same leg parsed from several emails; keep the most complete."""
    def score(d):
        return (d["confidence"] == "high", bool(d.get("arrival")),
                bool(d.get("seatNumber")), -len(d.get("issues", [])))

    best = {}
    for d in cands:
        key = (d.get("flightNumber"), d.get("date"))
        if key == (None, None):
            best[id(d)] = d
            continue
        if key not in best or score(d) > score(best[key]):
            best[key] = d
    return list(best.values())


def iter_paths(args):
    for a in args:
        if os.path.isdir(a):
            yield from sorted(glob.glob(os.path.join(a, "**", "*.txt"),
                                        recursive=True))
        else:
            yield from sorted(glob.glob(a))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        sys.exit(__doc__)
    out_path = None
    if "-o" in sys.argv:
        out_path = sys.argv[sys.argv.index("-o") + 1]
        args = [a for a in args if a != out_path]

    raw = []
    for path in iter_paths(args):
        try:
            raw += [c.to_dict() for c in parse_file(path)]
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the sweep
            print(f"  !! {path}: {exc}", file=sys.stderr)
    candidates = dedup_candidates(raw)
    candidates.sort(key=lambda d: (d.get("date") or "", d.get("flightNumber") or ""))

    payload = json.dumps(candidates, indent=2, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        hi = sum(c["confidence"] == "high" for c in candidates)
        print(f"{len(candidates)} candidates ({hi} high-confidence) -> {out_path}",
              file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
