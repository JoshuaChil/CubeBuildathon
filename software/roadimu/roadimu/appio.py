"""Read/write the Road Reader iPhone app's CSV (roadreader_app/RoadReader/Models/IMUSample.swift).

Columns:
    ax,ay,az,gx,gy,gz,crack_pothole_detected,phone_time_ms,device_time_ms,
    latitude,longitude,altitude_m,speed_mps,horizontal_accuracy_m

Differences from the docs/data-schema.md format, and how they're handled:

* Timing.  If the ESP32 sends its own millis() (7-field lines) the app writes it to
  device_time_ms and we use it.  Otherwise only phone_time_ms exists: the time each
  BLE notification *arrived*, which is bursty (iOS delivers several per connection
  event).  The firmware samples at a fixed rate, so we rebuild a uniform time base from
  the sample index and use the arrival times only to find the rate and lost packets.
* GPS columns are blank until the phone has a fix; fixes worse than 30 m are dropped.
* The label is held while the Detect Bump button is down; we keep its rising edges.
* Units: the firmware sends g and deg/s.  If the accelerometer looks like m/s^2 (|a| ~ 9.8
  at rest) it is converted.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np
from scipy.ndimage import minimum_filter1d

from . import G
from .io import ImuLog

APP_COLUMNS = [
    "ax", "ay", "az", "gx", "gy", "gz", "crack_pothole_detected", "phone_time_ms", "device_time_ms",
    "latitude", "longitude", "altitude_m", "speed_mps", "horizontal_accuracy_m",
]  # fmt: skip
MAX_GPS_ACCURACY_M = 30.0


def is_app_csv(path: str | Path) -> bool:
    with Path(path).open() as fh:
        return "phone_time_ms" in fh.readline()


def _f(x: str | None) -> float:
    try:
        return float(x) if x not in (None, "") else np.nan
    except ValueError:
        return np.nan


def parse_rows(rows: list[list[str]]) -> dict[str, np.ndarray]:
    """Rows of the app format (header removed) -> column arrays.  Malformed rows are dropped."""
    data = {c: np.full(len(rows), np.nan) for c in APP_COLUMNS}
    keep = np.zeros(len(rows), dtype=bool)
    for i, r in enumerate(rows):
        if len(r) < 8 or r[0] == "ax":
            continue
        vals = [_f(r[j]) if j < len(r) else np.nan for j in range(len(APP_COLUMNS))]
        if not np.all(np.isfinite(vals[:6])) or not np.isfinite(vals[7]):
            continue
        for c, v in zip(APP_COLUMNS, vals):
            data[c][i] = v
        keep[i] = True
    return {c: v[keep] for c, v in data.items()}


def uniform_timeline(phone_ms: np.ndarray, nominal_hz: float | None = None) -> tuple[np.ndarray, float, int]:
    """Uniform sample times (ms) from bursty arrival times.  Returns (t_ms, rate_hz, samples_lost)."""
    n = phone_ms.size
    if n < 3:
        return phone_ms.astype(np.int64), nominal_hz or 50.0, 0
    period = (phone_ms[-1] - phone_ms[0]) / (n - 1) if nominal_hz is None else 1000.0 / nominal_hz
    index = np.arange(n, dtype=float)
    lost = 0
    for _ in range(1):  # one pass: re-running starts to see BLE burst patterns as losses
        # Arrival delay relative to an ideal clock.  BLE bursts make it jump *up* briefly, but
        # the earliest arrival in any ~1 s stays put - unless a sample was lost, which shifts
        # every later sample by one period.  Compare the minimum just before and just after.
        excess = phone_ms - (phone_ms[0] + index * period)
        w = max(3, int(round(1000.0 / period)))
        after = minimum_filter1d(excess, size=w, origin=-(w // 2), mode="nearest")  # [i, i+w)
        before = minimum_filter1d(excess, size=w, origin=(w - 1) // 2, mode="nearest")  # (i-w, i]
        step = np.zeros(n)
        step[1:] = after[1:] - before[:-1]
        hit = step > 0.6 * period
        starts = np.flatnonzero(hit & ~np.concatenate([[False], hit[:-1]]))
        gaps = np.zeros(n)
        for s in starts:
            e = s
            while e < n and hit[e]:
                e += 1
            gaps[s] = np.round(step[s:e].max() / period)
        if gaps.sum() == 0:
            break
        index = index + np.cumsum(gaps)
        lost += int(gaps.sum())
        if nominal_hz is None and index[-1] > 0:
            period = (phone_ms[-1] - phone_ms[0]) / index[-1]
    t_ms = phone_ms[0] + index * period
    return np.round(t_ms).astype(np.int64), 1000.0 / period, lost


def to_imulog(cols: dict[str, np.ndarray], nominal_hz: float | None = None) -> tuple[ImuLog, dict]:
    phone = cols["phone_time_ms"]
    dev = cols["device_time_ms"]
    info: dict = {"rows": int(phone.size)}
    if phone.size and np.mean(np.isfinite(dev)) > 0.9:
        ok = np.isfinite(dev)
        t_ms = np.interp(np.arange(dev.size), np.flatnonzero(ok), dev[ok]).astype(np.int64)
        info["timing"] = "ESP32 clock"
        info["rate_hz"] = float(1000.0 / np.median(np.diff(t_ms))) if t_ms.size > 2 else None
    else:
        t_ms, rate, lost = uniform_timeline(phone, nominal_hz)
        info.update(timing="rebuilt from phone arrival times", rate_hz=float(rate), samples_lost=lost)

    acc = np.column_stack([cols["ax"], cols["ay"], cols["az"]])
    if acc.size and np.median(np.linalg.norm(acc[: min(200, acc.shape[0])], axis=1)) > 3.0:
        acc = acc / G  # firmware sent m/s^2
        info["accel_units"] = "m/s^2 (converted to g)"
    gyro = np.column_stack([cols["gx"], cols["gy"], cols["gz"]])

    lat, lon, spd, hacc = (cols[c].copy() for c in ("latitude", "longitude", "speed_mps", "horizontal_accuracy_m"))
    bad = ~np.isfinite(lat) | ~np.isfinite(lon) | (np.isfinite(hacc) & (hacc > MAX_GPS_ACCURACY_M))
    lat[bad], lon[bad], spd[bad] = np.nan, np.nan, np.nan
    info["gps_rows"] = int((~bad).sum())

    held = cols["crack_pothole_detected"] == 1
    rising = held & ~np.concatenate([[False], held[:-1]])
    info["button_presses"] = int(rising.sum())
    return ImuLog(t_ms=t_ms, lat=lat, lon=lon, speed=spd, acc_g=acc, gyro_dps=gyro, label=rising), info


def read_app_csv(path: str | Path, nominal_hz: float | None = None) -> tuple[ImuLog, dict]:
    with Path(path).open(newline="") as fh:
        rows = list(csv.reader(fh))
    return to_imulog(parse_rows(rows), nominal_hz)


def read_app_lines(lines: list[str], nominal_hz: float | None = None) -> tuple[ImuLog, dict]:
    return to_imulog(parse_rows(list(csv.reader(io.StringIO("\n".join(lines))))), nominal_hz)


def write_app_csv(path: str | Path, rows: list[list]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(APP_COLUMNS)
        w.writerows(rows)
