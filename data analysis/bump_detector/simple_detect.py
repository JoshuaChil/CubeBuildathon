#!/usr/bin/env python3
"""Simple adaptive bump detector: acceleration magnitude vs a MAD threshold from the last 10 s.

    1. |a| = sqrt(ax^2 + ay^2 + az^2)                     for every reading
    2. over the previous 10 s of |a| (not including this reading):
           centre    = median(|a|)                         ~1 g
           spread    = 1.4826 * MAD(|a|)                   robust "one sigma" of recent riding
           threshold = centre + k * spread
    3. bump when |a| > threshold; readings closer than `gap` seconds are one event

No filtering, no gates. The threshold follows the road: rough pavement raises it, smooth
pavement lowers it. Nothing can trigger until 10 s of history exist. `--window 0` uses
one session-wide threshold instead.

Usage:
    python3 simple_detect.py sessions/2026-11-07/session_10.csv            # k = 6, 10 s window
    python3 simple_detect.py session_10.csv --k 8 --out events.csv
    python3 simple_detect.py session_10.csv --window 5 --plot p.png
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def parse_ts(s: str) -> float:
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", s)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)


def mad_threshold(mag: np.ndarray, k: float) -> tuple[float, float, float]:
    centre = float(np.median(mag))
    spread = 1.4826 * float(np.median(np.abs(mag - centre)))
    return centre, spread, centre + k * spread


def running_threshold(mag: np.ndarray, k: float, n: int) -> np.ndarray:
    """Causal: threshold for sample i uses samples i-n .. i-1 only."""
    thr = np.full(len(mag), np.inf)          # never triggers until the window is full
    for i in range(n, len(mag)):
        w = mag[i - n:i]
        c = np.median(w)
        thr[i] = c + k * 1.4826 * np.median(np.abs(w - c))
    return thr


def detect(t: np.ndarray, mag: np.ndarray, thr, gap: float):
    """Group threshold crossings into events; report each event's peak."""
    over = np.where(mag > thr)[0]
    events = []
    for i in over:
        if events and t[i] - t[events[-1]["last"]] <= gap:
            events[-1]["last"] = i
            if mag[i] > mag[events[-1]["peak"]]:
                events[-1]["peak"] = i
        else:
            events.append({"first": i, "last": i, "peak": i})
    return events


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--k", type=float, default=6.0, help="threshold = median + k * 1.4826 * MAD (default 6)")
    ap.add_argument("--window", type=float, default=10, help="seconds of running history (default 10); 0 = whole session")
    ap.add_argument("--gap", type=float, default=1.0, help="crossings closer than this are one event (s)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--plot", type=Path)
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.csv)))
    t = np.array([parse_ts(r["recorded_at"]) for r in rows]); t -= t[0]
    mag = np.linalg.norm([[float(r[k]) for k in ("ax", "ay", "az")] for r in rows], axis=1)
    fs = len(t) / max(t[-1], 1e-3)

    if a.window:
        thr = running_threshold(mag, a.k, int(a.window * fs))
        desc = f"running {a.window:g} s window"
    else:
        centre, spread, thr_val = mad_threshold(mag, a.k)
        thr = np.full(len(mag), thr_val)
        desc = f"median {centre:.3f} g, MAD-sigma {spread:.3f} g, threshold {thr_val:.2f} g"

    events = detect(t, mag, thr, a.gap)
    flag_t = t[[r.get("anomaly_detected") == "1" for r in rows]]

    w = csv.writer(open(a.out, "w", newline="") if a.out else sys.stdout)
    w.writerow(["t_s", "recorded_at", "peak_g", "threshold_g", "n_over", "latitude", "longitude", "app_flagged"])
    n_hit = 0
    for e in events:
        p = e["peak"]; r = rows[p]
        hit = bool(len(flag_t)) and bool(np.any(np.abs(flag_t - t[p]) <= 1.0)); n_hit += hit
        w.writerow([f"{t[p]:.2f}", r["recorded_at"], f"{mag[p]:.2f}", f"{thr[p]:.2f}", e["last"] - e["first"] + 1,
                    r.get("latitude"), r.get("longitude"), int(hit)])
    print(f"# {a.csv.name}: {len(rows)} readings, {fs:.0f} Hz, {t[-1]/60:.1f} min | k={a.k:g}, {desc} | "
          f"{len(events)} events ({len(events)/max(t[-1]/60,1e-3):.1f}/min), {n_hit} coincide with an app flag", file=sys.stderr)

    if a.plot:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(14, 4.5))
        ax.plot(t, mag, color="#2563eb", lw=.7, label="|a|")
        ax.plot(t, np.where(np.isfinite(thr), thr, np.nan), color="#9a3412", lw=1.2, label=f"median + {a.k:g} × MAD-sigma, last {a.window:g} s" if a.window else f"median + {a.k:g} × MAD-sigma")
        for s in flag_t: ax.axvline(s, color="#dc2626", alpha=.15, lw=2)
        for e in events: ax.plot(t[e["peak"]], mag[e["peak"]], "o", mfc="none", mec="#0f766e", ms=7, mew=1.3)
        ax.set_xlabel("seconds"); ax.set_ylabel("g"); ax.legend(loc="upper right", frameon=False, fontsize=9)
        ax.set_title(f"{a.csv.stem} · simple MAD threshold · {len(events)} events (circles), red = app flag", loc="left", fontsize=11)
        for s in ("top", "right"): ax.spines[s].set_visible(False)
        fig.tight_layout(); fig.savefig(a.plot, dpi=150); print(f"# plot -> {a.plot}", file=sys.stderr)


if __name__ == "__main__":
    main()
