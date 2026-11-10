#!/usr/bin/env python3
"""Bench-validated bump detector: |a| -> 3 Hz high-pass -> fixed threshold.

Validated on session 7 (nine bench hits): every hit exceeds 1.2 g after the high-pass and
nothing else exceeds 0.77 g, so a 0.8 g threshold catches 9/9 with 0 false detections.
This is the method implemented live in software/dashboard/filter.js.

    python3 hp_detect.py sessions/2026-11-07/session_07.csv            # threshold 0.8 g
    python3 hp_detect.py session_10.csv --threshold 1.5 --out ev.csv

Note: on real rides the high-passed baseline reaches ~1 g during ordinary riding, so a fixed
0.8 g bar fires often there. Use detect.py (adaptive + gyro gate) for rides.
"""
import argparse, csv, re, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from scipy import signal

def parse_ts(s):
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", s)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--cutoff", type=float, default=3.0, help="high-pass corner, Hz (default 3)")
    ap.add_argument("--threshold", type=float, default=0.8, help="g on |high-passed magnitude| (default 0.8)")
    ap.add_argument("--gap", type=float, default=1.0, help="refractory seconds between events")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv)))
    t = np.array([parse_ts(r["recorded_at"]) for r in rows]); t -= t[0]; fs = len(t) / max(t[-1], 1e-3)
    mag = np.linalg.norm([[float(r[k]) for k in ("ax", "ay", "az")] for r in rows], axis=1)
    sos = signal.butter(2, a.cutoff, btype="high", fs=fs, output="sos")
    hp = signal.sosfilt(sos, mag, zi=signal.sosfilt_zi(sos) * mag[0])[0]     # causal, primed at the first value
    ahp = np.abs(hp)
    flag_t = t[[r.get("anomaly_detected") == "1" for r in rows]]
    w = csv.writer(open(a.out, "w", newline="") if a.out else sys.stdout)
    w.writerow(["t_s", "recorded_at", "hp_g", "mag_g", "latitude", "longitude", "app_flagged"])
    last = -1e9; n = 0; hit = 0
    for i in np.where(ahp > a.threshold)[0]:
        if t[i] - last <= a.gap: continue
        last = t[i]; n += 1; r = rows[i]
        f = bool(len(flag_t)) and bool(np.any(np.abs(flag_t - t[i]) <= 1.0)); hit += f
        w.writerow([f"{t[i]:.2f}", r["recorded_at"], f"{ahp[i]:.2f}", f"{mag[i]:.2f}", r.get("latitude"), r.get("longitude"), int(f)])
    print(f"# {a.csv.name}: {fs:.0f} Hz, {t[-1]:.0f} s, threshold {a.threshold} g -> {n} events, {hit} coincide with an app flag", file=sys.stderr)

if __name__ == "__main__":
    main()
