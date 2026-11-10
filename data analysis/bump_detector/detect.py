#!/usr/bin/env python3
"""Adaptive bump detector for GroundTruth IMU readings.

Tells a bump apart from a bike that is merely moving by asking "is this spike unusual
for the surface we are on right now?" instead of "is this over N g?".

Pipeline (every stage is causal, so the same code can run live on the phone):

    1. |a|        acceleration magnitude          -> mounting orientation does not matter
    2. high-pass  2nd-order Butterworth, 3 Hz     -> removes gravity, lean, pedalling cadence
    3. noise      running MAD of the high-passed   -> the roughness of the current surface
                  signal over the last 3 s
    4. z-score    |hp| / (1.4826 * MAD)           -> "how unusual is this sample"
    5. gates      moving?  (vibration or GPS speed)
                  gyro?    (a bump also pitches the frame: gyro z-score must spike too)
    6. trigger    z > k, transient shorter than max_width, then a refractory pause

Usage:
    python3 detect.py sessions/2026-11-07/session_10.csv               # events to stdout
    python3 detect.py session_10.csv --k 10 --out events.csv           # write a CSV
    python3 detect.py session_12.csv --no-motion-gate                  # stationary tap tests
    python3 detect.py session_10.csv --plot plots/session_10_detector.png

Input: the CSV written by export_supabase.py (recorded_at, ax..gz in g and deg/s,
latitude, longitude, anomaly_detected). Only numpy and scipy are required.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import signal


# --------------------------------------------------------------------------- parameters
@dataclass
class Params:
    hp_hz: float = 3.0         # high-pass corner; road bumps are short, pedalling/lean are slow
    noise_window_s: float = 3.0  # how much recent history defines "normal for this surface"
    noise_floor_g: float = 0.01  # stops a perfectly still sensor from dividing by ~0
    k: float = 10.0            # z-score threshold; 8 is sensitive, 12 is conservative
    max_width_s: float = 0.3   # a bump is a transient; longer = rumble strip / cobbles / noise
    refractory_s: float = 1.0  # one bump = one event
    motion_gate: bool = True   # only arm while moving
    motion_vib_g: float = 0.03  # RMS of high-passed |a| over 2 s that counts as "moving"
    motion_window_s: float = 2.0
    gyro_gate: bool = True     # require a coincident rotation transient
    gyro_k: float = 4.0        # gyro z-score the bump must also exceed


@dataclass
class Event:
    t: float                   # seconds from the first sample
    recorded_at: str
    peak_z: float
    peak_dev_g: float          # | |a| - 1 g | at the peak
    peak_gyro_z: float
    width_s: float
    latitude: float | None
    longitude: float | None
    flagged: bool              # did the app's manual flag fire within 1 s?


# --------------------------------------------------------------------------- streaming core
class BumpDetector:
    """Feed samples one at a time with `update()`; it returns an Event when a bump completes."""

    def __init__(self, fs: float, params: Params = Params()):
        self.p = params
        self.fs = fs
        self.sos = signal.butter(2, params.hp_hz, btype="high", fs=fs, output="sos")
        self.zi_a = signal.sosfilt_zi(self.sos) * 1.0   # start the filter at |a| = 1 g
        self.zi_g = signal.sosfilt_zi(self.sos) * 0.0
        n = max(int(params.noise_window_s * fs), 5)
        self.hist_a: deque = deque(maxlen=n)
        self.hist_g: deque = deque(maxlen=n)
        self.vib: deque = deque(maxlen=max(int(params.motion_window_s * fs), 5))
        self.last_event_t = -1e9
        self.open: dict | None = None    # event under construction
        # per-sample traces kept for plotting / debugging
        self.trace: dict[str, list] = {k: [] for k in ("t", "mag", "hp", "noise", "z", "gz", "moving", "armed")}

    @staticmethod
    def _mad(x: deque) -> float:
        a = np.fromiter(x, float)
        return float(np.median(np.abs(a - np.median(a)))) if len(a) else 0.0

    def update(self, t: float, acc_g, gyro_dps, meta: dict | None = None) -> Event | None:
        p = self.p
        mag = float(np.linalg.norm(acc_g))
        hp, self.zi_a = signal.sosfilt(self.sos, [mag], zi=self.zi_a)
        hp = float(hp[0])
        gmag = float(np.linalg.norm(gyro_dps))
        ghp, self.zi_g = signal.sosfilt(self.sos, [gmag], zi=self.zi_g)
        ghp = float(ghp[0])

        # noise level of the current surface, from history *before* this sample
        noise = max(1.4826 * self._mad(self.hist_a), p.noise_floor_g)
        gnoise = max(1.4826 * self._mad(self.hist_g), 0.5)      # deg/s floor
        z = abs(hp) / noise
        gz = abs(ghp) / gnoise
        self.hist_a.append(hp)
        self.hist_g.append(ghp)

        self.vib.append(hp * hp)
        moving = (not p.motion_gate) or (np.sqrt(np.mean(self.vib)) > p.motion_vib_g and len(self.vib) == self.vib.maxlen)
        warmed_up = len(self.hist_a) == self.hist_a.maxlen        # need a full noise window first
        armed = warmed_up and moving and (t - self.last_event_t) > p.refractory_s

        for k, v in zip(self.trace, (t, mag, hp, noise, z, gz, moving, armed)):
            self.trace[k].append(v)

        meta = meta or {}
        done = None
        if self.open is not None:
            ev = self.open
            if z > p.k / 2:                                   # still inside the transient
                if z > ev["peak_z"]:
                    ev.update(peak_z=z, peak_dev_g=abs(mag - 1), peak_t=t, meta=meta)
                ev["peak_gz"] = max(ev["peak_gz"], gz)
                if t - ev["start"] > p.max_width_s:           # too long to be a bump
                    self.open = None
            else:                                             # transient over -> decide
                width = t - ev["start"]
                self.open = None
                if (not p.gyro_gate) or ev["peak_gz"] >= p.gyro_k:
                    self.last_event_t = t
                    m = ev["meta"]
                    done = Event(ev["peak_t"], m.get("recorded_at", ""), ev["peak_z"], ev["peak_dev_g"],
                                 ev["peak_gz"], width, m.get("latitude"), m.get("longitude"), False)
        elif armed and z > p.k:
            self.open = dict(start=t, peak_t=t, peak_z=z, peak_dev_g=abs(mag - 1), peak_gz=gz, meta=meta)
        return done


# --------------------------------------------------------------------------- batch helpers
def parse_ts(s: str) -> float:
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", s)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + (float("0." + m.group(2)) if m.group(2) else 0.0)


def load_csv(path: Path):
    rows = list(csv.DictReader(open(path)))
    t = np.array([parse_ts(r["recorded_at"]) for r in rows])
    t -= t[0]
    acc = np.array([[float(r[k]) for k in ("ax", "ay", "az")] for r in rows])
    gyr = np.array([[float(r[k]) for k in ("gx", "gy", "gz")] for r in rows])
    return rows, t, acc, gyr


def run(rows, t, acc, gyr, params: Params = Params()):
    fs = len(t) / max(t[-1], 1e-3)
    det = BumpDetector(fs, params)
    events: list[Event] = []
    for i, r in enumerate(rows):
        meta = {"recorded_at": r["recorded_at"],
                "latitude": float(r["latitude"]) if r.get("latitude") else None,
                "longitude": float(r["longitude"]) if r.get("longitude") else None}
        ev = det.update(float(t[i]), acc[i], gyr[i], meta)
        if ev:
            events.append(ev)
    flag_t = t[[r.get("anomaly_detected") == "1" for r in rows]]
    for ev in events:
        ev.flagged = bool(len(flag_t)) and bool(np.any(np.abs(flag_t - ev.t) <= 1.0))
    return det, events, fs


def plot(det: BumpDetector, events, rows, t, out: Path, params: Params, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tr = {k: np.array(v) for k, v in det.trace.items()}
    flag = np.array([r.get("anomaly_detected") == "1" for r in rows])
    fig, ax = plt.subplots(3, 1, figsize=(14, 8), sharex=True)
    ax[0].plot(tr["t"], tr["mag"], color="#2563eb", lw=.8); ax[0].set_ylabel("|a| (g)")
    ax[1].plot(tr["t"], np.abs(tr["hp"]), color="#2563eb", lw=.8, label="|high-passed|")
    ax[1].plot(tr["t"], tr["noise"] * params.k, color="#9a3412", lw=1, label=f"adaptive threshold (k={params.k:g})")
    ax[1].set_ylabel("g"); ax[1].legend(loc="upper right", fontsize=8, frameon=False)
    ax[2].plot(tr["t"], tr["z"], color="#0f766e", lw=.8); ax[2].axhline(params.k, color="#9a3412", lw=1, ls="--")
    ax[2].fill_between(tr["t"], 0, tr["z"].max(), where=~tr["armed"].astype(bool), color="#9ca3af", alpha=.15, lw=0, label="not armed")
    ax[2].set_ylabel("z-score"); ax[2].set_xlabel("seconds")
    for a in ax:
        for s in t[flag]: a.axvline(s, color="#dc2626", alpha=.15, lw=1)
        for e in events: a.axvline(e.t, color="#0f766e", alpha=.6, lw=1, ls=":")
    ax[2].legend(loc="upper right", fontsize=8, frameon=False)
    fig.suptitle(f"{title}  ·  {len(events)} events  ·  red = app flag, dotted = detector", x=.02, ha="left")
    fig.tight_layout(); fig.savefig(out, dpi=150)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--k", type=float, default=Params.k, help="z-score threshold (default 10)")
    ap.add_argument("--no-motion-gate", action="store_true", help="arm even when stationary (tap tests)")
    ap.add_argument("--no-gyro-gate", action="store_true", help="do not require a gyro transient")
    ap.add_argument("--out", type=Path, help="write events to this CSV")
    ap.add_argument("--plot", type=Path, help="save a stage-by-stage PNG")
    a = ap.parse_args()
    params = Params(k=a.k, motion_gate=not a.no_motion_gate, gyro_gate=not a.no_gyro_gate)
    rows, t, acc, gyr = load_csv(a.csv)
    det, events, fs = run(rows, t, acc, gyr, params)
    cols = ["t_s", "recorded_at", "peak_z", "peak_dev_g", "peak_gyro_z", "width_s", "latitude", "longitude", "app_flagged"]
    out_rows = [[f"{e.t:.2f}", e.recorded_at, f"{e.peak_z:.1f}", f"{e.peak_dev_g:.2f}", f"{e.peak_gyro_z:.1f}",
                 f"{e.width_s:.2f}", e.latitude, e.longitude, int(e.flagged)] for e in events]
    w = csv.writer(open(a.out, "w", newline="") if a.out else sys.stdout)
    w.writerow(cols); w.writerows(out_rows)
    n_flag = sum(1 for r in rows if r.get("anomaly_detected") == "1")
    print(f"# {a.csv.name}: {len(rows)} readings, {fs:.0f} Hz, {t[-1]/60:.1f} min -> {len(events)} events "
          f"({len(events)/max(t[-1]/60,1e-3):.1f}/min); {sum(e.flagged for e in events)} coincide with an app flag"
          f"{' (none in file)' if not n_flag else ''}", file=sys.stderr)
    if a.plot:
        plot(det, events, rows, t, a.plot, params, a.csv.stem)
        print(f"# plot -> {a.plot}", file=sys.stderr)


if __name__ == "__main__":
    main()
