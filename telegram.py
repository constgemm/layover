#!/usr/bin/env python3
"""
telegram — interactive per-flight approval over a Telegram bot (Phase 4).

The last gate before a write. For each NEW candidate that Dawarich didn't
contradict, it sends you a message showing every field (missing ones flagged) with
an inline "✅ Approve / ⏭ Skip" keyboard, then waits for your tap. On Approve it
writes the flight to AirTrail and replies with the new total ("AirTrail now has N
flights"). On Skip (or timeout) it writes nothing.

This is the human in propose-then-approve, moved from the terminal (`--commit`) to
your phone. It uses long-poll getUpdates, so the container needs no inbound port.

Config (env, or populate.py flags):
    TELEGRAM_BOT_TOKEN   bot token from @BotFather   (SECRET — .env, never committed)
    TELEGRAM_CHAT_ID     chat/user id to message and accept taps from
    TELEGRAM_APPROVE     truthy to enable the approval step

Zero dependencies (urllib + json). All HTTP is injectable for testing.
"""

import json
import os
import time
import urllib.request

import airtrail

API = "https://api.telegram.org"
PREFIX = "lay"           # callback_data namespace so we ignore other bots' taps


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def load_config(token=None, chat_id=None):
    return (token or os.environ.get("TELEGRAM_BOT_TOKEN"),
            chat_id or os.environ.get("TELEGRAM_CHAT_ID"))


def enabled(token=None, chat_id=None):
    on = os.environ.get("TELEGRAM_APPROVE", "").strip().lower() in (
        "1", "true", "yes", "on")
    t, c = load_config(token, chat_id)
    return bool(on and t and c)


# ---------------------------------------------------------------------------
# transport (Telegram Bot API) — thin, and injectable via `api`
# ---------------------------------------------------------------------------

def api_call(token, method, params=None, timeout=35):
    url = f"{API}/bot{token}/{method}"
    data = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def send_message(token, chat_id, text, reply_markup=None, api=api_call):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup:
        params["reply_markup"] = reply_markup
    return api(token, "sendMessage", params)


def get_updates(token, offset=None, timeout=25, api=api_call):
    params = {"timeout": timeout, "allowed_updates": ["callback_query"]}
    if offset is not None:
        params["offset"] = offset
    return api(token, "getUpdates", params, timeout=timeout + 10).get("result", [])


def answer_callback(token, cb_id, text=None, api=api_call):
    params = {"callback_query_id": cb_id}
    if text:
        params["text"] = text
    return api(token, "answerCallbackQuery", params)


# ---------------------------------------------------------------------------
# message rendering
# ---------------------------------------------------------------------------

def _val(v):
    return str(v) if v not in (None, "", []) else "⚠ missing"


def format_candidate(c):
    """A human-readable card of every field, missing ones flagged."""
    airline = c.get("airline")
    if airline and c.get("airline_iata"):
        airline = f"{airline} ({c['airline_iata']})"
    frm = c.get("from")
    if frm and c.get("from_iata"):
        frm = f"{frm} ({c['from_iata']})"
    to = c.get("to")
    if to and c.get("to_iata"):
        to = f"{to} ({c['to_iata']})"
    seat = c.get("seatNumber")
    if seat and c.get("seat"):
        seat = f"{seat} ({c['seat']})"
    loc = {"confirmed": "📍 confirmed by Dawarich",
           "contradicted": "📍 NOT seen near airport (!)",
           "unknown": "location unverified"}.get(c.get("location"), "not checked")

    lines = [
        "✈️ New flight — approve?",
        f"Flight:     {_val(c.get('flightNumber'))}",
        f"Airline:    {_val(airline)}",
        f"From:       {_val(frm)}",
        f"To:         {_val(to)}",
        f"Date:       {_val(c.get('date'))}",
        f"Departure:  {_val(c.get('departure'))}",
        f"Arrival:    {_val(c.get('arrival'))}",
        f"Seat:       {_val(seat)}",
        f"Class:      {_val(c.get('seatClass'))}",
        f"Location:   {loc}",
        f"Source:     {_val(c.get('source_file'))}  [{c.get('extractor')}]",
    ]
    for issue in c.get("issues", []):
        lines.append(f"⚠ {issue}")
    return "\n".join(lines)


def approval_keyboard(idx):
    return {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"{PREFIX}:ok:{idx}"},
        {"text": "⏭ Skip", "callback_data": f"{PREFIX}:no:{idx}"},
    ]]}


# ---------------------------------------------------------------------------
# approval session
# ---------------------------------------------------------------------------

def eligible(cands):
    """NEW candidates worth asking about: not a Dawarich contradiction."""
    return [c for c in cands
            if c.get("status") == "new" and c.get("location") != "contradicted"]


class ApprovalBot:
    """Sends approval cards and collects button taps via long-poll getUpdates.

    Buttons carry `lay:ok:<idx>` / `lay:no:<idx>`, so taps are matched to the exact
    card and stray taps from other bots in the chat are ignored. Decisions are
    buffered by index, so an out-of-order tap is never lost."""

    def __init__(self, token, chat_id, api=api_call):
        self.token = token
        self.chat_id = chat_id
        self.api = api
        self.offset = None
        self.decisions = {}

    def send(self, text, keyboard=None):
        return send_message(self.token, self.chat_id, text, keyboard, api=self.api)

    def prime(self):
        """Drain any pending updates so an old tap doesn't leak into this run."""
        for u in get_updates(self.token, None, 0, api=self.api):
            self.offset = u["update_id"] + 1

    def _pump(self, timeout):
        for u in get_updates(self.token, self.offset, timeout, api=self.api):
            self.offset = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq:
                continue
            parts = (cq.get("data") or "").split(":")
            if len(parts) == 3 and parts[0] == PREFIX:
                verb, idx = parts[1], parts[2]
                self.decisions[idx] = "approve" if verb == "ok" else "skip"
                answer_callback(self.token, cq["id"],
                                "Recorded ✅" if verb == "ok" else "Skipped",
                                api=self.api)

    def wait(self, idx, timeout_s=600, poll=25):
        """Block until the card `idx` is tapped, or timeout. Returns
        'approve' / 'skip' / None (timeout)."""
        idx = str(idx)
        waited = 0
        while idx not in self.decisions and waited < timeout_s:
            self._pump(poll)
            waited += poll
        return self.decisions.get(idx)


def _flight_count(fetch_flights, url, key):
    try:
        return len(fetch_flights(url, key))
    except Exception:  # noqa: BLE001 - count is a nicety, never fail the write on it
        return None


def run_approvals(cands, airtrail_url, airtrail_key, token, chat_id, user_id=None,
                  timeout_s=600, poll=25, api=api_call,
                  post_flight=airtrail.post_flight,
                  fetch_flights=airtrail.fetch_flights,
                  build_payload=airtrail.build_payload):
    """Ask about each eligible NEW flight and write the approved ones. Returns the
    number written. Sends one card at a time and waits for its tap."""
    items = eligible(cands)
    if not items:
        return 0
    if not (airtrail_url and airtrail_key):
        send_message(token, chat_id,
                     "Layover: AirTrail URL/key missing — cannot write approvals.",
                     api=api)
        return 0
    bot = ApprovalBot(token, chat_id, api=api)
    bot.prime()
    bot.send(f"Layover found {len(items)} new flight(s) to review.")
    written = 0
    for i, c in enumerate(items):
        bot.send(format_candidate(c), approval_keyboard(i))
        decision = bot.wait(i, timeout_s, poll)
        if decision == "approve":
            try:
                status, _ = post_flight(airtrail_url, airtrail_key,
                                        build_payload(c, user_id=user_id))
                written += 1
                n = _flight_count(fetch_flights, airtrail_url, airtrail_key)
                tail = f" AirTrail now has {n} flights." if n is not None else ""
                bot.send(f"✅ Wrote {c.get('flightNumber')} "
                         f"{c.get('from')}->{c.get('to')} (HTTP {status}).{tail}")
            except Exception as exc:  # noqa: BLE001
                bot.send(f"⚠ Failed to write {c.get('flightNumber')}: {exc}")
        elif decision == "skip":
            bot.send(f"⏭ Skipped {c.get('flightNumber')}.")
        else:
            bot.send(f"⌛ No response for {c.get('flightNumber')} — left for later.")
    return written
