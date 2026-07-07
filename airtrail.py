#!/usr/bin/env python3
"""
airtrail — minimal AirTrail API client for Layover auto-population.

Read-only by default. It exists to answer one question during a weekly run —
"which of these parsed candidates does AirTrail already have?" — by pulling
GET /api/flight/list and matching on (flight number, date). It also knows how to
turn a candidate into the POST /api/flight/save body, but writing is gated behind
an explicit, interactive per-flight confirmation (see populate.py --commit); the
cron path never writes.

Zero dependencies (urllib). Config, in order of precedence:
    --url / --api-key flags
    env AIRTRAIL_URL, AIRTRAIL_API_KEY
    airtrail.ini  ([airtrail] url = ... / api_key = ...)   [git-ignored]
Offline: --flights-json FILE reads a saved /api/flight/list dump instead of the
network, so dedup works on the box without the API key in view.

The API key is a SECRET: keep it in airtrail.ini (git-ignored, chmod 600) or the
environment. Never commit it. (Layover's rule, and the vault's.)
"""

import configparser
import json
import os
import re
import sys
import urllib.request
from pathlib import Path


def load_config(url=None, api_key=None, ini="airtrail.ini"):
    url = url or os.environ.get("AIRTRAIL_URL")
    api_key = api_key or os.environ.get("AIRTRAIL_API_KEY")
    p = Path(ini)
    if (not url or not api_key) and p.exists():
        cfg = configparser.ConfigParser()
        cfg.read(p)
        if cfg.has_section("airtrail"):
            url = url or cfg["airtrail"].get("url")
            api_key = api_key or cfg["airtrail"].get("api_key")
    return url, api_key


def flight_key(flight_number, date):
    """Normalised dedup key: 'BA0745' + '2020-02-14' -> ('BA745', '2020-02-14').

    Strips spaces/punctuation and leading zeros in the numeric part so the same
    leg written by different sources (BA0745 vs BA745) collapses to one key."""
    if not flight_number:
        return (None, date)
    fn = re.sub(r"[^A-Za-z0-9]", "", flight_number).upper()
    m = re.match(r"([A-Z0-9]?[A-Z])0*(\d+)$", fn) or re.match(r"([A-Z]+)0*(\d+)$", fn)
    if m:
        fn = m.group(1) + m.group(2)
    return (fn, (date or "")[:10] or None)


# ---------------------------------------------------------------------------
# fetching existing flights
# ---------------------------------------------------------------------------

def _extract_flights(payload):
    if isinstance(payload, dict):
        for k in ("flights", "data", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
        return [payload]
    return payload if isinstance(payload, list) else []


def fetch_flights(url, api_key, timeout=30):
    """GET {url}/api/flight/list -> list of flight dicts."""
    endpoint = url.rstrip("/") + "/api/flight/list"
    req = urllib.request.Request(endpoint, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _extract_flights(json.loads(resp.read().decode()))


def load_flights_json(path):
    return _extract_flights(json.loads(Path(path).read_text()))


def existing_keys(flights):
    """Set of (flightNumber, date) keys from AirTrail flight objects."""
    keys = set()
    for f in flights:
        date = f.get("date") or (f.get("departure") or "")[:10] or None
        keys.add(flight_key(f.get("flightNumber"), date))
    return keys


# ---------------------------------------------------------------------------
# candidate -> AirTrail POST body (used only by the gated --commit path)
# ---------------------------------------------------------------------------

def build_payload(cand, user_id=None, guest_name=None):
    """Map a parsed candidate to a POST /api/flight/save body.

    from/to are ICAO (AirTrail accepts ICAO or IATA); airline is ICAO; departure/
    arrival are passed through as-is. Times parsed from SWISS/Edelweiss JSON-LD may
    be airport-local or offset-bearing — AirTrail interprets by airport tz, so
    review before committing."""
    seat = {
        "userId": user_id,
        "guestName": guest_name if not user_id else None,
        "seat": cand.get("seat"),
        "seatNumber": cand.get("seatNumber"),
        "seatClass": cand.get("seatClass"),
    }
    return {
        "from": cand.get("from") or cand.get("from_iata"),
        "to": cand.get("to") or cand.get("to_iata"),
        "departure": cand.get("departure"),
        "arrival": cand.get("arrival"),
        "date": cand.get("date"),
        "datePrecision": "day",
        "airline": cand.get("airline"),
        "flightNumber": cand.get("flightNumber"),
        "flightReason": cand.get("flightReason", "leisure"),
        "seats": [seat],
    }


def post_flight(url, api_key, payload, timeout=30):
    """POST one flight. Only ever called from the interactive --commit path."""
    endpoint = url.rstrip("/") + "/api/flight/save"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(endpoint, data=data, method="POST", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def main():
    """Tiny CLI: print existing (flightNumber, date) keys AirTrail already holds."""
    url, api_key = load_config()
    flights = (load_flights_json(sys.argv[2])
               if len(sys.argv) > 2 and sys.argv[1] == "--flights-json"
               else fetch_flights(url, api_key))
    for k in sorted(existing_keys(flights)):
        print(f"{k[0]}\t{k[1]}")
    print(f"\n{len(flights)} flights in AirTrail", file=sys.stderr)


if __name__ == "__main__":
    main()
