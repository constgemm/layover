#!/usr/bin/env bash
# Layover weekly run: incremental IMAP pull -> deterministic parse -> AirTrail
# dedup -> digest. NEVER writes to AirTrail (writes are manual: populate.py --commit).
#
# Wire it up with systemd (cron/layover-weekly.{service,timer}) on airtrail-host, or with
# launchd (cron/com.example.layover.weekly.plist) on macOS, or a plain crontab:
#     0 7 * * 1  /path/to/layover/cron/layover-weekly.sh
#
# Config via env (all optional except that accounts.ini must exist):
#   LAYOVER_DIR    repo dir            (default: this script's parent)
#   LAYOVER_OUT    pull output dir     (default: $LAYOVER_DIR/flight-mail-out)
#   ACCOUNTS_INI   IMAP config         (default: $LAYOVER_DIR/accounts.ini)
#   LAYOVER_LOG    log file            (default: $LAYOVER_OUT/weekly.log)
#   NOTIFY_CMD     command the digest is piped to (e.g. a Telegram/mail sender)
# AirTrail dedup uses airtrail.ini / AIRTRAIL_URL+AIRTRAIL_API_KEY (see airtrail.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAYOVER_DIR="${LAYOVER_DIR:-$(dirname "$SCRIPT_DIR")}"
LAYOVER_OUT="${LAYOVER_OUT:-$LAYOVER_DIR/flight-mail-out}"
ACCOUNTS_INI="${ACCOUNTS_INI:-$LAYOVER_DIR/accounts.ini}"
LAYOVER_LOG="${LAYOVER_LOG:-$LAYOVER_OUT/weekly.log}"
PYTHON="${PYTHON:-python3}"

cd "$LAYOVER_DIR"
mkdir -p "$LAYOVER_OUT"

stamp="$(date '+%Y-%m-%d %H:%M:%S')"
{
  echo
  echo "######## layover weekly run: $stamp ########"
} >> "$LAYOVER_LOG"

# Pull + parse + dedup + digest. --pull runs the incremental IMAP sweep first.
# Digest goes to stdout; capture it so we can both log and (optionally) notify.
digest="$("$PYTHON" populate.py "$LAYOVER_OUT" \
            --pull "$ACCOUNTS_INI" \
            -o "$LAYOVER_OUT/candidates.json" 2>>"$LAYOVER_LOG")"

echo "$digest" | tee -a "$LAYOVER_LOG"

# Optional push notification: pipe the digest to whatever NOTIFY_CMD is set to.
if [[ -n "${NOTIFY_CMD:-}" ]]; then
  echo "$digest" | eval "$NOTIFY_CMD" || echo "notify failed" >> "$LAYOVER_LOG"
fi
