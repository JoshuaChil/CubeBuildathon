"""Step 2: attitude, speed, distance and hills.

A Mahony complementary filter tracks which way is up.  The gyro is trusted
over short times; the accelerometer corrects it slowly.  Before the
accelerometer is used, the acceleration we already know from GPS is removed:
speeding up / braking (dv/dt) and the centripetal acceleration in turns
(v * yaw rate).  Without that, braking would look like riding downhill, and
leaning a bike into a corner would look like a tilted road.

Pitch then gives the road grade, and integrating grade over distance gives the
hills (macro topography).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal


@dataclass
class Attitude:
    pitch: np.ndarray  # nose-up, rad
    roll: np.ndarray  # rad
    speed: np.ndarray  # fused GPS + IMU speed, m/s
    distance: np.ndarray  # m
    acc_z_dyn: np.ndarray  # vertical (body z) acceleration minus gravity and known motion, m/s^2
    pitch_rate_dyn: np.ndarray  # high-passed body pitch rate (nose-up), rad/s
    moving: np.ndarray  # bool


def estimate(
    t: np.ndarray,
    fs: float,
    acc: np.ndarray,
    gyro: np.ndarray,
    v_gps: np.ndarray,
    dvdt: np.ndarray,
    g_local: float,
    kp: float = 0.1,
    ki: float = 0.002,
    acc_lp_hz: float = 1.5,
    speed_gain: float = 0.3,
) -> Attitude:
    """acc/gyro must already be in the vehicle frame with gyro bias removed."""
    n = t.size
    dt = 1.0 / fs
    # Vibration rectification ("sculling"): when the frame pitches nose-up at the same moment
    # it accelerates upwards, part of that vertical acceleration lands on the x axis, and the
    # product doesn't average to zero.  On a rigid bike this alone biases pitch by ~0.3 deg
    # (= 3 m of fake hill per km).  Both factors are measured, so subtract the product.
    hp = signal.butter(2, 0.5, btype="high", fs=fs, output="sos")
    pitch_vib = signal.sosfiltfilt(hp, np.cumsum(signal.sosfiltfilt(hp, -gyro[:, 1])) * dt)
    roll_vib = signal.sosfiltfilt(hp, np.cumsum(signal.sosfiltfilt(hp, gyro[:, 0])) * dt)
    acc = acc.copy()
    a_vert = acc[:, 2] - g_local
    acc[:, 0] -= np.sin(pitch_vib) * a_vert
    acc[:, 1] -= np.sin(roll_vib) * a_vert
    sos = signal.butter(2, acc_lp_hz, fs=fs, output="sos")
    acc_lp = signal.sosfiltfilt(sos, acc, axis=0)

    q0, q1, q2, q3 = 1.0, 0.0, 0.0, 0.0
    ix = iy = iz = 0.0
    v_hat = 0.0
    pitch, roll, speed = np.empty(n), np.empty(n), np.empty(n)
    acc_z = np.empty(n)
    for k in range(n):
        # rotation body -> world from the quaternion
        r00 = 1 - 2 * (q2 * q2 + q3 * q3)
        r01 = 2 * (q1 * q2 - q0 * q3)
        r02 = 2 * (q1 * q3 + q0 * q2)
        r10 = 2 * (q1 * q2 + q0 * q3)
        r11 = 1 - 2 * (q1 * q1 + q3 * q3)
        r12 = 2 * (q2 * q3 - q0 * q1)
        r20 = 2 * (q1 * q3 - q0 * q2)
        r21 = 2 * (q2 * q3 + q0 * q1)
        r22 = 1 - 2 * (q1 * q1 + q2 * q2)
        gx, gy, gz = gyro[k]

        # horizontal forward/left directions in the world frame
        h = np.hypot(r00, r10) or 1.0
        fx_, fy_ = r00 / h, r10 / h
        yaw_rate = r20 * gx + r21 * gy + r22 * gz
        aw_x = dvdt[k] * fx_ - v_gps[k] * yaw_rate * fy_
        aw_y = dvdt[k] * fy_ + v_gps[k] * yaw_rate * fx_
        # known acceleration in the body frame: R^T a_world (a_world has no z part)
        kb_x, kb_y, kb_z = r00 * aw_x + r10 * aw_y, r01 * aw_x + r11 * aw_y, r02 * aw_x + r12 * aw_y

        mx, my, mz = acc_lp[k, 0] - kb_x, acc_lp[k, 1] - kb_y, acc_lp[k, 2] - kb_z
        nm = np.sqrt(mx * mx + my * my + mz * mz)
        if 0.7 * g_local < nm < 1.3 * g_local:
            mx, my, mz = mx / nm, my / nm, mz / nm
            ex, ey, ez = my * r22 - mz * r21, mz * r20 - mx * r22, mx * r21 - my * r20  # meas x est(up in body)
            ix += ki * ex * dt
            iy += ki * ey * dt
            iz += ki * ez * dt
            wx, wy, wz = gx + kp * ex + ix, gy + kp * ey + iy, gz + kp * ez + iz
        else:
            wx, wy, wz = gx + ix, gy + iy, gz + iz

        # outputs for this sample
        pitch[k] = np.arcsin(max(-1.0, min(1.0, r20)))
        roll[k] = np.arctan2(r21, r22)
        a = acc[k]
        acc_z[k] = a[2] - g_local * r22 - kb_z  # body-z specific force minus gravity & known motion
        a_fwd = (r00 * a[0] + r01 * a[1] + r02 * a[2]) * fx_ + (r10 * a[0] + r11 * a[1] + r12 * a[2]) * fy_
        if v_gps[k] < 0.2 and abs(dvdt[k]) < 0.05:
            v_hat = 0.0
        else:
            v_hat = max(0.0, v_hat + dt * (a_fwd + speed_gain * (v_gps[k] - v_hat)))
        speed[k] = v_hat

        # integrate the quaternion
        dq0 = 0.5 * (-q1 * wx - q2 * wy - q3 * wz)
        dq1 = 0.5 * (q0 * wx + q2 * wz - q3 * wy)
        dq2 = 0.5 * (q0 * wy - q1 * wz + q3 * wx)
        dq3 = 0.5 * (q0 * wz + q1 * wy - q2 * wx)
        q0, q1, q2, q3 = q0 + dq0 * dt, q1 + dq1 * dt, q2 + dq2 * dt, q3 + dq3 * dt
        nq = np.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        q0, q1, q2, q3 = q0 / nq, q1 / nq, q2 / nq, q3 / nq

    distance = np.concatenate([[0.0], np.cumsum(0.5 * (speed[1:] + speed[:-1]) * dt)])
    hp = signal.butter(2, 0.3, btype="high", fs=fs, output="sos")
    pitch_rate = -signal.sosfiltfilt(hp, gyro[:, 1])  # gyro y is about the left axis; nose-up = negative
    acc_z = signal.sosfiltfilt(signal.butter(2, 0.1, btype="high", fs=fs, output="sos"), acc_z)
    return Attitude(pitch, roll, speed, distance, acc_z, pitch_rate, speed > 0.5)


def elevation_from_pitch(s: np.ndarray, pitch_s: np.ndarray, dx: float, min_wavelength_m: float = 30.0) -> np.ndarray:
    """Hills: integrate sin(pitch) over distance, keeping only long wavelengths."""
    grade = np.sin(pitch_s)
    if s.size > 30:
        sos = signal.butter(2, 1.0 / min_wavelength_m, fs=1.0 / dx, output="sos")
        grade = signal.sosfiltfilt(sos, grade)
    return np.concatenate([[0.0], np.cumsum(0.5 * (grade[1:] + grade[:-1]) * np.diff(s))])
