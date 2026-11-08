"""Score a reconstruction against simulator ground truth.

The estimate lives on the sensor's own clock and its own (GPS/IMU) distance.
For scoring the *shape* of the profile we map it onto the true distance with
the known clock model.  Distance-estimation error is reported separately.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy import signal

from .road import EVENT_KINDS
from .topo import TopoConfig, TopoResult, reconstruct
from .topo.roughness import detect_events, iri_slope


def load_truth(sim_dir: str | Path, name: str) -> dict:
    sim_dir = Path(sim_dir)
    tr = dict(np.load(sim_dir / f"{name}_truth.npz"))
    meta = json.loads((sim_dir / f"{name}_meta.json").read_text())
    tr["meta"] = meta
    tr["events"] = meta["events"]
    return tr


def _bandpass(x: np.ndarray, dx: float, lam_min: float, lam_max: float) -> np.ndarray:
    sos = signal.butter(2, [1.0 / lam_max, 1.0 / lam_min], btype="band", fs=1.0 / dx, output="sos")
    return signal.sosfiltfilt(sos, x)


def true_time(res: TopoResult, meta: dict) -> np.ndarray:
    """Map the estimate's time base (sensor clock) to simulator time."""
    clk = meta["clock"]
    t_sensor = res.t + res.t0_ms / 1000.0
    return (t_sensor - clk["clock_offset_s"]) / (1.0 + clk["clock_ppm"] * 1e-6)


def score(
    res: TopoResult,
    truth: dict,
    dx: float = 0.05,
    band=(0.3, 8.0),
    segment_m: float = 20.0,
    tol_m: float = 1.5,
    t_true: np.ndarray | None = None,
) -> dict:
    """``t_true``: simulator time of each estimate sample, if the default clock model doesn't apply
    (e.g. app logs whose time base was rebuilt from phone arrival times)."""
    meta = truth["meta"]
    tt = true_time(res, meta) if t_true is None else t_true
    x_true = np.interp(tt, truth["t"], truth["x_front"])
    use = res.moving & (x_true > truth["x_front"][0] + 2.0)
    xs, order = np.unique(x_true[use], return_index=True)
    grid = np.arange(xs[0] + 5.0, xs[-1] - 5.0, dx)

    def on_grid(series_t):
        return np.interp(grid, xs, series_t[use][order])

    est = on_grid(res.road_t)
    base = on_grid(res.baseline_t)
    env = np.interp(grid, truth["road_x"], truth["road_envelope"])
    raw = np.interp(grid, truth["road_x"], truth["road_micro"])

    def compare(a, b):
        a_bp, b_bp = _bandpass(a, dx, *band), _bandpass(b, dx, *band)
        return float(np.corrcoef(a_bp, b_bp)[0, 1]), float(np.sqrt(np.mean((a_bp - b_bp) ** 2)) * 1e3), float(np.std(b_bp) * 1e3)

    corr, rmse, truth_rms = compare(est, env)
    corr_b, rmse_b, _ = compare(base, env)

    # IRI per segment (true distance)
    sl_est, sl_true = iri_slope(est, dx), iri_slope(raw, dx)
    edges = np.arange(grid[0], grid[-1], segment_m)
    iri_e, iri_t = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (grid >= a) & (grid < b)
        iri_e.append(sl_est[sel].mean() * 1000)
        iri_t.append(sl_true[sel].mean() * 1000)
    iri_e, iri_t = np.array(iri_e), np.array(iri_t)

    # hills
    s_est_t = np.interp(res.distance, res.s, res.elevation) if res.s.size else np.zeros_like(res.t)
    elev_est = on_grid(s_est_t)
    elev_true = np.interp(grid, truth["road_x"], truth["road_macro"])
    elev_err = (elev_est - elev_est[0]) - (elev_true - elev_true[0])

    # distance
    moved_true = truth["x_front"][-1] - truth["x_front"][0]
    dist_err_pct = float((res.distance[-1] - moved_true) / moved_true * 100.0)

    # events: map detections to true distance via time
    det = []
    for e in res.events:  # events carry their own time (they're found from the IMU alone)
        x_at = lambda ts: float(np.interp(np.interp(ts, res.t, tt), truth["t"], truth["x_front"]))
        det.append({**e, "x_true": x_at(e["t_s"]), "x0_true": x_at(e["t_start_s"]), "x1_true": x_at(e["t_end_s"])})
    thr = res.config.pothole_mm / 1e3
    tru = [e for e in truth["events"] if e["kind"] in EVENT_KINDS]
    expected = [e for e in tru if abs(e["felt_m"]) >= 1.3 * thr]
    matched_truth, matched_det, depth_err = set(), set(), []
    for i, e in enumerate(tru):
        for j, d in enumerate(det):
            close = e["start_m"] - tol_m <= d["x_true"] <= e["end_m"] + tol_m
            same_sign = (d["magnitude_m"] < 0) == (e["felt_m"] < 0)
            if close and same_sign:
                matched_det.add(j)
                if i not in matched_truth:
                    matched_truth.add(i)
                    if abs(e["felt_m"]) >= 1.3 * thr:
                        depth_err.append(abs(d["magnitude_m"]) * 1e3 - abs(e["felt_m"]) * 1e3)
    # A detection that isn't a listed feature is still real if the true surface has a
    # same-sign dip/rise there (loose gravel stones, slab faulting, long-wave waviness).
    surface = detect_events(grid, env, dx, 0.6 * thr, 0.6 * res.config.bump_mm / 1e3, k_sigma=0.0)
    for j, d in enumerate(det):
        if j in matched_det:
            continue
        half = 0.5 * abs(d["x1_true"] - d["x0_true"]) + tol_m
        if any((t["magnitude_m"] < 0) == (d["magnitude_m"] < 0) and t["start_m"] - half <= d["x_true"] <= t["end_m"] + half for t in surface):
            matched_det.add(j)
    n_exp = len(expected)
    found = sum(1 for i, e in enumerate(tru) if i in matched_truth and abs(e["felt_m"]) >= 1.3 * thr)
    return {
        "name": res.name,
        "profile_corr": corr,
        "profile_rmse_mm": rmse,
        "truth_rms_mm": truth_rms,
        "baseline_corr": corr_b,
        "baseline_rmse_mm": rmse_b,
        "iri_true_mean": float(iri_t.mean()) if iri_t.size else float("nan"),
        "iri_est_mean": float(iri_e.mean()) if iri_e.size else float("nan"),
        "iri_mae": float(np.mean(np.abs(iri_e - iri_t))) if iri_t.size else float("nan"),
        "iri_corr": float(np.corrcoef(iri_e, iri_t)[0, 1]) if iri_t.size > 2 else float("nan"),
        "elevation_rmse_m": float(np.sqrt(np.mean(elev_err**2))),
        "elevation_range_true_m": float(np.ptp(elev_true)),
        "distance_error_pct": dist_err_pct,
        "events_expected": n_exp,
        "events_found": found,
        "events_detected": len(det),
        "false_detections": len(det) - len(matched_det),
        "depth_error_mm": float(np.mean(depth_err)) if depth_err else float("nan"),
        "yaw_est_deg": res.calibration.yaw_deg,
        "yaw_true_deg": meta["imu"]["mount_rpy_deg"][2],
        "_grid": grid,
        "_est": est,
        "_env": env,
        "_base": base,
        "_elev_est": elev_est - elev_est[0],
        "_elev_true": elev_true - elev_true[0],
        "_iri_est": iri_e,
        "_iri_true": iri_t,
        "_iri_edges": edges,
        "_detections": det,
    }


def public(metrics: dict) -> dict:
    return {k: v for k, v in metrics.items() if not k.startswith("_")}


def run(
    scenarios: list[str],
    vehicles: list[str],
    out_dir: str | Path = "out",
    seed: int = 0,
    cfg_overrides: dict | None = None,
    plots: bool = True,
    verbose: bool = True,
) -> list[dict]:
    from .simulate import run_scenario

    out = Path(out_dir)
    rows = []
    for sc in scenarios:
        for veh in vehicles:
            sim = run_scenario(sc, veh, seed=seed, out_dir=out / "sim")
            cfg = TopoConfig(vehicle=veh, **(cfg_overrides or {}))
            res = reconstruct(out / "sim" / f"{sim.name}_imu.csv", cfg)
            res.save(out / "topo")
            truth = load_truth(out / "sim", sim.name)
            m = score(res, truth)
            if plots:
                from .plots import evaluation_figure

                evaluation_figure(res, truth, m, out / "plots" / f"{sim.name}.png")
            rows.append(m)
            if verbose:
                p = public(m)
                print(
                    f"{sim.name:24s} corr {p['profile_corr']:.2f} (baseline {p['baseline_corr']:.2f})  "
                    f"rmse {p['profile_rmse_mm']:.1f}/{p['truth_rms_mm']:.1f} mm  "
                    f"IRI {p['iri_est_mean']:.1f} vs {p['iri_true_mean']:.1f}  "
                    f"events {p['events_found']}/{p['events_expected']} (+{p['false_detections']} false)  "
                    f"elev {p['elevation_rmse_m']:.2f} m  dist {p['distance_error_pct']:+.1f}%"
                )
    summary = [public(r) for r in rows]
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    write_markdown(summary, out / "summary.md")
    return rows


def write_markdown(rows: list[dict], path: Path) -> None:
    lines = [
        "| Run | Profile corr | Baseline corr | RMSE / truth RMS (mm) | IRI est / true (m/km) | Events found | False | Elev RMSE (m) | Dist err |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['profile_corr']:.2f} | {r['baseline_corr']:.2f} | {r['profile_rmse_mm']:.1f} / {r['truth_rms_mm']:.1f} | "
            f"{r['iri_est_mean']:.1f} / {r['iri_true_mean']:.1f} | {r['events_found']}/{r['events_expected']} | {r['false_detections']} | "
            f"{r['elevation_rmse_m']:.2f} | {r['distance_error_pct']:+.1f}% |"
        )
    path.write_text("\n".join(lines) + "\n")
