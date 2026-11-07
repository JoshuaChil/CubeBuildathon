"""Checks that the simulator's building blocks behave physically."""

import numpy as np

from roadimu import G
from roadimu.road import ISO8608_GD0, N0, iso8608_unit_profile, tyre_envelope
from roadimu.sensors import ImuSpec, measure_imu
from roadimu.vehicle import BikeParams, CarParams, bike_model, car_model


def test_iso8608_profile_has_the_right_roughness():
    rng = np.random.default_rng(0)
    dx, n = 0.01, 2**17
    z = np.sqrt(ISO8608_GD0["C"]) * iso8608_unit_profile(n, dx, rng)
    freqs = np.fft.rfftfreq(n, dx)
    psd = 2 * np.abs(np.fft.rfft(z)) ** 2 * dx / n  # one-sided PSD, m^3
    band = (freqs > 0.05) & (freqs < 2.0)
    measured = np.median(psd[band] / (freqs[band] / N0) ** -2.0)
    assert 0.5 * ISO8608_GD0["C"] < measured < 1.5 * ISO8608_GD0["C"]


def test_tyre_cannot_drop_into_a_narrow_crack():
    dx = 0.005
    z = np.zeros(2000)
    z[1000:1004] = -0.02  # 2 cm wide, 2 cm deep
    env = tyre_envelope(z, dx, radius_m=0.34, patch_m=0.08)
    assert -0.005 < env.min() < 0.0


def test_static_tyre_loads_carry_the_whole_vehicle():
    bike, car = BikeParams(), CarParams()
    for model, mass in (
        (bike_model(bike), bike.frame_mass + bike.rider_mass),
        (car_model(car), car.body_mass + car.wheel_mass_front + car.wheel_mass_rear),
    ):
        _, comp = model.static()
        assert np.all(comp > 0)
        assert np.isclose((model.kt * comp).sum(), mass * G, rtol=1e-6)


def test_vehicle_follows_slow_road_waves():
    for model in (bike_model(), car_model()):
        acc, _ = model.frf(np.array([0.05]), speed=5.0)
        assert np.isclose(np.abs(acc[0]) / (2 * np.pi * 0.05) ** 2, 1.0, atol=0.02)


def test_time_simulation_matches_frequency_response():
    """The time simulation (no lift-off at this amplitude) agrees with the analytic response."""
    model = car_model()
    fs, f0, v, amp = 2000.0, 3.0, 8.0, 0.003
    t = np.arange(0, 12, 1 / fs)
    tau = model.wheelbase / v
    u = np.column_stack([amp * np.sin(2 * np.pi * f0 * t), amp * np.sin(2 * np.pi * f0 * (t - tau))])
    out = model.simulate(1 / fs, u, np.gradient(u, axis=0) * fs)
    a = out["qdd"] @ model.mount_row
    sim_amp = np.sqrt(2) * np.std(a[t > 6])
    acc, _ = model.frf(np.array([f0]), v)
    assert np.isclose(sim_amp, amp * abs(acc[0]), rtol=0.05)


def test_imu_at_rest_reads_one_g_in_whole_lsbs():
    rng = np.random.default_rng(1)
    fs, n = 2000.0, 4000
    spec = ImuSpec()
    out = measure_imu(spec, fs, np.tile([0.0, 0.0, G], (n, 1)), np.zeros((n, 3)), (0.0, 0.0, 0.0), rng)
    assert abs(np.linalg.norm(out.acc_g.mean(axis=0)) - 1.0) < 0.1  # within bias + scale error
    dec = int(fs / spec.odr_hz)
    assert n // dec - 1 <= out.acc_g.shape[0] <= n // dec + 1
    lsb = spec.accel_fsr_g / 32768
    assert np.allclose(out.acc_g / lsb, np.round(out.acc_g / lsb))
