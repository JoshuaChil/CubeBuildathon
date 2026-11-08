"""Bump / pothole detection from the IMU alone - no GPS.

GPS is only used afterwards to put each event on the map.  So a bad fix, an urban
canyon, a tunnel or no GPS at all can move a pin but never adds, removes or resizes
an event.

Everything here comes from the accelerometer and gyro:

1. **Which way is up**: gravity is the slow part of the accelerometer signal
   (< 0.3 Hz).  Vertical acceleration = acceleration along it.
2. **Which way the frame pitches**: a bump at the front tyre pitches the frame
   about its left-right axis.  That axis is the direction of the strongest fast
   (> 3 Hz) horizontal rotation in the gyro.  Its sign (+/-) is the one for which
   the vehicle model explains the accelerometer and gyro together best.
3. **Speed**: the rear tyre hits every bump ``wheelbase / speed`` after the front
   one.  For each 4 s window we try speeds on a grid and keep the one for which
   the model fits both channels best (the "front-rear echo").  This also tells us
   when the vehicle is moving: standing still, nothing fits.
4. **Road under the tyre**: the same Wiener deconvolution as the topography
   (profile.py), but driven by these IMU-only signals.
5. **Events**: roughness.detect_events on that IMU-only profile (thresholds in mm
   and m, see roughness.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal
from scipy.ndimage import median_filter, uniform_filter1d

from ..sensors import imu_filter_response
from ..vehicle import VehicleModel
from . import profile, roughness

SPEED_RANGE = {"bike": (1.0, 14.0), "car": (1.0, 25.0)}
ASSUMED_SPEED = {"bike": 5.0, "car": 10.0}


@dataclass
class ImpactResult:
    t: np.ndarray  # s
    acc_v: np.ndarray  # vertical acceleration, m/s^2 (gravity removed)
    pitch_rate: np.ndarray  # rad/s about the estimated left-right axis (zeros if not found)
    speed: np.ndarray  # IMU-only speed estimate, m/s
    moving: np.ndarray
    road: np.ndarray  # road height under the front tyre vs time, m
    events: list[dict] = field(default_factory=list)  # with t_s, t_start_s, t_end_s
    pitch_axis: np.ndarray | None = None  # sensor frame
    speed_source: str = "front-rear echo"
    notes: list[str] = field(default_factory=list)


def vertical_acceleration(acc: np.ndarray, fs: float, gap: np.ndarray, lp_hz: float = 0.3) -> tuple[np.ndarray, np.ndarray]:
    """(vertical dynamic acceleration, unit 'up' vector per sample).  acc in m/s^2, sensor frame."""
    sos = signal.butter(2, lp_hz, fs=fs, output="sos")
    g = signal.sosfiltfilt(sos, acc, axis=0) if acc.shape[0] > 30 else np.repeat(acc.mean(0, keepdims=True), acc.shape[0], 0)
    gnorm = np.linalg.norm(g, axis=1, keepdims=True)
    up = g / np.maximum(gnorm, 1e-6)
    av = np.einsum("ij,ij->i", acc, up) - gnorm[:, 0]
    av = _highpass(av, fs)
    return np.where(gap, 0.0, av), up


def _highpass(x: np.ndarray, fs: float, f: float = 0.3) -> np.ndarray:
    if x.size < 30:
        return x - x.mean()
    return signal.sosfiltfilt(signal.butter(2, f, btype="high", fs=fs, output="sos"), x)


def _fit_residual(ya, yq, ha, hq) -> float:
    """How badly one road input explains both channels (0 = perfectly), per frequency LS."""
    na = np.mean(np.abs(ya) ** 2) + 1e-18
    nq = np.mean(np.abs(yq) ** 2) + 1e-18
    r = (np.conj(ha) * ya / na + np.conj(hq) * yq / nq) / (np.abs(ha) ** 2 / na + np.abs(hq) ** 2 / nq + 1e-18)
    return float(np.sum(np.abs(ya - ha * r) ** 2 / na + np.abs(yq - hq * r) ** 2 / nq))


class _Windows:
    def __init__(self, fs: float, imu_bw_hz: float, nw_s: float, f_min: float = 0.5):
        self.nw = int(round(nw_s * fs))
        self.nw += self.nw % 2
        self.nfft = 2 * self.nw
        self.freqs = np.fft.rfftfreq(self.nfft, 1.0 / fs)
        self.band = (self.freqs > f_min) & (self.freqs < min(0.4 * fs, 60.0))
        self.h_imu = imu_filter_response(self.freqs, imu_bw_hz, 2)[self.band]
        self.win = signal.windows.hann(self.nw, sym=False)

    def spec(self, x: np.ndarray, s0: int) -> np.ndarray:
        return np.fft.rfft(x[s0 : s0 + self.nw] * self.win, self.nfft)[self.band]

    def frf(self, model: VehicleModel, v: float):
        ha, hq = model.frf(self.freqs, v)
        return ha[self.band] * self.h_imu, hq[self.band] * self.h_imu


def pitch_rate_signal(gyro: np.ndarray, up: np.ndarray, fs: float, gap: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Gyro rate about the axis it vibrates around most (perpendicular to gravity), sign unresolved."""
    if gyro.shape[0] < 4 * fs or not np.any(gyro):
        return np.zeros(gyro.shape[0]), None
    w = gyro - np.median(gyro, axis=0)
    wh = w - np.einsum("ij,ij->i", w, up)[:, None] * up
    bp = signal.butter(2, [3.0, min(0.45 * fs, 60.0)], btype="band", fs=fs, output="sos")
    whf = signal.sosfiltfilt(bp, wh, axis=0)[~gap]
    vals, vecs = np.linalg.eigh(whf.T @ whf)
    if vals[-1] <= 0 or vals[-1] < 1.5 * vals[-2]:  # no clear pitch axis
        return np.zeros(gyro.shape[0]), None
    axis = vecs[:, -1]
    q = np.where(gap, 0.0, _highpass(wh @ axis, fs))
    return q, axis


def resolve_sign(av, q, fs, model, v0, wins: _Windows, top: int = 40) -> tuple[int, float]:
    """+1/-1 for the pitch signal, and how clearly (residual ratio; ~1 = can't tell)."""
    starts = np.arange(0, max(1, av.size - wins.nw), wins.nw // 2)
    energy = np.array([np.sum(av[s : s + wins.nw] ** 2) for s in starts])
    ha, hq = wins.frf(model, v0)
    res = {+1: 0.0, -1: 0.0}
    for s in starts[np.argsort(energy)[-top:]]:
        ya, yq = wins.spec(av, s), wins.spec(q, s)
        for sg in res:
            res[sg] += _fit_residual(ya, sg * yq, ha, hq)
    best = min(res, key=res.get)
    return best, res[-best] / max(res[best], 1e-18)


def echo_speed(av, q, fs, model, vehicle, wins: _Windows, hop_s: float = 0.5, smooth_windows: int = 5, min_contrast: float = 1.8):
    """Speed from the front-rear tyre delay, per window; returns (speed, moving) on the sample grid."""
    lo, hi = SPEED_RANGE.get(vehicle, (1.0, 20.0))
    grid = np.arange(lo, hi + 1e-9, 0.25)
    H = [wins.frf(model, v) for v in grid]
    hop = max(1, int(round(hop_s * fs)))
    starts = np.arange(0, max(1, av.size - wins.nw + 1), hop)
    logres = np.empty((starts.size, grid.size))
    for i, s in enumerate(starts):
        ya, yq = wins.spec(av, s), wins.spec(q, s)
        r = np.array([_fit_residual(ya, yq, ha, hq) for ha, hq in H])
        logres[i] = np.log(np.maximum(r, 1e-18))
    # Smooth the residual surface over neighbouring windows before picking the minimum:
    # speed changes slowly, single noisy windows don't.
    sm = uniform_filter1d(logres, smooth_windows, axis=0, mode="nearest")
    k = np.argmin(sm, axis=1)
    contrast = np.exp(sm.max(axis=1) - sm[np.arange(starts.size), k])
    # parabolic refinement between grid points
    kk = np.clip(k, 1, grid.size - 2)
    y0, y1, y2 = sm[np.arange(starts.size), kk - 1], sm[np.arange(starts.size), kk], sm[np.arange(starts.size), kk + 1]
    denom = y0 - 2 * y1 + y2
    off = np.where(np.abs(denom) > 1e-12, 0.5 * (y0 - y2) / denom, 0.0)
    v_w = grid[kk] + np.clip(off, -1, 1) * (grid[1] - grid[0])
    moving_w = contrast > min_contrast
    v_w = median_filter(v_w, size=smooth_windows, mode="nearest")
    centres = starts + wins.nw // 2
    t_idx = np.arange(av.size)
    mov = np.interp(t_idx, centres, moving_w.astype(float)) > 0.5
    if moving_w.sum() >= 2:
        v = np.interp(t_idx, centres[moving_w], v_w[moving_w])
    else:
        v = np.zeros(av.size)
    v = uniform_filter1d(np.where(mov, v, 0.0), max(3, int(fs)))
    return v, mov


def detect(
    t: np.ndarray,
    acc: np.ndarray,
    gyro: np.ndarray,
    gap: np.ndarray,
    fs: float,
    model: VehicleModel,
    vehicle: str,
    imu_bw_hz: float,
    pothole_m: float = 0.02,
    bump_m: float = 0.02,
    k_sigma: float = 4.0,
    dx: float = 0.05,
    echo_window_s: float = 4.0,
    echo_fmin_hz: float = 0.5,
    smooth_windows: int = 5,
    **wiener_kw,
) -> ImpactResult:
    """acc in m/s^2, gyro in rad/s, both in the sensor frame (no mounting calibration needed)."""
    notes: list[str] = []
    av, up = vertical_acceleration(acc, fs, gap)
    q, axis = pitch_rate_signal(gyro, up, fs, gap)
    wins = _Windows(fs, imu_bw_hz, echo_window_s, echo_fmin_hz)
    v0 = ASSUMED_SPEED.get(vehicle, 5.0)
    use_gyro = axis is not None
    if use_gyro:
        sign, clarity = resolve_sign(av, q, fs, model, v0, wins)
        if clarity < 1.3:
            use_gyro = False
        else:
            q, axis = sign * q, sign * axis
    if use_gyro:
        speed, moving = echo_speed(av, q, fs, model, vehicle, wins, smooth_windows=smooth_windows)
        source = "front-rear echo"
        if moving.sum() < 2 * fs:
            use_gyro = False
    if not use_gyro:
        notes.append(f"Bump detection: no usable gyro pitch signal, so it assumes {v0:g} m/s while the box vibrates.")
        q, axis = np.zeros_like(av), None
        speed, moving = _vibration_moving(acc, fs, v0)
        source = "assumed"

    road, valid = profile.wiener_profile(
        av, q, speed, fs, model, imu_bw_hz=imu_bw_hz, use_gyro=use_gyro, v_min=0.5, **wiener_kw
    )
    moving = moving & valid & (speed > 0.5)
    road = np.where(moving, road, 0.0)
    res = ImpactResult(t, av, q, speed, moving, road, pitch_axis=axis, speed_source=source, notes=notes)
    if moving.sum() < 2 * fs:
        return res
    dist = np.concatenate([[0.0], np.cumsum(0.5 * (speed[1:] + speed[:-1]) / fs)])
    s, (prof,) = profile.to_distance(dist, moving, dx, road)
    events = roughness.detect_events(s, prof, dx, pothole_m, bump_m, k_sigma=k_sigma)
    d_mov, t_mov = dist[moving], t[moving]
    keep = np.concatenate([[True], np.diff(d_mov) > 1e-6])
    d_mov, t_mov = d_mov[keep], t_mov[keep]
    for e in events:
        e["t_s"] = float(np.interp(e["peak_m"], d_mov, t_mov))
        e["t_start_s"] = float(np.interp(e["start_m"], d_mov, t_mov))
        e["t_end_s"] = float(np.interp(e["end_m"], d_mov, t_mov))
        e["speed_mps"] = float(np.interp(e["t_s"], t, speed))
    res.events = events
    return res


def _vibration_moving(acc: np.ndarray, fs: float, speed: float) -> tuple[np.ndarray, np.ndarray]:
    hp = signal.butter(2, 3.0, btype="high", fs=fs, output="sos")
    vib = np.sqrt(uniform_filter1d(signal.sosfiltfilt(hp, np.linalg.norm(acc, axis=1)) ** 2, int(fs)))
    moving = uniform_filter1d((vib > 0.3).astype(float), int(2 * fs)) > 0.5
    return uniform_filter1d(moving * speed, int(2 * fs)), moving
