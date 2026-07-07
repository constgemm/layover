#!/usr/bin/env python3
"""
Layover — pull flight-related emails from IMAP mailboxes, text-only.

Sweeps one or more IMAP accounts (Gmail / Google Workspace / IONOS / any IMAP host),
filters message HEADERS locally against airline & OTA sender domains + booking
keywords (English + German), and downloads the full text — and PDF attachments —
of the matches only. No browser, no screenshots, no third-party dependencies:
Python 3.9+ standard library.

Runs are INCREMENTAL by default: Layover records the highest UID it has seen per
(account, folder) in <out>/state.json and, on the next run, fetches only newer
messages. Pass --full to ignore the state and re-scan the whole date window.

Usage:
    python3 layover.py accounts.ini out/            # incremental (default)
    python3 layover.py accounts.ini out/ --full     # force a full re-scan

Config: one [section] per account — see accounts.example.ini.

Output: <out>/<account>/<uid>_<subject>.txt (headers + text body), matching
<uid>_<subject>_N.pdf attachments, an appended <out>/index.csv, and state.json.
"""

import configparser
import csv
import email
import email.policy
import imaplib
import json
import re
import sys
from email.header import decode_header
from pathlib import Path

# --- what counts as a flight email (matched on From OR Subject, case-insensitive) ---

SENDER_RE = re.compile(
    r"(swiss(air|\.com)?|lufthansa|edelweiss|austrian|eurowings|germanwings|airberlin"
    r"|easyjet|condor|iberia|qatarairways|flytap|tap-?portugal|united\.com|unitedairlines"
    r"|aa\.com|americanairlines|aegean|klm|airfrance|british-?airways|ba\.com"
    r"|turkishairlines|emirates|etihad|ryanair|wizzair|vueling|norwegian|flysas|sas\.se"
    r"|brusselsairlines|airbaltic|airdolomiti|helvetic|chair\.ch|tuifly|sunexpress"
    r"|pegasus|niki\.|flyniki|icelandair|finnair|lot\.com|lotpolish|croatia(airlines)?"
    r"|olympicair|luxair|transavia|hop!?\.|delta\.com|jetblue|southwest|alaskaair"
    r"|aircanada|westjet|latam|avianca|copaair|singaporeair|cathay|ethiopian|egyptair"
    r"|skywest|airlink"
    r"|ebookers|expedia|opodo|kiwi\.com|lastminute|bravofly|edreams|gotogate"
    r"|travelocity|orbitz|cheaptickets|tripit|kuoni|hotelplan|dertour|migros-?ferien"
    r"|travel\.ch|flightright|checkfelix|swoodoo|fluege\.de|travelstart)",
    re.I,
)

SUBJECT_RE = re.compile(
    r"(flight|flug(schein|ticket|plan|bestätigung)?|boarding|bordkarte|e-?ticket"
    r"|itinerar|reiseplan|reisebestätigung|buchungsbest|booking\s?confirmation"
    r"|ihre\s(reise|buchung)|your\s(trip|booking|flight)|check-?in|abflug|departure"
    r"|fluggesellschaft|\bpnr\b|reiseroute|travel\s?(document|confirmation)"
    r"|eingecheckt|réservation)",
    re.I,
)

SKIP_FOLDER_RE = re.compile(
    r"(drafts?|entw(ü|=FC)rfe|trash|papierkorb|gel(ö|=F6)scht|deleted|spam|junk"
    r"|\[gmail\]$|outbox)", re.I,
)

BATCH = 200  # header-fetch chunk size


def dec(value):
    """Decode an RFC2047 header to a plain string, tolerating bogus charsets."""
    if not value:
        return ""
    out = []
    for part, cs in decode_header(value):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(cs or "utf-8", errors="replace"))
            except LookupError:  # e.g. "unknown-8bit"
                out.append(part.decode("latin-1", errors="replace"))
        else:
            out.append(part)
    return " ".join(" ".join(out).split())


def list_folders(m, spec):
    """Resolve which folders to sweep. 'auto' = Gmail All-Mail if present, else all."""
    _, data = m.list()
    boxes, all_mail = [], None
    for line in data or []:
        line = line.decode(errors="replace")
        match = re.match(r'\((?P<flags>[^)]*)\)\s+"?(?P<sep>[^"\s]+)"?\s+(?P<name>.+)$', line)
        if not match:
            continue
        name = match.group("name").strip().strip('"')
        if "\\All" in match.group("flags"):
            all_mail = name
        if "\\Noselect" in match.group("flags"):
            continue
        boxes.append(name)
    if spec and spec != "auto":
        return [f.strip() for f in spec.split(",") if f.strip()]
    if all_mail:
        return [all_mail]
    return [b for b in boxes if not SKIP_FOLDER_RE.search(b)]


def body_text(msg):
    """Best-effort plain text from a parsed email message."""
    texts = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, ValueError, KeyError):  # bogus/unknown charset
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("latin-1", errors="replace")
        if ctype == "text/plain":
            texts.append(content)
        elif ctype == "text/html" and not texts:
            texts.append(re.sub(r"<[^>]+>", " ", content))
    return re.sub(r"[ \t]+", " ", "\n".join(texts))


def pdf_parts(msg):
    for part in msg.walk():
        fname = part.get_filename()
        if fname and fname.lower().endswith(".pdf"):
            yield dec(fname), part.get_payload(decode=True)


def uidvalidity(m):
    _, data = m.response("UIDVALIDITY")
    try:
        return int(data[0])
    except (TypeError, ValueError, IndexError):
        return None


def sweep_account(name, cfg, out_root, state, index_writer, full=False):
    host, user, pw = cfg["host"], cfg["user"], cfg["password"]
    print(f"\n=== {name} ({user} @ {host}) ===")
    m = imaplib.IMAP4_SSL(host)
    m.login(user, pw)

    window = ["SINCE", cfg["since"]]
    if cfg.get("before"):
        window += ["BEFORE", cfg["before"]]

    out_dir = out_root / name
    out_dir.mkdir(parents=True, exist_ok=True)
    total_scanned = total_hits = 0

    for folder in list_folders(m, cfg.get("folders", "auto")):
        try:
            typ, _ = m.select(f'"{folder}"', readonly=True)
        except imaplib.IMAP4.error:
            continue
        if typ != "OK":
            continue

        key = f"{name}\x1f{folder}"
        uv = uidvalidity(m)
        prev = state.get(key)
        incremental = (not full and prev and prev.get("uidvalidity") == uv
                       and prev.get("last_uid"))

        if incremental:
            last = prev["last_uid"]
            typ, data = m.uid("SEARCH", None, "UID", f"{last + 1}:*")
            uids = [u for u in (data[0].split() if typ == "OK" and data and data[0] else [])
                    if int(u) > last]
        else:
            typ, data = m.uid("SEARCH", None, *window)
            uids = data[0].split() if typ == "OK" and data and data[0] else []

        if not uids:
            continue
        max_uid = max(int(u) for u in uids)
        print(f"  {folder}: {len(uids)} "
              f"{'new ' if incremental else ''}messages to scan")

        hits = []
        for i in range(0, len(uids), BATCH):
            chunk = b",".join(uids[i:i + BATCH]).decode()
            typ, resp = m.uid(
                "FETCH", chunk, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK":
                continue
            for item in resp:
                if not isinstance(item, tuple):
                    continue
                meta, raw = item
                um = re.search(rb"UID (\d+)", meta)
                if not um:
                    continue
                hdr = email.message_from_bytes(raw)
                frm, subj = dec(hdr.get("From")), dec(hdr.get("Subject"))
                total_scanned += 1
                if SENDER_RE.search(frm) or SUBJECT_RE.search(subj):
                    hits.append((um.group(1).decode(), frm, subj, dec(hdr.get("Date"))))
            print(f"    scanned {min(i + BATCH, len(uids))}/{len(uids)}, "
                  f"hits: {len(hits)}", end="\r")
        print()

        for uid, frm, subj, date in hits:
            typ, resp = m.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not resp or not isinstance(resp[0], tuple):
                continue
            msg = email.message_from_bytes(resp[0][1], policy=email.policy.default)
            total_hits += 1
            slug = re.sub(r"[^A-Za-z0-9]+", "-", subj)[:60].strip("-") or "no-subject"
            base = f"{uid}_{slug}"          # UID-named -> stable & idempotent across runs
            pdfs = []
            for pi, (pdf_name, blob) in enumerate(pdf_parts(msg)):
                if blob:
                    (out_dir / f"{base}_{pi}.pdf").write_bytes(blob)
                    pdfs.append(pdf_name)
            txt = (f"Account: {name}\nFolder: {folder}\nUID: {uid}\nDate: {date}\n"
                   f"From: {frm}\nSubject: {subj}\nPDFs: {'; '.join(pdfs)}\n\n"
                   + body_text(msg))
            (out_dir / f"{base}.txt").write_text(txt, errors="replace")
            index_writer.writerow([name, folder, uid, date, frm, subj,
                                   "yes" if pdfs else "", f"{name}/{base}.txt"])

        # advance the watermark for this folder (all scanned msgs, not just hits)
        state[key] = {"uidvalidity": uv,
                      "last_uid": max(max_uid, (prev or {}).get("last_uid", 0))}

    m.logout()
    print(f"  -> {name}: {total_scanned} headers scanned, {total_hits} flight "
          f"candidates saved to {out_dir}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    full = "--full" in sys.argv
    if len(args) != 2:
        sys.exit(__doc__)
    cfg = configparser.ConfigParser(interpolation=None)  # passwords may contain %, $, ...
    if not cfg.read(args[0]):
        sys.exit(f"config not found: {args[0]}")
    out_root = Path(args[1]).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)

    state_path = out_root / "state.json"
    state = {}
    if state_path.exists() and not full:
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            state = {}

    index_path = out_root / "index.csv"
    write_header = not index_path.exists() or index_path.stat().st_size == 0
    with open(index_path, "a", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["account", "folder", "uid", "date", "from", "subject",
                             "has_pdf", "file"])
        for section in cfg.sections():
            try:
                sweep_account(section, cfg[section], out_root, state, writer, full=full)
            except Exception as exc:  # noqa: BLE001 - one bad account must not abort the rest
                hint = ""
                low = str(exc).lower()
                if any(s in low for s in ("application-specific", "app password",
                                          "invalid credentials", "authenticationfailed")):
                    hint = ("  (Google 2FA account: use a 16-char *app password*, not the "
                            "login password — https://myaccount.google.com/apppasswords; "
                            "app passwords need 2-Step Verification ON. IONOS uses the "
                            "plain mailbox password.)")
                print(f"  !! {section}: {exc}{hint} — skipping, continuing with the rest")
            finally:
                state_path.write_text(json.dumps(state, indent=2))  # persist per account

    print(f"\nDone. Index: {index_path}  |  State: {state_path}")


if __name__ == "__main__":
    main()
