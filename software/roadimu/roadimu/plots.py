"""Figures: a per-session report (no ground truth needed) and an evaluation figure (with truth)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .road import EVENT_KINDS  # noqa: E402
from .topo import TopoResult  # noqa: E402

COND_COLORS = {"good": "#2e9e4f", "fair": "#e0b000", "poor": "#e06000", "very poor": "#c0182b"}


def _condition_map(ax, res: TopoResult) -> None:
    for sg in res.segments:
        sel = (res.s >= sg["start_m"]) & (res.s <= sg["end_m"])
        ax.plot(res.lon[sel], res.lat[sel], color=COND_COLORS[sg["condition"]], lw=4, solid_capstyle="round")
    for e in res.events:
        ax.plot(e["lon"], e["lat"], "v" if e["magnitude_m"] < 0 else "^", color="k", ms=6)
    ax.set_aspect(1.0 / np.cos(np.deg2rad(np.nanmean(res.lat))) if res.lat.size else 1.0)
    ax.set_title("Road condition map (IRI per segment; ▼ dip ▲ bump)")
    ax.set_xticks([])
    ax.set_yticks([])
    for name, c in COND_COLORS.items():
        ax.plot([], [], color=c, lw=4, label=name)
    ax.legend(loc="best", fontsize=8)


def report_figure(res: TopoResult, path: str | Path) -> Path:
    fig, axes = plt.subplots(4, 1, figsize=(12, 13), gridspec_kw={"height_ratios": [1, 1.3, 1, 1.6]})
    ax = axes[0]
    ax.plot(res.t, res.acc_z / 9.81, lw=0.4, color="0.5", label="vertical accel (g)")
    ax2 = ax.twinx()
    ax2.plot(res.t, res.speed, color="C0", label="speed (m/s)")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("accel (g)")
    ax2.set_ylabel("speed (m/s)")
    ax.set_title(f"{res.name}: raw vibration and speed")
    ax = axes[1]
    ax.plot(res.s, res.profile * 1e3, lw=0.6, color="k")
    for e in res.events:
        ax.axvspan(e["start_m"], e["end_m"], color="C3" if e["magnitude_m"] < 0 else "C2", alpha=0.3)
    ax.set_ylabel("surface (mm)")
    ax.set_title("Reconstructed road surface profile (red = dip, green = bump)")
    ax = axes[2]
    ax.plot(res.s, res.elevation, color="C1")
    ax.set_ylabel("elevation (m)")
    ax.set_xlabel("distance (m)")
    ax.set_title("Hills (from pitch)")
    _condition_map(axes[3], res)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def evaluation_figure(res: TopoResult, truth: dict, metrics: dict, path: str | Path) -> Path:
    g, est, env, base = metrics["_grid"], metrics["_est"], metrics["_env"], metrics["_base"]
    fig = plt.figure(figsize=(14, 15))
    gs = fig.add_gridspec(5, 2, height_ratios=[1.2, 1, 1, 1, 1.4])
    ax = fig.add_subplot(gs[0, :])
    ax.plot(g, env * 1e3, color="0.6", lw=1.2, label="truth (what the tyre felt)")
    ax.plot(g, est * 1e3, color="C0", lw=0.7, label=f"reconstructed (corr {metrics['profile_corr']:.2f})")
    for e in truth["events"]:
        if e["kind"] in EVENT_KINDS:
            ax.axvspan(e["start_m"], e["end_m"], color="k", alpha=0.08)
    for d in metrics["_detections"]:
        ax.plot(d["x_true"], d["magnitude_m"] * 1e3, "v" if d["magnitude_m"] < 0 else "^", color="C3", ms=6)
    ax.set_title(f"{res.name}: road surface profile vs truth (grey bands = true features, red = detections)")
    ax.set_ylabel("mm")
    ax.legend(loc="upper right", fontsize=8)

    # zoom on the biggest true feature
    feats = [e for e in truth["events"] if e["kind"] in EVENT_KINDS] or [{"start_m": g[g.size // 2], "end_m": g[g.size // 2] + 1}]
    big = max(feats, key=lambda e: abs(e.get("felt_m", 0)))
    sel = (g > big["start_m"] - 6) & (g < big["end_m"] + 6)
    ax = fig.add_subplot(gs[1, 0])
    ax.plot(g[sel], env[sel] * 1e3, color="0.6", lw=2, label="truth")
    ax.plot(g[sel], est[sel] * 1e3, color="C0", label="model-based")
    ax.plot(g[sel], base[sel] * 1e3, color="C1", lw=0.8, ls="--", label=f"double integration (corr {metrics['baseline_corr']:.2f})")
    ax.set_title(f"zoom: {big.get('kind', '')}")
    ax.set_xlabel("distance (m)")
    ax.set_ylabel("mm")
    ax.legend(fontsize=8)

    ax = fig.add_subplot(gs[1, 1])
    mid = g.size // 2
    sel = slice(mid, min(g.size, mid + int(8.0 / (g[1] - g[0]))))
    ax.plot(g[sel], env[sel] * 1e3, color="0.6", lw=2, label="truth")
    ax.plot(g[sel], est[sel] * 1e3, color="C0", label="model-based")
    ax.set_title("zoom: 8 m of ordinary surface")
    ax.set_xlabel("distance (m)")
    ax.legend(fontsize=8)

    ax = fig.add_subplot(gs[2, :])
    centers = 0.5 * (metrics["_iri_edges"][:-1] + metrics["_iri_edges"][1:])
    w = 0.4 * (metrics["_iri_edges"][1] - metrics["_iri_edges"][0])
    ax.bar(centers - w / 2, metrics["_iri_true"], width=w, color="0.6", label="true IRI")
    ax.bar(centers + w / 2, metrics["_iri_est"], width=w, color="C0", label="estimated IRI")
    ax.set_ylabel("IRI (m/km)")
    ax.set_title(f"Roughness per 20 m (mean abs error {metrics['iri_mae']:.2f} m/km)")
    ax.legend(fontsize=8)

    ax = fig.add_subplot(gs[3, :])
    ax.plot(g, metrics["_elev_true"], color="0.6", lw=2, label="true elevation")
    ax.plot(g, metrics["_elev_est"], color="C1", label=f"estimated (RMSE {metrics['elevation_rmse_m']:.2f} m)")
    ax.set_ylabel("m")
    ax.set_xlabel("distance (m)")
    ax.set_title("Hills")
    ax.legend(fontsize=8)

    _condition_map(fig.add_subplot(gs[4, 0]), res)
    ax = fig.add_subplot(gs[4, 1])
    ax.axis("off")
    p = {k: v for k, v in metrics.items() if not k.startswith("_")}
    lines = [
        f"profile correlation   {p['profile_corr']:.2f}   (naive double integration {p['baseline_corr']:.2f})",
        f"profile RMSE          {p['profile_rmse_mm']:.1f} mm  (truth RMS {p['truth_rms_mm']:.1f} mm)",
        f"IRI estimated / true  {p['iri_est_mean']:.1f} / {p['iri_true_mean']:.1f} m/km",
        f"events found          {p['events_found']} / {p['events_expected']}   false: {p['false_detections']}",
        f"depth error (found)   {p['depth_error_mm']:+.1f} mm",
        f"elevation RMSE        {p['elevation_rmse_m']:.2f} m",
        f"distance error        {p['distance_error_pct']:+.1f} %",
        f"box yaw est / true    {p['yaw_est_deg']:+.1f} / {-p['yaw_true_deg']:+.1f} deg",
    ]
    ax.text(0, 1, "\n".join(lines), va="top", family="monospace", fontsize=10)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path
