"""Checks for the signal-processing side, including one full simulate -> reconstruct run."""

import numpy as np
import pytest

from roadimu.evaluate import score
from roadimu.simulate import run_scenario
from roadimu.topo import TopoConfig, reconstruct
from roadimu.topo.roughness import detect_events, iri_slope


def test_iri_of_a_flat_road_is_zero():
    assert iri_slope(np.zeros(4000), 0.05).mean() == pytest.approx(0.0, abs=1e-12)


def test_iri_scales_with_roughness():
    rng = np.random.default_rng(0)
    z = np.cumsum(rng.normal(size=4000)) * 1e-4
    a, b = iri_slope(z, 0.05).mean(), iri_slope(2 * z, 0.05).mean()
    assert b == pytest.approx(2 * a, rel=1e-6)


def test_detects_a_pothole_and_a_hump():
    dx = 0.05
    s = np.arange(0, 100, dx)
    z = np.zeros_like(s)
    z[(s > 30) & (s < 30.6)] = -0.04
    z[(s > 60) & (s < 63.7)] = 0.07 * np.sin(np.pi * (s[(s > 60) & (s < 63.7)] - 60) / 3.7) ** 2
    ev = detect_events(s, z, dx)
    kinds = {(e["type"], round(e["peak_m"])) for e in ev}
    assert any(k == "pothole" and abs(p - 30) <= 1 for k, p in kinds)
    assert any(k == "bump" and abs(p - 62) <= 1 for k, p in kinds)
    assert len(ev) == 2


@pytest.fixture(scope="module")
def pothole_ride(tmp_path_factory):
    out = tmp_path_factory.mktemp("sim")
    sim = run_scenario("potholes", "bike", seed=3, out_dir=out)
    csv_path = out / f"{sim.name}_imu.csv"
    res = reconstruct(csv_path, TopoConfig(vehicle="bike"))
    truth = dict(np.load(out / f"{sim.name}_truth.npz"))
    truth["meta"] = sim.meta
    truth["events"] = sim.meta["events"]
    return sim, res, truth, csv_path


def test_csv_follows_the_data_schema(pothole_ride):
    sim, _, _, csv_path = pothole_ride
    header = csv_path.read_text().splitlines()[0]
    assert header == "t_ms,lat,lon,speed_mps,ax,ay,az,gx,gy,gz,label"  # docs/data-schema.md section 4
    assert np.all(np.diff(sim.log.t_ms) > 0)
    assert sim.log.label.any()  # the simulated rider pressed the bump button


def test_mounting_calibration(pothole_ride):
    sim, res, _, _ = pothole_ride
    yaw_err = res.calibration.yaw_deg + sim.meta["imu"]["mount_rpy_deg"][2]
    assert abs(yaw_err) < 8.0


def test_reconstruction_matches_truth(pothole_ride):
    _, res, truth, _ = pothole_ride
    m = score(res, truth)
    assert m["profile_corr"] > 0.8
    assert m["profile_corr"] > m["baseline_corr"]
    assert m["events_found"] >= m["events_expected"] - 2
    assert m["false_detections"] <= 2  # bike over 7 potholes: ~1 false alarm per ride is typical (see README)
    assert abs(m["distance_error_pct"]) < 3.0
    assert abs(m["iri_est_mean"] - m["iri_true_mean"]) < 2.5
