"""Step 3: road surface profile from vibration.

The IMU doesn't measure the road directly; it measures how the bike/car *responds*
to the road.  That response is known from the vehicle model: road height
under the front tyre -> (vertical acceleration at the box, pitch rate), with
the rear tyre seeing the same road ``wheelbase / speed`` later.  We invert it
with a multi-channel Wiener filter in short overlapping windows (speed is
roughly constant inside each window):

    R(f) = sum_c conj(H_c) Y_c / N_c  /  ( sum_c |H_c|^2 / N_c + 1 / S_road )

* H_c      model response of channel c (accel, gyro pitch rate) incl. the IMU's own low-pass filter
* Y_c      measured spectrum
* N_c      noise: sensor noise + model error (real rider/tyres differ from the nominal model)
* S_road   prior road PSD (ISO 8608) at the window's speed

Using the gyro as well as the accelerometer separates front-wheel and
rear-wheel hits: they pitch the frame in opposite directions.

A naive alternative (double-integrating acceleration with high-pass filters)
is included as a baseline; it ignores the vehicle dynamics.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

from ..road import iso8608_psd
from ..sensors import imu_filter_response
from ..vehicle import VehicleModel


def road_psd_time(freqs: np.ndarray, speed: float, road_class: str) -> np.ndarray:
    """ISO 8608 road PSD seen in time at a given speed (m^2/Hz)."""
    n = np.maximum(np.asarray(freqs) / max(speed, 0.1), 0.011)
    return iso8608_psd(n, road_class) / max(speed, 0.1)


def wiener_profile(
    acc_z: np.ndarray,
    pitch_rate: np.ndarray,
    speed: np.ndarray,
    fs: float,
    model: VehicleModel,
    imu_bw_hz: float,
    imu_filter_order: int = 2,
    window_s: float = 4.0,
    prior_class: str = "D",
    acc_noise: float = 0.05,
    rate_noise: float = 0.005,
    acc_lf_noise: float = 0.1,
    rate_lf_noise: float = 0.01,
    lf_corner_hz: float = 1.0,
    model_error: float = 0.3,
    lambda_max_m: float = 10.0,
    lambda_min_m: float = 0.15,
    v_min: float = 1.0,
    use_gyro: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Road height under the front tyre vs time (m), and a validity mask."""
    n = acc_z.size
    nw = int(round(window_s * fs))
    nw += nw % 2
    if n < nw:
        pad = nw - n
        acc_z, pitch_rate, speed = (np.pad(a, (0, pad)) for a in (acc_z, pitch_rate, speed))
    N = acc_z.size
    hop = nw // 4
    nfft = 2 * nw
    win = signal.windows.hann(nw, sym=False)
    freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
    f_safe = np.maximum(freqs, freqs[1])
    h_imu = imu_filter_response(freqs, imu_bw_hz, imu_filter_order)
    # Below ~1 Hz the "noise" is dominated by attitude-estimate lag (e.g. leaning into a
    # corner), rider movement and speed changes - not by the sensor.  It grows as 1/f.
    lf = (lf_corner_hz / f_safe) ** 2
    acc_floor = acc_noise**2 + acc_lf_noise**2 * lf
    rate_floor = rate_noise**2 + rate_lf_noise**2 * lf
    starts = list(range(0, N - nw + 1, hop))
    if starts[-1] != N - nw:
        starts.append(N - nw)

    out = np.zeros(N)
    norm = np.zeros(N)
    frf_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for s0 in starts:
        sl = slice(s0, s0 + nw)
        v = float(np.mean(speed[sl]))
        if v < v_min:
            continue
        key = round(v, 1)
        if key not in frf_cache:
            ha, hq = model.frf(freqs, key)
            frf_cache[key] = (ha * h_imu, hq * h_imu)
        ha, hq = frf_cache[key]
        s_road = road_psd_time(freqs, v, prior_class)
        ya = np.fft.rfft(acc_z[sl] * win, nfft)
        na = acc_floor + (model_error * np.abs(ha)) ** 2 * s_road
        num = np.conj(ha) * ya / na
        den = np.abs(ha) ** 2 / na + 1.0 / s_road
        if use_gyro:
            yq = np.fft.rfft(pitch_rate[sl] * win, nfft)
            nq = rate_floor + (model_error * np.abs(hq)) ** 2 * s_road
            num = num + np.conj(hq) * yq / nq
            den = den + np.abs(hq) ** 2 / nq
        est = num / den
        est *= 1.0 / (1.0 + ((v / lambda_max_m) / f_safe) ** 4)  # drop wavelengths the IMU can't see
        est *= 1.0 / (1.0 + (freqs / (v / lambda_min_m)) ** 8)
        est[0] = 0.0
        r = np.fft.irfft(est, nfft)[:nw]
        out[sl] += r * win
        norm[sl] += win**2
    valid = norm > 0.05
    out[valid] /= norm[valid]
    out[~valid] = 0.0
    return out[:n], valid[:n]


def double_integration(acc_z: np.ndarray, fs: float, f_hp: float = 0.5) -> np.ndarray:
    """Baseline: integrate acceleration twice, high-passing at every stage to stop drift."""
    sos = signal.butter(2, f_hp, btype="high", fs=fs, output="sos")
    dt = 1.0 / fs
    vel = signal.sosfiltfilt(sos, np.cumsum(signal.sosfiltfilt(sos, acc_z)) * dt)
    return signal.sosfiltfilt(sos, np.cumsum(vel) * dt)


def to_distance(distance: np.ndarray, moving: np.ndarray, dx: float, *series: np.ndarray):
    """Resample time series onto a uniform distance grid (only where the vehicle moves)."""
    d = distance[moving]
    keep = np.concatenate([[True], np.diff(d) > 1e-6])
    d = d[keep]
    s = np.arange(np.ceil(d[0] / dx) * dx, d[-1], dx)
    return s, [np.interp(s, d, x[moving][keep]) for x in series]
