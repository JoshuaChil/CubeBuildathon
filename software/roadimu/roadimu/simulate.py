"""Run a scenario end to end.

road -> speed plan -> vehicle dynamics (with tyre lift-off, pedalling / engine
vibration, lean in turns, hills) -> true specific force + angular rate at the
IMU -> ICM-42688-P model -> BLE packet loss + ESP32 clock -> phone GPS + rider
button -> CSV in the docs/data-schema.md format, plus ground truth for evaluation.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d

from . import G
from .io import ImuLog, write_imu_csv
from .road import Road, build_road, tyre_envelope
from .scenarios import SCENARIOS
from .sensors import (
    GpsSpec,
    ImuSpec,
    esp32_timestamps,
    measure_imu,
    packet_loss_mask,
    simulate_button,
    simulate_gps,
)
from .vehicle import BikeParams, CarParams, VehicleModel, bike_model, car_model

SLOW_FOR = ("speed_bump", "speed_hump", "raised_crosswalk")


@dataclass
class SimConfig:
    fs_sim: float = 2000.0
    imu: ImuSpec = field(default_factory=ImuSpec)
    gps: GpsSpec = field(default_factory=GpsSpec)
    param_spread: float = 0.12  # how different the real vehicle is from the nominal model
    mount_tilt_deg: float = 4.0  # box not perfectly level (1-sigma)
    mount_yaw_deg: float = 15.0  # box not pointing straight ahead (uniform +-)
    still_start_s: float = 3.0
    still_end_s: float = 2.0


@dataclass
class SimResult:
    name: str
    vehicle_kind: str
    road: Road
    log: ImuLog
    truth: dict
    meta: dict
    raw: dict = field(default_factory=dict)  # full-rate IMU output, GPS fixes, button presses


# The ESP32 firmware in "groundtruth_code/esp32/esp32.ino" as pushed: the ICM-42688 runs at its
# library default (1 kHz, UI filter ~ODR/4) and loop() polls the latest value every 20 ms.
CURRENT_FIRMWARE_IMU = ImuSpec(odr_hz=1000.0, filter_bw_fraction=0.25)


def _smooth_noise(n: int, width: int, rng: np.random.Generator) -> np.ndarray:
    z = uniform_filter1d(rng.normal(size=n), max(1, width), mode="wrap")
    return z / max(np.std(z), 1e-12)


def _speed_plan(road: Road, kind: str, cruise: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    """Target speed along the road (m/s) and the stopping position."""
    x, dx = road.x, road.dx
    v = np.full(x.size, cruise)
    for seg in road.segments:
        v[(x >= seg["start_m"]) & (x < seg["end_m"])] *= seg["speed_factor"]
    for e in road.events:
        if e["kind"] in SLOW_FOR:
            sel = (x > e["start_m"] - 15.0) & (x < e["end_m"] + 3.0)
            v[sel] = np.minimum(v[sel], 3.5 if kind == "car" else 0.7 * cruise)
    v = uniform_filter1d(v, int(8.0 / dx), mode="nearest")
    v *= 1.0 + 0.05 * _smooth_noise(x.size, int(30.0 / dx), rng)  # rider/driver doesn't hold speed exactly
    x_stop = road.length_m - 8.0
    brake = 1.0 if kind == "bike" else 2.0
    v = np.minimum(v, np.sqrt(2.0 * brake * np.maximum(x_stop - x, 0.0)))
    return v, x_stop


def _kinematics(road: Road, v_target: np.ndarray, x_stop: float, x0: float, kind: str, cfg: SimConfig):
    dt = 1.0 / cfg.fs_sim
    acc_max, brake = (0.8, 1.2) if kind == "bike" else (1.5, 2.5)
    n_still0 = int(cfg.still_start_s * cfg.fs_sim)
    xs, vs = [x0] * n_still0, [0.0] * n_still0
    x, v, n = x0, 0.0, v_target.size
    while True:
        vt = v_target[min(int(x / road.dx), n - 1)]
        a = min(max(1.2 * (vt - v), -brake), acc_max)
        v = max(0.0, v + a * dt)
        x += v * dt
        xs.append(x)
        vs.append(v)
        if (x > x_stop - 3.0 and v < 0.05) or x > road.length_m - 2.0 or len(xs) > 2_000_000:
            break
    n_end = int(cfg.still_end_s * cfg.fs_sim)
    xs += [x] * n_end
    vs += [0.0] * n_end
    xs, vs = np.array(xs), np.array(vs)
    t = np.arange(xs.size) * dt
    return t, xs, vs, np.gradient(vs, dt)


def _external_forces(model: VehicleModel, t, v, a_long, rng) -> tuple[np.ndarray, np.ndarray]:
    """Pedalling / rider shifting (bike) or engine vibration (car).  Also returns cadence/engine freq."""
    n = t.size
    dt = t[1] - t[0]
    fext = np.zeros((n, model.ndof))
    if model.kind == "bike":
        cadence = 1.4 * (1.0 + 0.05 * _smooth_noise(n, int(5.0 / dt), rng))  # rev/s (~84 rpm)
        pedalling = uniform_filter1d(((v > 1.0) & (a_long > -0.3)).astype(float), int(0.5 / dt))
        phase = 2.0 * np.pi * np.cumsum(cadence) * dt
        f_pedal = 70.0 * np.cos(2.0 * phase) * pedalling  # two power strokes per revolution
        f_rider = 25.0 * _smooth_noise(n, int(1.0 / dt), rng)
        fext += f_pedal[:, None] * model.force_rows["pedal"] + f_rider[:, None] * model.force_rows["saddle"]
        return fext, cadence * pedalling
    rpm = np.where(v < 1.0, 850.0, 1300.0 + 60.0 * v)
    phase = 2.0 * np.pi * np.cumsum(rpm / 30.0) * dt  # 4-cylinder: 2nd engine order
    fext += (120.0 * np.sin(phase))[:, None] * model.force_rows["engine"]
    return fext, rpm / 30.0


def run_scenario(
    scenario: str,
    vehicle: str = "bike",
    seed: int = 0,
    cfg: SimConfig | None = None,
    out_dir: str | Path | None = None,
) -> SimResult:
    cfg = cfg or SimConfig()
    sc = SCENARIOS[scenario]
    rng = np.random.default_rng(seed)
    road = build_road(sc.build(rng), rng, max_grade=sc.max_grade)

    nominal = BikeParams() if vehicle == "bike" else CarParams()
    true_params = nominal.perturbed(rng, cfg.param_spread)
    model = bike_model(true_params) if vehicle == "bike" else car_model(true_params)
    wb = model.wheelbase

    # ---- motion along the road
    v_target, x_stop = _speed_plan(road, vehicle, sc.cruise_mps[vehicle], rng)
    t, x, v, a_long = _kinematics(road, v_target, x_stop, wb + 3.0, vehicle, cfg)
    dt = 1.0 / cfg.fs_sim

    # ---- road input under each tyre (what the tyre can actually follow)
    env = tyre_envelope(road.micro, road.dx, model.tyre_radius, model.patch_m)
    denv = np.gradient(env, road.dx)
    u = np.column_stack([road.at(env, x), road.at(env, x - wb)])
    udot = np.column_stack([road.at(denv, x), road.at(denv, x - wb)]) * v[:, None]

    fext, excitation_hz = _external_forces(model, t, v, a_long, rng)
    dyn = model.simulate(dt, u, udot, fext)
    qdd_pitch = dyn["qdd"][:, model.pitch_idx]

    # ---- attitude: hills, turns, lean
    grade = np.gradient(road.macro, road.dx)
    x_mid = x - wb / 2.0
    gamma = np.arctan(road.at(grade, x_mid))
    dgamma_dx = road.at(np.gradient(np.arctan(grade), road.dx), x_mid)
    macro_curv = road.at(np.gradient(grade, road.dx), x)
    theta = gamma + dyn["q"][:, model.pitch_idx]  # nose-up pitch
    theta_dot = v * dgamma_dx + dyn["qd"][:, model.pitch_idx]

    yaw_rate_path = v * road.at(road.curvature, x)
    steer_sd = np.deg2rad(1.5 if vehicle == "bike" else 0.3)
    yaw_rate = yaw_rate_path + steer_sd * _smooth_noise(t.size, int(0.7 / dt), rng) * (v > 0.5)
    lat_acc = v * uniform_filter1d(yaw_rate_path, int(0.5 / dt))
    if vehicle == "bike":
        rock = np.deg2rad(1.2) * np.sin(2.0 * np.pi * np.cumsum(excitation_hz) * dt)
        roll = -np.arctan(lat_acc / G) + rock  # leans into the turn (negative roll = left side down)
    else:
        roll = 0.06 * lat_acc / G  # body rolls outwards
    roll_dot = np.gradient(roll, dt)

    # ---- specific force in the level heading frame, then rotate into the vehicle body
    a_vert = dyn["qdd"] @ model.mount_row + v**2 * macro_curv
    fx, fy, fz = a_long, v * yaw_rate, a_vert + G
    ct, st, cr, sr = np.cos(theta), np.sin(theta), np.cos(roll), np.sin(roll)
    f1x, f1z = ct * fx + st * fz, -st * fx + ct * fz
    f_body = np.column_stack([f1x - model.mount_h * qdd_pitch, cr * fy + sr * f1z, -sr * fy + cr * f1z])

    beta, beta_dot = -theta, -theta_dot  # rotation angle about +y (left) is minus nose-up pitch
    w_body = np.column_stack(
        [
            roll_dot - yaw_rate * np.sin(beta),
            beta_dot * cr + yaw_rate * np.cos(beta) * sr,
            yaw_rate * np.cos(beta) * cr - beta_dot * sr,
        ]
    )

    # ---- sensors
    mount = (
        np.deg2rad(rng.normal(0.0, cfg.mount_tilt_deg)),
        np.deg2rad(rng.normal(0.0, cfg.mount_tilt_deg)),
        np.deg2rad(rng.uniform(-cfg.mount_yaw_deg, cfg.mount_yaw_deg)),
    )
    imu = measure_imu(cfg.imu, cfg.fs_sim, f_body, w_body, mount, rng)
    t_ms, clock = esp32_timestamps(cfg.imu, imu.t_true, rng)
    keep = packet_loss_mask(cfg.imu, imu.t_true.size, rng)

    east, north = road.at(road.east, x), road.at(road.north, x)
    gps = simulate_gps(cfg.gps, t, east, north, v, (road.lat[0], road.lon[0]), rng)

    # ---- ground-truth events and the rider pressing the button
    events = []
    for e in road.events:
        sel = (road.x >= e["start_m"]) & (road.x <= e["end_m"])
        around = ((road.x >= e["start_m"] - 1.0) & (road.x < e["start_m"])) | ((road.x > e["end_m"]) & (road.x <= e["end_m"] + 1.0))
        rel = env[sel] - np.median(env[around])
        felt = rel.min() if e["magnitude_m"] < 0 else rel.max()
        center = 0.5 * (e["start_m"] + e["end_m"])
        i = int(np.searchsorted(x, center))
        if i >= x.size:
            continue
        events.append({**e, "felt_m": float(felt), "t_front_s": float(t[i])})
    presses = simulate_button(
        np.array([e["t_front_s"] for e in events]), np.array([e["felt_m"] for e in events]), t[-1], rng
    )

    t_kept = imu.t_true[keep]
    fix_idx = np.clip(np.searchsorted(gps.t, t_kept, side="right") - 1, 0, gps.t.size - 1)
    label = np.zeros(t_kept.size, dtype=bool)
    for p in presses:
        j = int(np.searchsorted(t_kept, p))
        if j < t_kept.size:
            label[j] = True
    log = ImuLog(
        t_ms=t_ms[keep],
        lat=gps.lat[fix_idx],
        lon=gps.lon[fix_idx],
        speed=gps.speed[fix_idx],
        acc_g=imu.acc_g[keep],
        gyro_dps=imu.gyro_dps[keep],
        label=label,
    )

    name = f"{scenario}_{vehicle}"
    truth = {
        "t": t,
        "x_front": x,
        "speed": v,
        "u_front": u[:, 0],
        "pitch": theta,
        "roll": roll,
        "tyre_force": dyn["tyre_force"],
        "road_x": road.x,
        "road_micro": road.micro,
        "road_envelope": env,
        "road_macro": road.macro,
        "road_lat": road.lat,
        "road_lon": road.lon,
        "events": events,
        "presses": presses,
    }
    airborne = np.mean(dyn["tyre_force"] <= 0.0, axis=0)
    meta = {
        "name": name,
        "scenario": scenario,
        "description": sc.description,
        "vehicle": vehicle,
        "seed": seed,
        "duration_s": float(t[-1]),
        "distance_m": float(x[-1] - x[0]),
        "fs_sim_hz": cfg.fs_sim,
        "imu_spec": asdict(cfg.imu),
        "gps_spec": asdict(cfg.gps),
        "imu": imu.info,
        "clock": clock,
        "samples_written": int(keep.sum()),
        "samples_lost": int((~keep).sum()),
        "button_presses": int(len(presses)),
        "true_vehicle_params": {k: v_ for k, v_ in asdict(true_params).items()},
        "tyre_airborne_fraction": airborne.tolist(),
        "segments": road.segments,
        "events": events,
    }
    raw = {"imu": imu, "gps": gps, "presses": presses, "clock": clock, "origin": (road.lat[0], road.lon[0]), "macro_at": lambda xq: road.at(road.macro, xq), "x_at": lambda tq: np.interp(tq, t, x)}
    result = SimResult(name, vehicle, road, log, truth, meta, raw)
    if out_dir is not None:
        save(result, out_dir)
    return result


def app_rows(
    result: SimResult,
    rng: np.random.Generator,
    poll_hz: float = 50.0,
    device_timestamps: bool = False,
    notification_loss: float = 0.005,
    ble_interval_ms: float = 30.0,
    fix_after_s: float = 3.0,
    mark_s: float = 0.5,
) -> list[list]:
    """Rows exactly as the iPhone app would write them (see appio.APP_COLUMNS).

    Emulates the ESP32 loop() polling the IMU every 1/poll_hz (no anti-alias averaging), iOS
    delivering BLE notifications once per connection event (bursty arrival times), the phone's
    1 Hz GPS (blank until the first fix), and the Detect Bump button held for ``mark_s``.
    """
    imu, gps = result.raw["imu"], result.raw["gps"]
    t_out = np.arange(imu.t_true[0], imu.t_true[-1], 1.0 / poll_hz)
    idx = np.clip(np.searchsorted(imu.t_true, t_out, side="right") - 1, 0, imu.t_true.size - 1)  # latest value
    acc, gyro = imu.acc_g[idx], imu.gyro_dps[idx]
    clk = result.raw["clock"]
    millis = np.floor((clk["clock_offset_s"] + t_out * (1 + clk["clock_ppm"] * 1e-6)) * 1000).astype(np.int64)
    epoch0 = 1_794_000_000_000 + int(rng.integers(0, 10**9))
    send = t_out * 1000.0 + 2.0
    arrive = np.ceil(send / ble_interval_ms) * ble_interval_ms + rng.uniform(0, 4, send.size)
    phone_ms = (epoch0 + arrive).astype(np.int64)
    keep = rng.random(t_out.size) >= notification_loss

    fix_t = gps.t
    fix_idx = np.searchsorted(fix_t, t_out, side="right") - 1
    alt = result.raw["macro_at"](result.raw["x_at"](fix_t)) + 12.0 + rng.normal(0, 3.0, fix_t.size)
    hacc = np.clip(rng.normal(6.0, 2.0, fix_t.size), 3.0, None)
    held = np.zeros(t_out.size, dtype=bool)
    for p in result.raw["presses"]:
        held |= (t_out >= p) & (t_out < p + mark_s)

    rows = []
    for i in np.flatnonzero(keep):
        a, g = acc[i], gyro[i]
        row = [f"{a[0]:.4f}", f"{a[1]:.4f}", f"{a[2]:.4f}", f"{g[0]:.4f}", f"{g[1]:.4f}", f"{g[2]:.4f}", "1" if held[i] else "0", str(phone_ms[i]), str(millis[i]) if device_timestamps else ""]
        k = fix_idx[i]
        if k >= 0 and fix_t[k] >= fix_after_s:
            row += [f"{gps.lat[k]:.6f}", f"{gps.lon[k]:.6f}", f"{alt[k]:.1f}", f"{max(gps.speed[k], 0):.2f}", f"{hacc[k]:.1f}"]
        else:
            row += ["", "", "", "", ""]
        rows.append(row)
    return rows


def save(result: SimResult, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "imu": out / f"{result.name}_imu.csv",
        "truth": out / f"{result.name}_truth.npz",
        "events": out / f"{result.name}_truth_events.csv",
        "meta": out / f"{result.name}_meta.json",
    }
    write_imu_csv(paths["imu"], result.log)
    tr = result.truth
    np.savez_compressed(
        paths["truth"],
        **{k: np.asarray(v, dtype=np.float32 if k.startswith("road_") or k in ("u_front", "pitch", "roll") else float) for k, v in tr.items() if k not in ("events", "tyre_force")},
        tyre_force=tr["tyre_force"].astype(np.float32),
    )
    with paths["events"].open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "start_m", "end_m", "magnitude_mm", "felt_mm", "t_front_s", "segment"])
        for e in tr["events"]:
            w.writerow([e["kind"], f"{e['start_m']:.2f}", f"{e['end_m']:.2f}", f"{e['magnitude_m'] * 1e3:.1f}", f"{e['felt_m'] * 1e3:.1f}", f"{e['t_front_s']:.3f}", e["segment"]])
    paths["meta"].write_text(json.dumps(result.meta, indent=2))
    return paths
