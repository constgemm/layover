#!/usr/bin/env python3
"""
llm — optional local-LLM fallback parser for the long tail (Phase 2).

The deterministic extractors in flightparse.py cover the formats that dominate and
are stable (SWISS/Edelweiss JSON-LD, Lufthansa check-in, BA e-tickets). This module
picks up the *rest* — Ryanair, Wizz, LATAM, OTAs — whose emails have no reliable
structured markup, by asking a local **OpenAI-compatible** chat endpoint (Open WebUI
on the homelab box) to extract flights as strict JSON.

Safety contract, on purpose and non-negotiable:
  * every candidate this returns is `extractor="llm"` and `confidence="uncertain"`,
    so classify() drops it in the human-reviewed bucket and _blocked_reason() keeps
    it out of the unattended --auto-write path. An LLM guess never writes itself.
  * it only runs on emails the deterministic pass missed (see populate.py), so a
    normal weekly run makes zero LLM calls unless there's genuinely new long-tail mail.
  * disabled unless configured — no endpoint, no calls.

Config (env, or the matching populate.py flags):
    LLM_URL       base URL of the OpenAI-compatible API   (e.g. http://vmgpu:3000)
    LLM_MODEL     model name to request
    LLM_API_KEY   Bearer key (Open WebUI issues one; optional for keyless servers)
    LLM_FALLBACK  set truthy (1/true/yes/on) to enable the fallback

Zero dependencies (urllib + json).
"""

import json
import os
import re
import urllib.request

import airdata
import flightparse

# The schema we force the model to emit. Kept tiny and flat so a small local model
# can hit it reliably; anything richer (seat class, terminals) is best-effort.
SYSTEM_PROMPT = (
    "You extract flight bookings from the text of a single airline or travel-agency "
    "email. Return ONLY a JSON array (no prose, no code fence). Each element is one "
    "flown/booked flight leg with these keys:\n"
    '  airline_iata   two-char IATA airline code (e.g. "FR", "W6", "LA") or null\n'
    '  flight_number  digits only, no airline prefix (e.g. "1834") or null\n'
    '  from_iata      3-letter IATA departure airport or null\n'
    '  to_iata        3-letter IATA arrival airport or null\n'
    '  date           departure date as YYYY-MM-DD or null\n'
    '  departure      local departure timestamp YYYY-MM-DDTHH:MM or null\n'
    '  arrival        local arrival timestamp YYYY-MM-DDTHH:MM or null\n'
    '  seat_number    e.g. "14C" or null\n'
    '  status         "Confirmed" / "Cancelled" / null\n'
    "Rules: one element per leg (a round trip is two). Use IATA codes, never names. "
    "If the email is not a flight booking, or you are unsure, return []. Never invent "
    "a flight number, airport, or date — use null for anything not clearly stated."
)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def load_config(url=None, model=None, api_key=None):
    url = url or os.environ.get("LLM_URL")
    model = model or os.environ.get("LLM_MODEL")
    api_key = api_key or os.environ.get("LLM_API_KEY")
    return url, model, api_key


def enabled(url=None, model=None):
    """Fallback runs only when explicitly switched on AND an endpoint+model exist."""
    on = os.environ.get("LLM_FALLBACK", "").strip().lower() in ("1", "true", "yes", "on")
    u, m, _ = load_config(url, model)
    return bool(on and u and m)


# ---------------------------------------------------------------------------
# transport (OpenAI-compatible /v1/chat/completions)
# ---------------------------------------------------------------------------

def chat(url, model, api_key, system, user, timeout=60):
    """POST one chat completion, return the assistant message content (str)."""
    endpoint = url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# response parsing
# ---------------------------------------------------------------------------

def parse_json_flights(text):
    """Pull the JSON array out of a model reply, tolerating ```json fences and
    stray prose around it. Returns a list (possibly empty); never raises."""
    if not text:
        return []
    # strip a ```json ... ``` (or ``` ... ```) fence if present
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = fenced.group(1) if fenced else text
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        # last resort: grab the first [...] span
        m = re.search(r"\[.*\]", body, re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(obj, dict):                 # a lone object -> wrap it
        obj = [obj]
    return [x for x in obj if isinstance(x, dict)] if isinstance(obj, list) else []


def to_candidate(obj, source_file):
    """Map one LLM-extracted flight dict to the canonical candidate dict shape
    (same keys as flightparse.Candidate.to_dict), forced to LLM provenance and
    uncertain confidence so it can never auto-write."""
    airline_iata = (obj.get("airline_iata") or "").strip().upper() or None
    number = obj.get("flight_number")
    flight_number = None
    if airline_iata and number not in (None, ""):
        flight_number = flightparse.norm_flight_number(airline_iata, number)
    from_iata = (obj.get("from_iata") or "").strip().upper() or None
    to_iata = (obj.get("to_iata") or "").strip().upper() or None
    seat = obj.get("seat_number")
    seat = seat.strip().upper() if isinstance(seat, str) and seat.strip() else None
    date = (obj.get("date") or "") or None

    issues = ["LLM-parsed — verify before writing"]
    status = obj.get("status")
    if (status or "").lower() in ("cancelled", "canceled", "storniert"):
        issues.append(f"reservation status {status!r} — verify not a ghost")

    return {
        "flightNumber": flight_number,
        "airline": airdata.airline_icao(airline_iata),
        "airline_iata": airline_iata,
        "from": airdata.airport_icao(from_iata),
        "to": airdata.airport_icao(to_iata),
        "from_iata": from_iata,
        "to_iata": to_iata,
        "date": date[:10] if date else None,
        "departure": obj.get("departure") or None,
        "arrival": obj.get("arrival") or None,
        "seatNumber": seat,
        "seat": flightparse.seat_type(seat),
        "seatClass": flightparse.seat_class(obj.get("seat_class")),
        "flightReason": "leisure",
        "source_file": source_file,
        "extractor": "llm",
        "confidence": "uncertain",   # never "high": keeps it out of auto-write
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def extract_flights(body, source_file, url=None, model=None, api_key=None):
    """Ask the model for flights in `body`; return candidate dicts (may be empty)."""
    url, model, api_key = load_config(url, model, api_key)
    if not (url and model):
        return []
    reply = chat(url, model, api_key, SYSTEM_PROMPT, body[:12000])
    cands = []
    for obj in parse_json_flights(reply):
        cand = to_candidate(obj, source_file)
        # require at least a route or a flight number to be worth a human's time
        if cand["flightNumber"] or (cand["from_iata"] and cand["to_iata"]):
            cands.append(cand)
    return cands


def extract_file(path, url=None, model=None, api_key=None):
    """Load a Layover-saved .txt and run the fallback on its body."""
    _, body = flightparse.load_email(path)
    rel = os.path.basename(os.path.dirname(path)) + "/" + os.path.basename(path)
    return extract_flights(body, rel, url, model, api_key)
