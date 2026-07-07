#!/usr/bin/env python3
"""
dawarich — location-history validation of candidate flights (Phase 3).

Dawarich already knows where you actually were (your phone's point history). This
module uses that as a second, independent signal on a parsed candidate — the same
thing a flight-tracker app gets from GPS, but self-hosted:

  * points near the destination shortly after arrival  -> "confirmed"
  * tracking clearly active that day but nowhere near either endpoint
        -> "contradicted"  (a likely cancellation/rebooking — the ghost-flight trap)
  * no coordinates for the airport, or no points in the window -> "unknown"

It only ever ANNOTATES: it sets candidate["location"] and, on a contradiction, adds
an issue (which also blocks unattended auto-write, since _blocked_reason trips on
"cancellation"). It never changes confidence or writes anything. Absence of data is
"unknown", never "contradicted" — a Dawarich tracking gap must not invent a problem.

Config (env, or the matching populate.py flags):
    DAWARICH_URL       base URL              (e.g. http://airtrail-host:3005)
    DAWARICH_API_KEY   API key
    DAWARICH_VALIDATE  truthy to enable

Zero dependencies (urllib + math).
"""

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import airdata

RADIUS_KM = 100          # metro-area proximity for "near an airport"
WINDOW_DAYS = 1          # look ±1 day around the flight date
MIN_POINTS_FOR_CONTRADICTION = 5   # need real tracking before calling a miss a problem


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def load_config(url=None, api_key=None):
    return (url or os.environ.get("DAWARICH_URL"),
            api_key or os.environ.get("DAWARICH_API_KEY"))


def enabled(url=None, api_key=None):
    on = os.environ.get("DAWARICH_VALIDATE", "").strip().lower() in (
        "1", "true", "yes", "on")
    u, k = load_config(url, api_key)
    return bool(on and u and k)


# ---------------------------------------------------------------------------
# geo
# ---------------------------------------------------------------------------

def haversine_km(a, b):
    """Great-circle distance in km between two (lat, lon) pairs."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
# fetching points
# ---------------------------------------------------------------------------

def fetch_points(url, api_key, start, end, timeout=30):
    """GET points in [start, end] (date strings/ISO). Returns [(lat, lon), ...].

    Tolerant of Dawarich response shape: accepts a bare list or {data|points|
    features:[...]}, and either flat lat/lon keys or GeoJSON geometry."""
    q = urllib.parse.urlencode({
        "api_key": api_key,
        "start_at": start,
        "end_at": end,
        "per_page": 1000,
    })
    endpoint = url.rstrip("/") + "/api/v1/points?" + q
    req = urllib.request.Request(endpoint, headers={
        "Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    return _coords(payload)


def _coords(payload):
    rows = payload
    if isinstance(payload, dict):
        for k in ("data", "points", "features", "results"):
            if isinstance(payload.get(k), list):
                rows = payload[k]
                break
        else:
            rows = []
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        lat = r.get("latitude", r.get("lat"))
        lon = r.get("longitude", r.get("lon", r.get("lng")))
        if lat is None and isinstance(r.get("geometry"), dict):
            coords = r["geometry"].get("coordinates")     # GeoJSON [lon, lat]
            if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                lon, lat = coords[0], coords[1]
        try:
            out.append((float(lat), float(lon)))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _near(points, airport_code, radius_km=RADIUS_KM):
    """True/False if any point is within radius of the airport; None if the
    airport has no known coordinates (can't tell)."""
    ap = airdata.airport_latlon(airport_code)
    if not ap:
        return None
    return any(haversine_km(ap, p) <= radius_km for p in points)


def classify_location(cand, points):
    """Return 'confirmed' | 'contradicted' | 'unknown' for one candidate given the
    points already filtered to its date window."""
    near_to = _near(points, cand.get("to") or cand.get("to_iata"))
    near_from = _near(points, cand.get("from") or cand.get("from_iata"))
    if near_to or near_from:
        return "confirmed"
    # only call it a contradiction if tracking was genuinely active and we had a
    # coordinate to test against — otherwise we simply don't know.
    testable = near_to is not None or near_from is not None
    if testable and len(points) >= MIN_POINTS_FOR_CONTRADICTION:
        return "contradicted"
    return "unknown"


def validate(cands, url=None, api_key=None, fetch=fetch_points):
    """Annotate each candidate with candidate['location']; add a review issue on a
    contradiction. `fetch` is injectable for testing. Returns the same list."""
    url, api_key = load_config(url, api_key)
    for c in cands:
        if not c.get("date"):
            c["location"] = "unknown"
            continue
        try:
            d = datetime.strptime(c["date"][:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            c["location"] = "unknown"
            continue
        start = (d - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
        end = (d + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
        try:
            points = fetch(url, api_key, start, end) if url and api_key else []
        except Exception as exc:  # noqa: BLE001 - a Dawarich hiccup must not break the run
            c["location"] = "unknown"
            c.setdefault("issues", []).append(f"Dawarich check failed: {exc}")
            continue
        loc = classify_location(c, points)
        c["location"] = loc
        if loc == "contradicted":
            c.setdefault("issues", []).append(
                f"Dawarich shows you elsewhere around {c['date']} "
                f"(no points near {c.get('from_iata')}/{c.get('to_iata')}) — "
                "possible cancellation/rebooking")
    return cands
