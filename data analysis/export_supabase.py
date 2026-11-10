#!/usr/bin/env python3
"""Export GroundTruth IMU readings from Supabase to CSV.

Usage (run from anywhere):
    python3 "data analysis/export_supabase.py"            # latest session only
    python3 "data analysis/export_supabase.py" --all      # every session + combined all_readings.csv
    python3 "data analysis/export_supabase.py" --session 2026-11-07 2   # one specific session

Output layout (inside this folder):
    data analysis/
      all_readings.csv                         (only with --all)
      sessions/
        2026-11-07/
          session_01.csv
          session_02.csv

Credentials default to the same project/publishable key the iPhone app and web dashboard use
(groundtruth_app/RoadReader/Config/SupabaseConfig.swift). Override with env vars
SUPABASE_URL / SUPABASE_KEY if needed. Only needs the `requests` package.
"""
import argparse
import csv
import os
import sys
from pathlib import Path

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://vycrslqwsirgajgatatl.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_fhuQUhehrMS1YeC3O95OJA_1ntsPe-e")
TABLE = os.environ.get("SUPABASE_TABLE", "readings")
PAGE_SIZE = 1000  # PostgREST caps responses at 1000 rows by default

COLUMNS = [
    "id", "session_date", "session_number", "recorded_at",
    "ax", "ay", "az", "gx", "gy", "gz",
    "latitude", "longitude", "anomaly_detected",
]

HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE / "sessions"

HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def fetch(params):
    """Fetch every row matching `params`, following PostgREST pagination."""
    rows = []
    offset = 0
    while True:
        headers = dict(HEADERS, Range=f"{offset}-{offset + PAGE_SIZE - 1}", **{"Range-Unit": "items"})
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{TABLE}", headers=headers, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def latest_session():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABLE}",
        headers=HEADERS,
        params={"select": "session_date,session_number",
                "order": "session_date.desc,session_number.desc", "limit": 1},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        sys.exit("No readings in the table.")
    return data[0]["session_date"], int(data[0]["session_number"])


def fetch_session(date, number):
    return fetch({"select": "*", "session_date": f"eq.{date}",
                  "session_number": f"eq.{number}", "order": "recorded_at.asc,id.asc"})


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows):>6} rows -> {path.relative_to(HERE.parent)}")


def session_path(date, number):
    return SESSIONS_DIR / str(date) / f"session_{int(number):02d}.csv"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="export every session plus a combined all_readings.csv")
    ap.add_argument("--session", nargs=2, metavar=("DATE", "NUMBER"), help="export one session, e.g. 2026-11-07 2")
    args = ap.parse_args()

    if args.all:
        rows = fetch({"select": "*", "order": "session_date.asc,session_number.asc,recorded_at.asc,id.asc"})
        write_csv(HERE / "all_readings.csv", rows)
        by_session = {}
        for row in rows:
            by_session.setdefault((row["session_date"], row["session_number"]), []).append(row)
        for (date, number), srows in sorted(by_session.items()):
            write_csv(session_path(date, number), srows)
        print(f"{len(by_session)} session(s) exported.")
        return

    if args.session:
        date, number = args.session[0], int(args.session[1])
    else:
        date, number = latest_session()
        print(f"latest session: {date} #{number}")

    rows = fetch_session(date, number)
    if not rows:
        sys.exit(f"No readings for session {date} #{number}.")
    write_csv(session_path(date, number), rows)


if __name__ == "__main__":
    main()
