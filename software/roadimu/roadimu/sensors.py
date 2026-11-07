"""Sensor models: ICM-42688-P IMU, phone GPS, and the rider's label button.

The IMU model follows the ICM-42688-P datasheet: noise density, initial
bias, sensitivity and cross-axis error, the on-chip low-pass ("UI") filter,
the output data rate (ODR), saturation at full scale and 16-bit quantisation.
It also models the ESP32 clock (ppm drift) and lost BLE packets.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

from . import G
from .road import enu_to_latlon


@dataclass(frozen=True)
class ImuSpec:
    odr_hz: float = 200.0
    accel_fsr_g: float = 16.0
    gyro_fsr_dps: float = 2000.0
    accel_noise_ug_rthz: float = 70.0  # datasheet: 65-70 ug/sqrt(Hz)
    gyro_noise_mdps_rthz: float = 2.8
    accel_bias_mg: float = 20.0  # 1-sigma initial offset per axis
    gyro_bias_dps: float = 0.5
    accel_scale_error: float = 0.005
    gyro_scale_error: float = 0.005
    cross_axis: float = 0.01
    filter_bw_fraction: float = 0.25  # UI low-pass bandwidth = fraction * ODR
    filter_order: int = 2
    clock_ppm: float = 30.0  # ESP32 crystal error
    packet_samples: int = 10  # samples per BLE notification
    packet_loss: float = 0.005  # probability a notification is lost

    @property
    def filter_bw_hz(self) -> float:
        return self.filter_bw_fraction * self.odr_hz


def imu_filter_response(freqs: np.ndarray, bw_hz: float, order: int = 2) -> np.ndarray:
    """Complex response of the IMU's low-pass filter (used by the reconstruction)."""
    b, a = signal.butter(order, 2 * np.pi * bw_hz, analog=True)
    _, h = signal.freqs(b, a, worN=2 * np.pi * np.asarray(freqs, dtype=float))
    return h


def rotation_xyz(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """R = Rz(yaw) @ Ry(pitch) @ Rx(roll)."""
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    Rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    Ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    Rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _misalignment(rng: np.random.Generator, scale_err: float, cross: float) -> np.ndarray:
    S = np.eye(3) + np.diag(rng.normal(0.0, scale_err, 3))
    off = rng.normal(0.0, cross / 2.0, (3, 3))
    np.fill_diagonal(off, 0.0)
    return S + off


@dataclass
class ImuSamples:
    t_true: np.ndarray  # true time of each output sample, s
    acc_g: np.ndarray  # (M, 3) in g, sensor frame
    gyro_dps: np.ndarray  # (M, 3) in deg/s, sensor frame
    info: dict


def measure_imu(
    spec: ImuSpec,
    fs_sim: float,
    f_body: np.ndarray,
    w_body: np.ndarray,
    mount_rpy_rad: tuple[float, float, float],
    rng: np.random.Generator,
) -> ImuSamples:
    """Turn true specific force (m/s^2) and angular rate (rad/s) in the vehicle frame into IMU output."""
    Rm = rotation_xyz(*mount_rpy_rad)  # sensor axes expressed in the vehicle frame
    f_s = f_body @ Rm  # = (Rm.T @ f).T  row-wise
    w_s = w_body @ Rm
    f_s = f_s @ _misalignment(rng, spec.accel_scale_error, spec.cross_axis).T
    w_s = w_s @ _misalignment(rng, spec.gyro_scale_error, spec.cross_axis).T

    acc_bias = rng.normal(0.0, spec.accel_bias_mg * 1e-3 * G, 3)
    gyro_bias = np.deg2rad(rng.normal(0.0, spec.gyro_bias_dps, 3))
    n = f_s.shape[0]
    sig_a = spec.accel_noise_ug_rthz * 1e-6 * G * np.sqrt(fs_sim / 2.0)
    sig_g = np.deg2rad(spec.gyro_noise_mdps_rthz * 1e-3) * np.sqrt(fs_sim / 2.0)
    f_s = f_s + acc_bias + rng.normal(0.0, sig_a, (n, 3))
    w_s = w_s + gyro_bias + rng.normal(0.0, sig_g, (n, 3))

    sos = signal.butter(spec.filter_order, spec.filter_bw_hz, fs=fs_sim, output="sos")
    zi = signal.sosfilt_zi(sos)
    f_f = np.column_stack([signal.sosfilt(sos, f_s[:, i], zi=zi * f_s[0, i])[0] for i in range(3)])
    w_f = np.column_stack([signal.sosfilt(sos, w_s[:, i], zi=zi * w_s[0, i])[0] for i in range(3)])

    dec = int(round(fs_sim / spec.odr_hz))
    idx = np.arange(int(rng.integers(dec)), n, dec)
    acc_g = f_f[idx] / G
    gyro_dps = np.rad2deg(w_f[idx])
    a_lsb = spec.accel_fsr_g / 32768.0
    g_lsb = spec.gyro_fsr_dps / 32768.0
    acc_g = np.clip(np.round(acc_g / a_lsb) * a_lsb, -spec.accel_fsr_g, spec.accel_fsr_g - a_lsb)
    gyro_dps = np.clip(np.round(gyro_dps / g_lsb) * g_lsb, -spec.gyro_fsr_dps, spec.gyro_fsr_dps - g_lsb)
    info = {
        "accel_bias_mg": (acc_bias / G * 1e3).tolist(),
        "gyro_bias_dps": np.rad2deg(gyro_bias).tolist(),
        "mount_rpy_deg": np.rad2deg(mount_rpy_rad).tolist(),
        "saturated_fraction": float(np.mean(np.abs(acc_g) >= spec.accel_fsr_g - a_lsb)),
    }
    return ImuSamples(idx / fs_sim, acc_g, gyro_dps, info)


def esp32_timestamps(spec: ImuSpec, t_true: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, dict]:
    """millis() timestamps from a drifting clock that started before the session."""
    ppm = rng.normal(0.0, spec.clock_ppm)
    t0 = float(rng.uniform(5.0, 60.0))  # ESP32 booted a while ago
    t_ms = np.floor((t0 + t_true * (1.0 + ppm * 1e-6)) * 1000.0).astype(np.int64)
    return t_ms, {"clock_offset_s": t0, "clock_ppm": ppm}


def packet_loss_mask(spec: ImuSpec, n: int, rng: np.random.Generator) -> np.ndarray:
    """True for samples that arrive at the phone."""
    n_packets = int(np.ceil(n / spec.packet_samples))
    lost = rng.random(n_packets) < spec.packet_loss
    return ~np.repeat(lost, spec.packet_samples)[:n]


@dataclass(frozen=True)
class GpsSpec:
    rate_hz: float = 1.0
    pos_sigma_m: float = 3.0  # phone GPS in a city: a few metres, slowly wandering
    pos_tau_s: float = 30.0
    speed_sigma_mps: float = 0.10  # Doppler speed is much better than position


@dataclass
class GpsFixes:
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    speed: np.ndarray


def simulate_gps(
    spec: GpsSpec,
    t: np.ndarray,
    east: np.ndarray,
    north: np.ndarray,
    speed: np.ndarray,
    origin: tuple[float, float],
    rng: np.random.Generator,
) -> GpsFixes:
    t_fix = np.arange(0.0, t[-1], 1.0 / spec.rate_hz)
    e = np.interp(t_fix, t, east)
    nn = np.interp(t_fix, t, north)
    v = np.interp(t_fix, t, speed)
    phi = np.exp(-1.0 / (spec.rate_hz * spec.pos_tau_s))
    err = np.zeros((t_fix.size, 2))
    err[0] = rng.normal(0.0, spec.pos_sigma_m, 2)
    for k in range(1, t_fix.size):
        err[k] = phi * err[k - 1] + np.sqrt(1 - phi**2) * rng.normal(0.0, spec.pos_sigma_m, 2)
    lat, lon = enu_to_latlon(e + err[:, 0], nn + err[:, 1], origin)
    v_meas = np.where(v > 0.3, v + rng.normal(0.0, spec.speed_sigma_mps, v.size), np.abs(rng.normal(0.0, 0.03, v.size)))
    return GpsFixes(t_fix, lat, lon, np.maximum(v_meas, 0.0))


def simulate_button(
    event_times: np.ndarray,
    event_magnitudes: np.ndarray,
    t_end: float,
    rng: np.random.Generator,
    noticeable_m: float = 0.015,
    press_prob: float = 0.85,
    delay_mean_s: float = 0.6,
    delay_sd_s: float = 0.2,
    false_per_min: float = 0.3,
) -> np.ndarray:
    """Times the rider presses the 'bump' button: late, sometimes missed, sometimes by accident."""
    presses = []
    for t_e, mag in zip(event_times, event_magnitudes):
        if abs(mag) >= noticeable_m and rng.random() < press_prob:
            presses.append(t_e + float(np.clip(rng.normal(delay_mean_s, delay_sd_s), 0.25, 1.5)))
    n_false = rng.poisson(false_per_min * t_end / 60.0)
    presses.extend(rng.uniform(0.0, t_end, n_false).tolist())
    return np.sort(np.array([p for p in presses if p < t_end]))
