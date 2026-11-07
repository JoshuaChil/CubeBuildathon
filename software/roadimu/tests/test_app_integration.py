"""The Road Reader app path: app CSV format, rebuilt timing, no-GPS fallback, live analysis."""

import time

import numpy as np
import pytest

from roadimu.appio import read_app_lines, uniform_timeline, write_app_csv
from roadimu.evaluate import score
from roadimu.live import LiveAnalyzer
from roadimu.simulate import CURRENT_FIRMWARE_IMU, SimConfig, app_rows, run_scenario
from roadimu.topo import TopoConfig, reconstruct


def test_timeline_rebuilt_from_bursty_ble_arrivals():
    rng = np.random.default_rng(0)
    n, period = 5000, 20.0
    sent = np.arange(n) * period
    keep = rng.random(n) >= 0.005
    arrive = np.ceil((sent + 2) / 30.0) * 30.0 + rng.uniform(0, 4, n)  # iOS: one delivery per 30 ms
    t_ms, rate, lost = uniform_timeline(arrive[keep])
    err = t_ms - (sent[keep] - sent[keep][0] + t_ms[0])
    assert abs(rate - 50.0) < 0.5
    assert abs(lost - (~keep).sum()) <= 6
    assert np.abs(err - np.median(err)).max() < 80  # ms


@pytest.fixture(scope="module")
def app_ride(tmp_path_factory):
    sim = run_scenario("potholes", "bike", seed=2, cfg=SimConfig(imu=CURRENT_FIRMWARE_IMU))
    rows = app_rows(sim, np.random.default_rng(3), poll_hz=200.0, device_timestamps=True)
    path = tmp_path_factory.mktemp("app") / "session_test.csv"
    write_app_csv(path, rows)
    return sim, rows, path


def test_app_csv_with_200hz_firmware_reconstructs(app_ride):
    sim, _, path = app_ride
    res = reconstruct(path, TopoConfig(vehicle="bike"))
    assert res.input_info["timing"] == "ESP32 clock"
    truth = dict(sim.truth)
    truth["meta"] = sim.meta
    m = score(res, truth, t_true=sim.raw["imu"].t_true[0] + res.t)
    assert m["profile_corr"] > 0.65  # this seed is a hard one: the schema-format path also scores 0.71 on it
    assert abs(m["iri_est_mean"] - m["iri_true_mean"]) < 3.0
    d = res.to_dict()
    assert {"summary", "segments", "events", "track", "notes"} <= set(d)


def test_no_gps_still_produces_a_profile(app_ride):
    _, rows, _ = app_ride
    no_gps = [r[:9] + ["", "", "", "", ""] for r in rows]
    log, info = read_app_lines([",".join(r) for r in no_gps])
    res = reconstruct(log, TopoConfig(vehicle="bike"))
    assert info["gps_rows"] == 0
    assert any("No GPS" in n for n in res.notes)
    assert res.s.size > 1000 and len(res.segments) > 5


def test_live_analyzer_reports_while_riding(app_ride):
    _, rows, _ = app_ride
    live = LiveAnalyzer(vehicle="bike", interval_s=0.2)
    for r in rows[: 200 * 40]:  # first 40 s
        live.add_line(",".join(r))
    deadline = time.time() + 30
    while time.time() < deadline and live.snapshot()["status"] != "ok":
        time.sleep(0.2)
    snap = live.snapshot()
    live.close()
    assert snap["status"] == "ok", snap["message"]
    assert snap["distance_m"] > 100
    assert snap["current"]["condition"] in ("good", "fair", "poor", "very poor")
