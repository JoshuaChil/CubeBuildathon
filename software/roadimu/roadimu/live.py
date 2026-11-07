"""Live road analysis of a ride in progress (used by roadreader_app/listener/listener.py).

Rows arrive one by one from the phone.  A background thread re-runs the full pipeline on
everything received so far every few seconds (a 10-minute ride at 200 Hz takes ~1-2 s),
and keeps the latest result as a small JSON-able snapshot for the app to poll.

A new ride is detected when the phone's clock jumps by more than ``new_session_gap_s``.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from .appio import read_app_lines
from .topo import TopoConfig, reconstruct


class LiveAnalyzer:
    def __init__(self, vehicle: str = "bike", interval_s: float = 3.0, min_ride_s: float = 10.0, new_session_gap_s: float = 15.0):
        self.vehicle = vehicle
        self.interval_s = interval_s
        self.min_ride_s = min_ride_s
        self.new_session_gap_s = new_session_gap_s
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._session = 0
        self._first_ms: float | None = None
        self._last_ms: float | None = None
        self._version = 0
        self._snapshot: dict = self._status("waiting", "Waiting for the phone to start streaming.")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ----------------------------------------------------------------- input

    def add_line(self, line: str) -> None:
        fields = line.split(",")
        if len(fields) < 8 or fields[0] == "ax":
            return
        try:
            phone_ms = float(fields[7])
        except ValueError:
            return
        with self._lock:
            if self._last_ms is not None and abs(phone_ms - self._last_ms) > self.new_session_gap_s * 1000:
                self._reset_locked()
            if self._first_ms is None:
                self._first_ms = phone_ms
            self._last_ms = phone_ms
            self._lines.append(line)
            self._version += 1

    def set_vehicle(self, vehicle: str) -> None:
        if vehicle in ("bike", "car") and vehicle != self.vehicle:
            with self._lock:
                self.vehicle = vehicle
                self._version += 1

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._session += 1
        self._lines = []
        self._first_ms = self._last_ms = None
        self._snapshot = self._status("waiting", "New ride started.")

    # ----------------------------------------------------------------- output

    def snapshot(self) -> dict:
        with self._lock:
            snap = dict(self._snapshot)
            snap["rows"] = len(self._lines)
            snap["duration_s"] = round((self._last_ms - self._first_ms) / 1000.0, 1) if self._first_ms is not None else 0.0
            snap["session"] = self._session
            snap["vehicle"] = self.vehicle
        return snap

    def close(self) -> None:
        self._stop.set()

    def _status(self, status: str, message: str, **extra) -> dict:
        return {"status": status, "message": message, "updated_at": time.time(), **extra}

    # ----------------------------------------------------------------- worker

    def _run(self) -> None:
        done_version = -1
        while not self._stop.wait(0.5):
            with self._lock:
                version, session, vehicle = self._version, self._session, self.vehicle
                lines = list(self._lines) if version != done_version else None
                duration = (self._last_ms - self._first_ms) / 1000.0 if self._first_ms is not None else 0.0
            if lines is None:
                continue
            if duration < self.min_ride_s:
                with self._lock:
                    if session == self._session:
                        self._snapshot = self._status(
                            "collecting", "Stand still for ~2 s to calibrate, then ride." if duration < 3 else "Collecting data…"
                        )
                continue
            t0 = time.time()
            try:
                snap = self._analyse(lines, vehicle)
            except Exception as exc:  # keep serving; show why in the app
                snap = self._status("collecting" if "moving data" in str(exc) else "error", str(exc))
            snap["processing_ms"] = int((time.time() - t0) * 1000)
            with self._lock:
                if session == self._session:
                    self._snapshot = snap
            done_version = version
            time.sleep(max(self.interval_s - (time.time() - t0), 0.0))

    def _analyse(self, lines: list[str], vehicle: str) -> dict:
        log, info = read_app_lines(lines)
        res = reconstruct(log, TopoConfig(vehicle=vehicle), name="live")
        res.input_info = info
        d = res.to_dict(track_step_m=5.0)
        seg = res.segments[-1] if res.segments else None
        moving = res.speed[res.moving]
        fs = 1.0 / (res.t[1] - res.t[0]) if res.t.size > 1 else 50.0
        last = res.speed[-int(3 * fs) :] if res.speed.size else np.zeros(1)
        return self._status(
            "ok",
            res.notes[0] if res.notes else "Analysing.",
            distance_m=d["summary"]["distance_m"],
            speed_mps=round(float(np.median(last)), 2),
            mean_speed_mps=round(float(moving.mean()), 2) if moving.size else 0.0,
            mean_iri=d["summary"]["mean_iri_m_per_km"],
            current=seg and {k: seg[k] for k in ("iri_m_per_km", "rms_mm", "condition", "lat", "lon")},
            potholes=d["summary"]["potholes"],
            bumps=d["summary"]["bumps"],
            events=d["events"][-30:],
            segments=d["segments"],
            notes=res.notes,
            input=info,
        )
