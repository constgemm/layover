#!/usr/bin/env python3
"""
notify — optional notification / webhook for Layover (stdlib only).

Fires ONLY when a run finds genuinely new flights (candidates classified `new`
after AirTrail dedup) — never for duplicates or uncertain rows, and never on an
empty run. Two independent, both-optional sinks:

    WEBHOOK_URL   HTTP POST of a JSON payload (Slack/Discord/Telegram-bot/n8n/…)
    NOTIFY_CMD    shell command the text summary is piped to (mail, telegram-send…)

If neither is configured, notification is a no-op. Network/exec failures are
swallowed with a log line so a notifier problem never breaks the pipeline.
"""

import json
import subprocess
import sys
import urllib.request


def _new_only(cands):
    return [c for c in cands if c.get("status") == "new"]


def build_notification(cands):
    """Build a JSON-serialisable payload from classified candidates (new only)."""
    new = _new_only(cands)
    counts = {
        "new": len(new),
        "uncertain": sum(c.get("status") == "uncertain" for c in cands),
        "duplicate": sum(c.get("status") == "duplicate" for c in cands),
    }
    lines = [f"{c.get('date')} {c.get('flightNumber')} "
             f"{c.get('from') or c.get('from_iata')}->{c.get('to') or c.get('to_iata')}"
             f"{(' seat ' + c['seatNumber']) if c.get('seatNumber') else ''}"
             for c in new]
    summary = f"{counts['new']} new flight(s), {counts['uncertain']} uncertain"
    text = "Layover — " + summary + ("\n" + "\n".join(lines) if lines else "")
    return {
        "source": "layover",
        "summary": summary,
        "counts": counts,
        "new": [{
            "date": c.get("date"),
            "flightNumber": c.get("flightNumber"),
            "airline": c.get("airline"),
            "from": c.get("from") or c.get("from_iata"),
            "to": c.get("to") or c.get("to_iata"),
            "departure": c.get("departure"),
            "seatNumber": c.get("seatNumber"),
        } for c in new],
        "text": text,
    }


def send_webhook(url, payload, timeout=15):
    """POST payload as JSON. Returns (status, body); raises on transport error."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode(errors="replace")


def run_notify_cmd(cmd, text):
    """Pipe the text summary into a shell command's stdin."""
    proc = subprocess.run(cmd, shell=True, input=text, text=True,
                          capture_output=True)
    return proc.returncode


def notify_new(cands, webhook_url=None, notify_cmd=None):
    """Send notifications for new candidates. No-op if nothing new or no sink.

    Returns True if at least one sink was invoked."""
    payload = build_notification(cands)
    if payload["counts"]["new"] == 0:
        return False
    sent = False
    if webhook_url:
        try:
            status, _ = send_webhook(webhook_url, payload)
            print(f"[notify] webhook POST -> HTTP {status}", flush=True)
            sent = True
        except Exception as exc:  # noqa: BLE001 - notifier must not break the run
            print(f"[notify] webhook failed: {exc}", file=sys.stderr, flush=True)
    if notify_cmd:
        try:
            rc = run_notify_cmd(notify_cmd, payload["text"])
            print(f"[notify] NOTIFY_CMD exit {rc}", flush=True)
            sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] NOTIFY_CMD failed: {exc}", file=sys.stderr, flush=True)
    return sent
