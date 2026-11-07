"""Step 1: clean up a raw log and work out how the box is mounted.

* Timestamps from the ESP32 are in whole milliseconds and BLE drops packets,
  so samples are re-gridded onto a uniform time base (gaps are flagged).
* The phone writes the latest GPS fix on every row; the actual fixes are the
  rows where lat/lon/speed change.
* Calibration: while the vehicle stands still at the start, the accelerometer
  sees only gravity -> roll/pitch of the box and gyro bias.  Once moving, the
  direction of horizontal acceleration that matches GPS speed changes gives
  the yaw of the box (any yaw works, even mounted sideways or backwards).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import uniform_filter1d

from .. import G
from ..io import ImuLog


@dataclass
class Prepared:
    t: np.ndarray  # uniform time base, s since first sample (sensor clock)
    t0_ms: int  # ESP32 timestamp of the first sample
    fs: float
    acc: np.ndarray  # (N, 3) m/s^2, sensor frame
    gyro: np.ndarray  # (N, 3) rad/s, sensor frame
    gap: np.ndarray  # True where the sample was interpolated across lost packets
    gps_t: np.ndarray
    gps_lat: np.ndarray
    gps_lon: np.ndarray
    gps_speed: np.ndarray
    label_t: np.ndarray  # button presses, s


@dataclass
class Calibration:
    R: np.ndarray  # rotation sensor -> vehicle frame (x forward, y left, z up)
    gyro_bias: np.ndarray  # rad/s, sensor frame
    g_local: float  # magnitude of gravity as this accelerometer sees it, m/s^2
    still: slice
    tilt_deg: float
    yaw_deg: float


def prepare(log: ImuLog, fs: float | None = None) -> Prepared:
    t_raw = (log.t_ms - log.t_ms[0]) / 1000.0
    t_raw, uniq = np.unique(t_raw, return_index=True)
    if t_raw.size < 10:
        raise ValueError("log too short")
    fs = fs or float(np.round(1.0 / np.median(np.diff(t_raw))))
    t = np.arange(0.0, t_raw[-1], 1.0 / fs)
    acc_raw = log.acc_g[uniq] * G
    gyro_raw = np.deg2rad(log.gyro_dps[uniq])
    acc = np.column_stack([np.interp(t, t_raw, acc_raw[:, i]) for i in range(3)])
    gyro = np.column_stack([np.interp(t, t_raw, gyro_raw[:, i]) for i in range(3)])
    j = np.clip(np.searchsorted(t_raw, t), 1, t_raw.size - 1)
    nearest = np.minimum(np.abs(t_raw[j] - t), np.abs(t_raw[j - 1] - t))
    gap = nearest > 1.5 / fs

    lat, lon, spd = log.lat[uniq], log.lon[uniq], log.speed[uniq]
    change = np.ones(lat.size, dtype=bool)
    change[1:] = (np.diff(lat) != 0) | (np.diff(lon) != 0) | (np.diff(spd) != 0)
    change &= np.isfinite(lat) & np.isfinite(lon)
    label_t = t_raw[log.label[uniq]]
    return Prepared(t, int(log.t_ms[0]), fs, acc, gyro, gap, t_raw[change], lat[change], lon[change], spd[change], label_t)


def gps_speed(prep: Prepared) -> tuple[np.ndarray, np.ndarray]:
    """Smoothed GPS speed and its derivative on the IMU time base."""
    v = prep.gps_speed.astype(float)
    if v.size >= 5:
        v = signal.savgol_filter(v, 5, 2)
    v = np.maximum(v, 0.0)
    if prep.gps_t.size < 2:
        return np.zeros_like(prep.t), np.zeros_like(prep.t)
    interp = PchipInterpolator(prep.gps_t, v, extrapolate=False)
    vt = np.nan_to_num(interp(prep.t), nan=0.0)
    vt[prep.t > prep.gps_t[-1]] = v[-1]
    sos = signal.butter(2, 0.5, fs=prep.fs, output="sos")
    dvdt = signal.sosfiltfilt(sos, np.gradient(vt, 1.0 / prep.fs))
    return np.maximum(vt, 0.0), dvdt


def find_still(
    acc: np.ndarray,
    gyro: np.ndarray,
    fs: float,
    v_gps: np.ndarray | None = None,
    search_s: float = 60.0,
    win_s: float = 1.0,
) -> slice:
    """First stretch (>= 1 s) where the vehicle is clearly not moving.

    Every accelerometer axis must be quiet (a smooth pull-away barely changes
    |a|, but it does change the x axis), the gyro must be quiet, and GPS must
    say we're stopped.  GPS updates at 1 Hz, so we stop 1.5 s before the first
    fix that shows motion.
    """
    w = max(3, int(win_s * fs))

    def mstd(x):
        m = uniform_filter1d(x, w)
        return np.sqrt(np.maximum(uniform_filter1d(x * x, w) - m * m, 0.0))

    n = min(acc.shape[0], int(search_s * fs))
    s_a = np.max([mstd(acc[:n, i]) for i in range(3)], axis=0)
    s_g = np.max([mstd(gyro[:n, i]) for i in range(3)], axis=0)
    ok = (s_a < 0.012 * G) & (s_g < np.deg2rad(0.6))
    if v_gps is not None:
        moving = np.flatnonzero(v_gps[:n] > 0.3)
        if moving.size:
            ok[max(0, moving[0] - int(1.5 * fs)) :] = False
    edges = np.flatnonzero(np.diff(np.concatenate([[0], ok.astype(int), [0]])))
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a >= w:
            return slice(a + w // 4, b - w // 4)
    return slice(0, w)  # fall back to the first second


def _rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking unit vector a onto unit vector b."""
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def calibrate(prep: Prepared, v_gps: np.ndarray, dvdt: np.ndarray) -> Calibration:
    still = find_still(prep.acc, prep.gyro, prep.fs, v_gps)
    gyro_bias = prep.gyro[still].mean(axis=0)
    g_vec = prep.acc[still].mean(axis=0)
    g_local = float(np.linalg.norm(g_vec))
    R_tilt = _rotation_between(g_vec / g_local, np.array([0.0, 0.0, 1.0]))
    acc_l = prep.acc @ R_tilt.T

    sos = signal.butter(2, 0.3, fs=prep.fs, output="sos")
    ax, ay = signal.sosfiltfilt(sos, acc_l[:, 0]), signal.sosfiltfilt(sos, acc_l[:, 1])
    moving = v_gps > 1.0
    if moving.sum() > prep.fs * 5:
        # Forward is the horizontal direction whose acceleration co-varies with GPS dv/dt.
        # (A free least-squares fit is ill-conditioned: on a straight road the lateral
        # channel barely varies, so tiny spurious correlations blow up its coefficient.)
        d = dvdt[moving] - dvdt[moving].mean()
        cx = float(np.dot(ax[moving] - ax[moving].mean(), d))
        cy = float(np.dot(ay[moving] - ay[moving].mean(), d))
        yaw = float(np.arctan2(cy, cx))
    else:
        yaw = 0.0
    c, s = np.cos(yaw), np.sin(yaw)
    R_yaw = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    tilt = float(np.degrees(np.arccos(np.clip(g_vec[2] / g_local, -1, 1))))
    return Calibration(R_yaw @ R_tilt, gyro_bias, g_local, still, tilt, float(np.degrees(yaw)))
