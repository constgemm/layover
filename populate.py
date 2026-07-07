#!/usr/bin/env python3
"""
populate — Layover auto-population orchestrator (Phase 1, propose-then-approve).

Ties the pieces together for a weekly run:

    (optional) incremental IMAP pull   ->  flightparse candidates
        ->  dedup against AirTrail (GET /api/flight/list)
        ->  DIGEST:  "N new, M uncertain, K already in AirTrail"

It never writes to AirTrail on its own. Writing is available only via the
explicit, interactive --commit flag, which asks for a typed 'yes' per flight.
The cron entry point (cron/) runs it in digest-only mode.

Usage:
    # digest only (what a weekly cron runs), dedup against live AirTrail:
    python3 populate.py flight-mail-out/

    # also run the incremental IMAP pull first:
    python3 populate.py flight-mail-out/ --pull accounts.ini

    # offline dedup against a saved flight list dump (no API key needed):
    python3 populate.py flight-mail-out/ --flights-json airtrail-flights.json

    # save the candidate JSON for review:
    python3 populate.py flight-mail-out/ -o candidates.json

    # interactively write the NEW ones (asks yes per flight; never automatic):
    python3 populate.py flight-mail-out/ --commit

Config for the AirTrail API: airtrail.ini / env (see airtrail.py). If AirTrail is
unreachable and no --flights-json is given, the run still produces candidates but
marks them 'unverified' (dedup skipped) rather than failing.
"""

import argparse
import os
import subprocess
import sys
from collections import defaultdict

import airtrail
import flightparse
import notify as notify_mod


def pull(accounts_ini, out_dir):
    print(f"[pull] python3 layover.py {accounts_ini} {out_dir}", file=sys.stderr)
    subprocess.run([sys.executable, "layover.py", accounts_ini, out_dir], check=True)


def parse_candidates(out_dir):
    raw = []
    for path in flightparse.iter_paths([out_dir]):
        try:
            raw += [c.to_dict() for c in flightparse.parse_file(path)]
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {path}: {exc}", file=sys.stderr)
    cands = flightparse.dedup_candidates(raw)
    cands.sort(key=lambda d: (d.get("date") or "", d.get("flightNumber") or ""))
    return cands


def get_existing(args):
    """Return (keys_set, source_label) or (None, reason) if unavailable."""
    if args.flights_json:
        try:
            fl = airtrail.load_flights_json(args.flights_json)
            return airtrail.existing_keys(fl), f"{len(fl)} flights ({args.flights_json})"
        except Exception as exc:  # noqa: BLE001
            return None, f"could not read {args.flights_json}: {exc}"
    url, key = airtrail.load_config(args.url, args.api_key)
    if not url or not key:
        return None, "no AirTrail url/api_key configured (set airtrail.ini or env)"
    try:
        fl = airtrail.fetch_flights(url, key)
        return airtrail.existing_keys(fl), f"{len(fl)} flights ({url})"
    except Exception as exc:  # noqa: BLE001
        return None, f"AirTrail unreachable: {exc}"


def classify(cands, existing):
    """Annotate each candidate with 'status' new/duplicate/uncertain/unverified."""
    for c in cands:
        if c["confidence"] != "high":
            c["status"] = "uncertain"
        elif existing is None:
            c["status"] = "unverified"
        elif airtrail.flight_key(c["flightNumber"], c["date"]) in existing:
            c["status"] = "duplicate"
        else:
            c["status"] = "new"
    return cands


def find_rebookings(cands):
    """Flag same route + same date but different flight number (possible rebooking
    / cancellation — the classic ghost-flight trap the human gate exists for)."""
    by_rd = defaultdict(list)
    for c in cands:
        if c["from"] and c["to"] and c["date"]:
            by_rd[(c["from"], c["to"], c["date"])].append(c)
    warnings = []
    for (frm, to, date), group in by_rd.items():
        nums = {c["flightNumber"] for c in group}
        if len(nums) > 1:
            warnings.append((frm, to, date, sorted(nums)))
            for c in group:
                c.setdefault("issues", []).append(
                    "same route+date as " + ", ".join(sorted(nums - {c["flightNumber"]})
                    ) + " — possible rebooking/cancellation")
    return warnings


def digest(cands, source_label, rebookings):
    buckets = defaultdict(list)
    for c in cands:
        buckets[c["status"]].append(c)
    n_new = len(buckets["new"])
    n_unc = len(buckets["uncertain"])
    n_dup = len(buckets["duplicate"])
    n_unv = len(buckets["unverified"])

    print("\n" + "=" * 66)
    print("LAYOVER — weekly flight digest")
    print("=" * 66)
    print(f"AirTrail: {source_label}")
    head = f"{n_new} new"
    if n_unv:
        head = f"{n_unv} parsed (unverified — AirTrail not consulted)"
    print(f"{head}, {n_unc} uncertain, {n_dup} already in AirTrail\n")

    def show(title, items):
        if not items:
            return
        print(f"--- {title} ({len(items)}) ---")
        for c in items:
            seat = f" seat {c['seatNumber']}" if c.get("seatNumber") else ""
            line = (f"  {c['date']}  {(c['flightNumber'] or '?'):8} "
                    f"{c['from'] or c['from_iata']}->{c['to'] or c['to_iata']}"
                    f"  {(c['departure'] or '')[:16]}{seat}  [{c['extractor']}]")
            print(line)
            for issue in c.get("issues", []):
                print(f"        ! {issue}")
        print()

    show("NEW — candidates to review", buckets["new"])
    show("PARSED (unverified)", buckets["unverified"])
    show("UNCERTAIN — needs a human", buckets["uncertain"])
    if rebookings:
        print(f"--- possible rebookings/cancellations ({len(rebookings)}) ---")
        for frm, to, date, nums in rebookings:
            print(f"  {date}  {frm}->{to}: {', '.join(nums)}")
        print()
    print(f"{n_dup} already in AirTrail (hidden). Nothing was written.")
    print("Review, then: python3 populate.py <out> --commit   (writes on typed yes)")


def _blocked_reason(c):
    """Why a NEW candidate must NOT be auto-written (None = safe to auto-write)."""
    if c.get("confidence") != "high":
        return "not high-confidence"
    for issue in c.get("issues", []):
        low = issue.lower()
        if "rebooking" in low or "cancel" in low or "ghost" in low:
            return issue
    if not (c.get("from") and c.get("to") and c.get("flightNumber") and c.get("date")):
        return "missing required field"
    return None


def auto_writable(cands):
    """NEW candidates safe to write unattended: high-confidence, complete, and not
    flagged as a possible rebooking/cancellation."""
    return [c for c in cands
            if c.get("status") == "new" and _blocked_reason(c) is None]


def commit(cands, args):
    """Interactive write of NEW flights — asks yes per flight."""
    url, key = airtrail.load_config(args.url, args.api_key)
    if not url or not key:
        sys.exit("--commit needs AirTrail url + api_key (airtrail.ini or env).")
    to_write = [c for c in cands if c["status"] == "new"]
    if not to_write:
        print("Nothing new to write.")
        return
    print(f"\n{len(to_write)} new flight(s). Confirm each (type 'yes' to write):\n")
    for c in to_write:
        payload = airtrail.build_payload(c, user_id=args.user_id)
        blocked = _blocked_reason(c)
        flag = f"   ⚠ {blocked}" if blocked else ""
        print(f"  {c['date']} {c['flightNumber']} {c['from']}->{c['to']} "
              f"{(c['departure'] or '')[:16]}{flag}")
        if input("    write to AirTrail? [yes/N] ").strip().lower() != "yes":
            print("    skipped.")
            continue
        try:
            status, _ = airtrail.post_flight(url, key, payload)
            print(f"    written (HTTP {status}).")
        except Exception as exc:  # noqa: BLE001
            print(f"    !! failed: {exc}")


def auto_write(cands, args):
    """Unattended write of the safe subset (opt-in). Skips anything uncertain or
    flagged as a rebooking/cancellation — those still need a human (--commit)."""
    writable = auto_writable(cands)
    held = [c for c in cands if c.get("status") == "new" and c not in writable]
    print(f"\n[auto-write] {len(writable)} safe to write, {len(held)} held for review.")
    if args.dry_run:
        for c in writable:
            print(f"  would write: {c['date']} {c['flightNumber']} "
                  f"{c['from']}->{c['to']}")
        print("[auto-write] dry-run — nothing written.")
        return
    url, key = airtrail.load_config(args.url, args.api_key)
    if not url or not key:
        sys.exit("--auto-write needs AirTrail url + api_key (airtrail.ini or env).")
    for c in writable:
        payload = airtrail.build_payload(c, user_id=args.user_id)
        try:
            status, _ = airtrail.post_flight(url, key, payload)
            print(f"  wrote {c['date']} {c['flightNumber']} {c['from']}->{c['to']} "
                  f"(HTTP {status})")
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {c['flightNumber']} failed: {exc}")
    for c in held:
        print(f"  held: {c['date']} {c['flightNumber']} — {_blocked_reason(c)}")


def main():
    ap = argparse.ArgumentParser(description="Layover auto-population digest.")
    ap.add_argument("out_dir", help="Layover output dir (e.g. flight-mail-out/)")
    ap.add_argument("--pull", metavar="ACCOUNTS_INI",
                    help="run the incremental IMAP pull first")
    ap.add_argument("--flights-json", help="offline AirTrail flight-list dump")
    ap.add_argument("--url", help="AirTrail base URL (overrides env/ini)")
    ap.add_argument("--api-key", help="AirTrail API key (overrides env/ini)")
    ap.add_argument("-o", "--out", help="write candidate JSON here")
    ap.add_argument("--commit", action="store_true",
                    help="interactively write NEW flights (asks yes per flight)")
    ap.add_argument("--auto-write", action="store_true",
                    help="unattended write of the safe subset (high-confidence, "
                         "non-rebooking NEW flights); opt-in, also via AUTO_WRITE=1")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --auto-write, only print what would be written")
    ap.add_argument("--webhook-url", help="POST a JSON digest here when new flights "
                    "are found (or set WEBHOOK_URL)")
    ap.add_argument("--user-id", help="AirTrail user id to assign seats to on writes")
    args = ap.parse_args()

    if args.pull:
        pull(args.pull, args.out_dir)

    cands = parse_candidates(args.out_dir)
    existing, source_label = get_existing(args)
    classify(cands, existing)
    rebookings = find_rebookings(cands)

    if args.out:
        import json
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(cands, indent=2, ensure_ascii=False) + "\n")
        print(f"[candidates] {len(cands)} -> {args.out}", file=sys.stderr)

    digest(cands, source_label, rebookings)

    # Optional notification — fires only when there are genuinely new flights.
    notify_mod.notify_new(
        cands,
        webhook_url=args.webhook_url or os.environ.get("WEBHOOK_URL"),
        notify_cmd=os.environ.get("NOTIFY_CMD"),
    )

    if args.commit:
        commit(cands, args)
    elif args.auto_write or os.environ.get("AUTO_WRITE", "").strip().lower() in (
            "1", "true", "yes", "on"):
        auto_write(cands, args)


if __name__ == "__main__":
    main()
