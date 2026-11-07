"""Full pipeline: IMU log (CSV in the docs/data-schema.md format) -> topography."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..appio import is_app_csv, read_app_csv
from ..io import ImuLog, read_imu_csv
from ..vehicle import nominal_model
from . import attitude, preprocess, profile, roughness


@dataclass
class TopoConfig:
    vehicle: str = "bike"  # which nominal vehicle model to invert
    imu_filter_bw_fraction: float = 0.25  # must match the IMU UI-filter setting in firmware
    imu_filter_bw_hz: float | None = None  # overrides the fraction (e.g. firmware that polls a faster IMU)
    assumed_speed_mps: float = 5.0  # used only when the log has no GPS at all
    imu_filter_order: int = 2
    dx: float = 0.05  # output profile resolution, m
    segment_m: float = 20.0  # roughness reporting segments
    window_s: float = 4.0
    prior_class: str = "D"
    acc_noise: float = 0.05  # m/s^2/sqrt(Hz): unmodelled vibration
    rate_noise: float = 0.005  # rad/s/sqrt(Hz)
    acc_lf_noise: float = 0.05  # extra low-frequency error (attitude lag, rider motion), at lf_corner_hz
    rate_lf_noise: float = 0.005
    lf_corner_hz: float = 1.0
    model_error: float = 0.3  # real vehicle differs from the nominal model by ~30 %
    lambda_max_m: float = 200.0  # longest wavelength kept (the Wiener prior already handles drift)
    lambda_min_m: float = 0.15
    use_gyro: bool = True
    pothole_mm: float = 20.0  # ~ the common "1 inch" pothole definition
    bump_mm: float = 20.0
    attitude_kp: float = 0.1
    attitude_ki: float = 0.002


@dataclass
class TopoResult:
    name: str
    config: TopoConfig
    calibration: preprocess.Calibration
    t0_ms: int
    # time base (s since first sample)
    t: np.ndarray
    speed: np.ndarray
    distance: np.ndarray
    pitch: np.ndarray
    roll: np.ndarray
    acc_z: np.ndarray
    road_t: np.ndarray  # road height under the front tyre, m
    baseline_t: np.ndarray  # naive double integration, m
    valid: np.ndarray
    moving: np.ndarray
    label_t: np.ndarray
    # distance base
    s: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    profile: np.ndarray  # surface profile, m
    elevation: np.ndarray  # hills, m (relative to start)
    segments: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # warnings about the input (no GPS, no still start...)
    input_info: dict = field(default_factory=dict)

    @property
    def topography(self) -> np.ndarray:
        return self.elevation + self.profile

    def to_dict(self, track_step_m: float = 2.0) -> dict:
        """Compact JSON-able result for the app / web (summary, segments, events, map track)."""
        def clean(x):
            if isinstance(x, float) and not np.isfinite(x):
                return None
            return x

        step = max(1, int(round(track_step_m / self.config.dx)))
        track = [
            {"s_m": round(float(self.s[i]), 1), "lat": clean(float(self.lat[i])), "lon": clean(float(self.lon[i])), "elevation_m": round(float(self.elevation[i]), 2)}
            for i in range(0, self.s.size, step)
        ]
        return {
            "summary": {k: (clean(v) if not isinstance(v, dict) else None) for k, v in self.summary().items() if k != "config"},
            "notes": self.notes,
            "input": self.input_info,
            "segments": [{k: clean(v) for k, v in sg.items()} for sg in self.segments],
            "events": [
                {"type": e["type"], "severity": round(e["severity"], 2), "magnitude_mm": round(e["magnitude_m"] * 1e3, 1), "length_m": round(e["end_m"] - e["start_m"], 2), "s_m": round(e["peak_m"], 1), "t_s": round(e["t_s"], 2), "lat": clean(e["lat"]), "lon": clean(e["lon"])}
                for e in self.events
            ],
            "track": track,
        }

    def summary(self) -> dict:
        iri = [s["iri_m_per_km"] for s in self.segments]
        return {
            "name": self.name,
            "vehicle": self.config.vehicle,
            "duration_s": float(self.t[-1]),
            "distance_m": float(self.s[-1] - self.s[0]) if self.s.size else 0.0,
            "mean_speed_mps": float(self.speed[self.moving].mean()) if self.moving.any() else 0.0,
            "box_tilt_deg": self.calibration.tilt_deg,
            "box_yaw_deg": self.calibration.yaw_deg,
            "elevation_range_m": float(np.ptp(self.elevation)) if self.s.size else 0.0,
            "mean_iri_m_per_km": float(np.mean(iri)) if iri else None,
            "potholes": sum(e["type"] == "pothole" for e in self.events),
            "bumps": sum(e["type"] == "bump" for e in self.events),
            "button_presses": int(self.label_t.size),
            "config": asdict(self.config),
        }

    def save(self, out_dir: str | Path) -> dict[str, Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {k: out / f"{self.name}_{k}.{ext}" for k, ext in (("topography", "csv"), ("segments", "csv"), ("events", "csv"), ("summary", "json"))}
        with paths["topography"].open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["s_m", "lat", "lon", "elevation_m", "profile_mm", "topography_m"])
            for i in range(self.s.size):
                w.writerow([f"{self.s[i]:.2f}", f"{self.lat[i]:.7f}", f"{self.lon[i]:.7f}", f"{self.elevation[i]:.3f}", f"{self.profile[i] * 1e3:.1f}", f"{self.topography[i]:.4f}"])
        with paths["segments"].open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["start_m", "end_m", "lat", "lon", "iri_m_per_km", "rms_mm", "condition"])
            for sg in self.segments:
                w.writerow([f"{sg['start_m']:.1f}", f"{sg['end_m']:.1f}", f"{sg['lat']:.7f}", f"{sg['lon']:.7f}", f"{sg['iri_m_per_km']:.2f}", f"{sg['rms_mm']:.1f}", sg["condition"]])
        with paths["events"].open("w", newline="") as fh:  # matches the `events` table in docs/data-schema.md
            w = csv.writer(fh)
            w.writerow(["t_s", "lat", "lon", "type", "severity", "source", "magnitude_mm", "s_m", "length_m"])
            for e in self.events:
                w.writerow([f"{e['t_s']:.2f}", f"{e['lat']:.7f}", f"{e['lon']:.7f}", e["type"], f"{e['severity']:.2f}", "model", f"{e['magnitude_m'] * 1e3:.1f}", f"{e['peak_m']:.1f}", f"{e['end_m'] - e['start_m']:.2f}"])
        paths["summary"].write_text(json.dumps(self.summary(), indent=2))
        return paths


def reconstruct(source: str | Path | ImuLog, cfg: TopoConfig | None = None, name: str | None = None) -> TopoResult:
    cfg = cfg or TopoConfig()
    notes: list[str] = []
    input_info: dict = {}
    imu_bw = cfg.imu_filter_bw_hz
    if isinstance(source, ImuLog):
        log, name = source, name or "session"
    elif is_app_csv(source):
        log, input_info = read_app_csv(source)
        name = name or Path(source).stem
        # The pushed firmware polls the ICM-42688 (1 kHz, ~250 Hz UI filter) every 20 ms.
        imu_bw = imu_bw or 250.0
    else:
        log = read_imu_csv(source)
        name = name or Path(source).stem.removesuffix("_imu")

    # 1. clean up + calibrate the mounting
    prep = preprocess.prepare(log)
    if cfg.vehicle == "bike" and prep.fs < 120:
        notes.append(
            f"Sample rate is {prep.fs:.0f} Hz. A bike frame vibrates at ~28-40 Hz, which aliases below 100 Hz "
            "and makes the bike road profile unreliable. Flash the 200 Hz firmware (esp32.ino)."
        )
    has_gps = prep.gps_t.size >= 3
    if has_gps:
        v_gps, dvdt = preprocess.gps_speed(prep)
    else:
        v_gps, dvdt = _speed_from_vibration(prep, cfg.assumed_speed_mps)
        notes.append(f"No GPS in this log: assumed {cfg.assumed_speed_mps:g} m/s while moving, so distances and the map are approximate.")
    cal = preprocess.calibrate(prep, v_gps if has_gps else np.zeros_like(prep.t), dvdt)
    if cal.still.stop - cal.still.start < prep.fs:
        notes.append("No clear standing-still period at the start: mounting calibration may be off. Stand still ~2 s after pressing Start.")
    acc_v = prep.acc @ cal.R.T
    gyro_v = (prep.gyro - cal.gyro_bias) @ cal.R.T

    # 2. attitude, speed, distance
    att = attitude.estimate(prep.t, prep.fs, acc_v, gyro_v, v_gps, dvdt, cal.g_local, cfg.attitude_kp, cfg.attitude_ki)
    if not has_gps:
        att.speed = v_gps
        att.distance = np.concatenate([[0.0], np.cumsum(0.5 * (v_gps[1:] + v_gps[:-1]) / prep.fs)])
        att.moving = v_gps > 0.5

    # 3. road profile under the front tyre
    # Lost BLE packets: a straight line between two random points of a 2 g vibration has a
    # random offset that the deconvolution turns into a fake pothole.  Fill gaps with the
    # expected value of the dynamic signals instead (zero).
    acc_z = np.where(prep.gap, 0.0, att.acc_z_dyn)
    pitch_rate = np.where(prep.gap, 0.0, att.pitch_rate_dyn)
    model = nominal_model(cfg.vehicle)
    road_t, valid = profile.wiener_profile(
        acc_z,
        pitch_rate,
        att.speed,
        prep.fs,
        model,
        imu_bw_hz=imu_bw or cfg.imu_filter_bw_fraction * prep.fs,
        imu_filter_order=cfg.imu_filter_order,
        window_s=cfg.window_s,
        prior_class=cfg.prior_class,
        acc_noise=cfg.acc_noise,
        rate_noise=cfg.rate_noise,
        acc_lf_noise=cfg.acc_lf_noise,
        rate_lf_noise=cfg.rate_lf_noise,
        lf_corner_hz=cfg.lf_corner_hz,
        model_error=cfg.model_error,
        lambda_max_m=cfg.lambda_max_m,
        lambda_min_m=cfg.lambda_min_m,
        use_gyro=cfg.use_gyro and has_gps,  # without GPS we can't find "forward", so pitch rate is unreliable
    )
    baseline_t = profile.double_integration(acc_z, prep.fs)

    # 4. onto distance, add hills, roughness and events
    if has_gps:
        lat_t = np.interp(prep.t, prep.gps_t, prep.gps_lat)
        lon_t = np.interp(prep.t, prep.gps_t, prep.gps_lon)
    else:
        lat_t = lon_t = np.full(prep.t.size, np.nan)
    moving = att.moving & valid
    if moving.sum() < 2 * prep.fs:
        raise ValueError("Not enough moving data to analyse yet (need a few seconds of riding).")
    s, (prof, pitch_s, lat_s, lon_s) = profile.to_distance(att.distance, moving, cfg.dx, road_t, att.pitch, lat_t, lon_t)
    elev = attitude.elevation_from_pitch(s, pitch_s, cfg.dx)

    segs = roughness.segment_stats(s, prof, cfg.dx, cfg.segment_m)
    for sg in segs:
        mid = 0.5 * (sg["start_m"] + sg["end_m"])
        sg["lat"], sg["lon"] = float(np.interp(mid, s, lat_s)), float(np.interp(mid, s, lon_s))
    events = roughness.detect_events(s, prof, cfg.dx, cfg.pothole_mm / 1e3, cfg.bump_mm / 1e3)
    d_mov, t_mov = att.distance[moving], prep.t[moving]
    for e in events:
        e["lat"], e["lon"] = float(np.interp(e["peak_m"], s, lat_s)), float(np.interp(e["peak_m"], s, lon_s))
        e["t_s"] = float(np.interp(e["peak_m"], d_mov, t_mov))

    return TopoResult(
        name=name,
        config=cfg,
        calibration=cal,
        t0_ms=prep.t0_ms,
        t=prep.t,
        speed=att.speed,
        distance=att.distance,
        pitch=att.pitch,
        roll=att.roll,
        acc_z=acc_z,
        road_t=road_t,
        baseline_t=baseline_t,
        valid=valid,
        moving=moving,
        label_t=prep.label_t,
        s=s,
        lat=lat_s,
        lon=lon_s,
        profile=prof,
        elevation=elev,
        segments=segs,
        events=events,
        notes=notes,
        input_info=input_info,
    )


def _speed_from_vibration(prep: preprocess.Prepared, speed: float) -> tuple[np.ndarray, np.ndarray]:
    """No GPS: call it 'moving' when the box vibrates like it's on a road, at an assumed speed."""
    from scipy import signal
    from scipy.ndimage import uniform_filter1d

    hp = signal.butter(2, 3.0, btype="high", fs=prep.fs, output="sos")
    vib = np.sqrt(uniform_filter1d(signal.sosfiltfilt(hp, np.linalg.norm(prep.acc, axis=1)) ** 2, int(prep.fs)))
    moving = uniform_filter1d((vib > 0.3).astype(float), int(2 * prep.fs)) > 0.5  # > 0.03 g RMS
    v = uniform_filter1d(moving * speed, int(2 * prep.fs))
    return v, np.gradient(v, 1.0 / prep.fs)
