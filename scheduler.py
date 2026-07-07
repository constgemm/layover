#!/usr/bin/env python3
"""
scheduler — self-contained runner for Layover (stdlib only).

Designed to be PID 1 in a long-running container: it sleeps until the next
scheduled slot, then runs the populate.py pipeline — incremental IMAP pull ->
parse -> AirTrail dedup -> digest — and loops forever. It NEVER writes to
AirTrail; writes stay manual (populate.py --commit).

Two modes:
    weekly   (default) — one slot a week, default Monday 07:00 local time.
    interval           — every SCAN_INTERVAL_MINUTES for a continuous scan.

No dependencies beyond the standard library. Local-time scheduling is DST-correct
when the container has tzdata and TZ is set (see the image / compose).

Environment:
    LAYOVER_OUT           output + state dir            (default: /data)
    ACCOUNTS_INI          IMAP config path              (default: accounts.ini)
    SCHEDULE_MODE         weekly | interval             (default: weekly)
    SCAN_INTERVAL_MINUTES minutes between runs (interval mode)  (default: 30)
    SCHEDULE_DOW          weekday, 0=Mon .. 6=Sun (weekly)       (default: 0)
    SCHEDULE_HOUR         hour of day, 0-23 local (weekly)       (default: 7)
    SCHEDULE_MINUTE       minute, 0-59 (weekly)                  (default: 0)
    RUN_ON_START          run once immediately at start          (default: true)
    TZ                    scheduling timezone           (e.g. Europe/Berlin)
AirTrail dedup config comes from AIRTRAIL_URL / AIRTRAIL_API_KEY (see airtrail.py).
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def next_interval_run(now, interval_minutes):
    """Next slot in interval mode: `now` + interval (at least one minute)."""
    return now + timedelta(minutes=max(1, interval_minutes))


def next_run(now, dow, hour, minute):
    """First datetime strictly after `now` matching weekday/hour/minute."""
    days_ahead = (dow - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) \
        + timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def run_once(out_dir, accounts_ini):
    """Invoke populate.py in digest-only mode (pull first if config is present)."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(app_dir, "populate.py"), out_dir,
           "-o", os.path.join(out_dir, "candidates.json")]
    if os.path.exists(accounts_ini):
        cmd += ["--pull", accounts_ini]
    else:
        print(f"[scheduler] {accounts_ini} not found — parsing existing "
              f"{out_dir} without a pull", flush=True)
    print(f"[scheduler] {datetime.now().isoformat(timespec='seconds')} running: "
          f"{' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=app_dir, check=False)


def main():
    out_dir = os.environ.get("LAYOVER_OUT", "/data")
    accounts_ini = os.environ.get("ACCOUNTS_INI", "accounts.ini")
    mode = os.environ.get("SCHEDULE_MODE", "weekly").strip().lower()
    interval = env_int("SCAN_INTERVAL_MINUTES", 30)
    dow = env_int("SCHEDULE_DOW", 0)
    hour = env_int("SCHEDULE_HOUR", 7)
    minute = env_int("SCHEDULE_MINUTE", 0)
    os.makedirs(out_dir, exist_ok=True)

    if mode == "interval":
        upcoming = lambda now: next_interval_run(now, interval)  # noqa: E731
        print(f"[scheduler] Layover — interval mode, every {max(1, interval)} min "
              f"(TZ={os.environ.get('TZ', 'system')})", flush=True)
    else:
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        upcoming = lambda now: next_run(now, dow, hour, minute)  # noqa: E731
        print(f"[scheduler] Layover — weekly on {days[dow % 7]} "
              f"{hour:02d}:{minute:02d} (TZ={os.environ.get('TZ', 'system')})",
              flush=True)

    if env_bool("RUN_ON_START", True):
        print("[scheduler] initial run on start", flush=True)
        try:
            run_once(out_dir, accounts_ini)
        except Exception as exc:  # noqa: BLE001 - never let one run kill the loop
            print(f"[scheduler] initial run failed: {exc}", flush=True)

    while True:
        now = datetime.now()
        nxt = upcoming(now)
        secs = max(1.0, (nxt - now).total_seconds())
        print(f"[scheduler] next run {nxt.isoformat(timespec='seconds')} "
              f"(in {secs / 60:.0f} min)", flush=True)
        time.sleep(secs)
        try:
            run_once(out_dir, accounts_ini)
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] run failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
