"""Vehicle vertical dynamics: pitch-plane ("half") models of a bike and a car.

Each model is a linear multi-body system driven by the road height under the
front and rear tyre (the rear sees the same road, ``wheelbase / speed`` later).
Tyres are one-sided springs, so in the time simulation a wheel can leave the
ground over a pothole or a speed bump.

The simulator uses randomly *perturbed* parameters (every rider, bike, tyre
pressure and car is different).  The reconstruction uses the *nominal* model,
linearised, so it is tested against model mismatch the way it will be with real data.

Coordinates: x forward, z up.  ``pitch`` is nose-up positive (rad).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from . import G


def _perturb(params, rng: np.random.Generator, spread: float, names: tuple[str, ...]):
    return replace(params, **{n: getattr(params, n) * float(np.exp(rng.normal(0.0, spread))) for n in names})


@dataclass(frozen=True)
class BikeParams:
    """Road bike (rigid fork) + rider.  DOFs: frame heave, frame pitch, rider heave."""

    frame_mass: float = 10.0  # bike incl. wheels, kg
    frame_pitch_inertia: float = 1.1  # kg m^2 about the frame CG
    front_axle_m: float = 0.55  # ahead of the frame CG
    rear_axle_m: float = 0.45  # behind the frame CG
    rider_mass: float = 75.0
    saddle_x: float = -0.25
    saddle_k: float = 35e3
    saddle_c: float = 900.0
    hands_x: float = 0.42
    hands_k: float = 10e3
    hands_c: float = 350.0
    pedal_x: float = -0.02
    pedal_k: float = 25e3
    pedal_c: float = 600.0
    tyre_k_front: float = 120e3  # ~6 bar road tyre, N/m
    tyre_k_rear: float = 150e3
    tyre_c: float = 60.0
    tyre_radius: float = 0.34  # 700c
    patch_m: float = 0.08
    mount_x: float = 0.45  # IMU box on the handlebar
    mount_h: float = 0.50  # height of the box above the frame CG

    UNCERTAIN = (
        "frame_mass", "frame_pitch_inertia", "rider_mass", "saddle_k", "saddle_c", "hands_k", "hands_c",
        "pedal_k", "pedal_c", "tyre_k_front", "tyre_k_rear", "tyre_c",
    )  # fmt: skip

    def perturbed(self, rng: np.random.Generator, spread: float = 0.12) -> "BikeParams":
        return _perturb(self, rng, spread, self.UNCERTAIN)


@dataclass(frozen=True)
class CarParams:
    """Sedan, one track lumped per axle.  DOFs: body heave, body pitch, front/rear wheel hop."""

    body_mass: float = 1400.0
    body_pitch_inertia: float = 2200.0
    front_axle_m: float = 1.15
    rear_axle_m: float = 1.55
    wheel_mass_front: float = 80.0  # both front wheels
    wheel_mass_rear: float = 70.0
    susp_k_front: float = 48e3
    susp_k_rear: float = 42e3
    susp_c_front: float = 3200.0
    susp_c_rear: float = 2800.0
    tyre_k: float = 440e3
    tyre_c: float = 300.0
    tyre_radius: float = 0.31
    patch_m: float = 0.15
    mount_x: float = 0.90  # phone/box on the dashboard, ahead of the CG
    mount_h: float = 0.50
    engine_x: float = 1.30

    UNCERTAIN = (
        "body_mass", "body_pitch_inertia", "wheel_mass_front", "wheel_mass_rear", "susp_k_front", "susp_k_rear",
        "susp_c_front", "susp_c_rear", "tyre_k", "tyre_c",
    )  # fmt: skip

    def perturbed(self, rng: np.random.Generator, spread: float = 0.12) -> "CarParams":
        return _perturb(self, rng, spread, self.UNCERTAIN)


@dataclass
class VehicleModel:
    kind: str
    M: np.ndarray  # mass matrix
    K: np.ndarray  # internal stiffness (tyres excluded)
    C: np.ndarray  # internal damping (tyres excluded)
    T: np.ndarray  # (2, n) vertical displacement of front/rear tyre = T @ q
    kt: np.ndarray  # (2,) tyre stiffness
    ct: np.ndarray  # (2,) tyre damping
    wheelbase: float
    mount_row: np.ndarray  # vertical displacement of the IMU = mount_row @ q
    mount_h: float
    pitch_idx: int
    gravity: np.ndarray  # generalised gravity force
    tyre_radius: float
    patch_m: float
    force_rows: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def ndof(self) -> int:
        return self.M.shape[0]

    def _tyre_mats(self):
        Kt = self.T.T @ np.diag(self.kt) @ self.T
        Ct = self.T.T @ np.diag(self.ct) @ self.T
        return Kt, Ct

    def static(self) -> tuple[np.ndarray, np.ndarray]:
        """Static sag ``q0`` under gravity and the resulting tyre compressions (m)."""
        Kt, _ = self._tyre_mats()
        q0 = np.linalg.solve(self.K + Kt, self.gravity)
        comp0 = -(self.T @ q0)
        return q0, comp0

    def modes(self) -> tuple[np.ndarray, np.ndarray]:
        """Natural frequencies (Hz) and damping ratios with all tyres on the ground."""
        Kt, Ct = self._tyre_mats()
        n = self.ndof
        Minv = np.linalg.inv(self.M)
        A = np.block([[np.zeros((n, n)), np.eye(n)], [-Minv @ (self.K + Kt), -Minv @ (self.C + Ct)]])
        lam = np.linalg.eigvals(A)
        lam = lam[np.imag(lam) > 0]
        order = np.argsort(np.abs(lam))
        lam = lam[order]
        return np.abs(lam) / (2 * np.pi), -np.real(lam) / np.abs(lam)

    def frf(self, freqs: np.ndarray, speed: float) -> tuple[np.ndarray, np.ndarray]:
        """Linear response to road height under the *front* tyre (rear = delayed copy).

        Returns (vertical acceleration at the IMU [1/s^2], pitch rate [rad/(s m)]) per frequency.
        """
        w = 2 * np.pi * np.asarray(freqs, dtype=float)
        Kt, Ct = self._tyre_mats()
        Ktot, Ctot = self.K + Kt, self.C + Ct
        Bk = self.T.T * self.kt  # (n, 2)
        Bc = self.T.T * self.ct
        tau = self.wheelbase / max(speed, 0.1)
        Z = -(w**2)[:, None, None] * self.M + 1j * w[:, None, None] * Ctot + Ktot
        inp = np.stack([np.ones_like(w), np.exp(-1j * w * tau)], axis=1)  # (nf, 2)
        B = np.einsum("nk,fk->fn", Bk, inp) + 1j * w[:, None] * np.einsum("nk,fk->fn", Bc, inp)
        Q = np.linalg.solve(Z, B[..., None])[..., 0]
        acc = -(w**2) * (Q @ self.mount_row)
        rate = 1j * w * Q[:, self.pitch_idx]
        return acc, rate

    def simulate(self, dt: float, u: np.ndarray, udot: np.ndarray, fext: np.ndarray | None = None) -> dict:
        """Time simulation with one-sided tyre contact (semi-implicit Euler).

        u, udot: (N, 2) road height and its rate under the front/rear tyre.
        fext: (N, ndof) generalised external forces (pedalling, engine), or None.
        Returns q, qd, qdd (deviation from static equilibrium) and tyre forces.
        """
        n = self.ndof
        _, comp0 = self.static()
        if np.any(comp0 <= 0):
            raise ValueError("vehicle does not rest on both tyres - check parameters")
        F0 = self.kt * comp0
        Minv = np.linalg.inv(self.M)
        Aq, Av, Bt = -Minv @ self.K, -Minv @ self.C, Minv @ self.T.T
        fx = None if fext is None else fext @ Minv.T
        Kt, _ = self._tyre_mats()
        q = np.linalg.solve(self.K + Kt, self.T.T @ (self.kt * u[0]))  # start in equilibrium on the road
        v = np.zeros(n)
        N = u.shape[0]
        Q, V, A, F = np.empty((N, n)), np.empty((N, n)), np.empty((N, n)), np.empty((N, 2))
        T, kt, ct = self.T, self.kt, self.ct
        for k in range(N):
            comp = comp0 + u[k] - T @ q
            force = kt * comp + ct * (udot[k] - T @ v)
            force = np.where(comp > 0.0, np.maximum(force, 0.0), 0.0)
            a = Aq @ q + Av @ v + Bt @ (force - F0)
            if fx is not None:
                a = a + fx[k]
            Q[k], V[k], A[k], F[k] = q, v, a, force
            v = v + a * dt
            q = q + v * dt
        return {"q": Q, "qd": V, "qdd": A, "tyre_force": F, "static_tyre_force": F0}


def bike_model(p: BikeParams = BikeParams()) -> VehicleModel:
    def frame(x: float) -> np.ndarray:
        return np.array([1.0, x, 0.0])

    rider = np.array([0.0, 0.0, 1.0])
    K = np.zeros((3, 3))
    C = np.zeros((3, 3))
    for x, k, c in ((p.saddle_x, p.saddle_k, p.saddle_c), (p.hands_x, p.hands_k, p.hands_c), (p.pedal_x, p.pedal_k, p.pedal_c)):
        a = frame(x) - rider
        K += k * np.outer(a, a)
        C += c * np.outer(a, a)
    return VehicleModel(
        kind="bike",
        M=np.diag([p.frame_mass, p.frame_pitch_inertia, p.rider_mass]),
        K=K,
        C=C,
        T=np.vstack([frame(p.front_axle_m), frame(-p.rear_axle_m)]),
        kt=np.array([p.tyre_k_front, p.tyre_k_rear]),
        ct=np.array([p.tyre_c, p.tyre_c]),
        wheelbase=p.front_axle_m + p.rear_axle_m,
        mount_row=frame(p.mount_x),
        mount_h=p.mount_h,
        pitch_idx=1,
        gravity=np.array([-p.frame_mass * G, 0.0, -p.rider_mass * G]),
        tyre_radius=p.tyre_radius,
        patch_m=p.patch_m,
        force_rows={"pedal": rider - frame(p.pedal_x), "saddle": rider - frame(p.saddle_x)},
    )


def car_model(p: CarParams = CarParams()) -> VehicleModel:
    def body(x: float) -> np.ndarray:
        return np.array([1.0, x, 0.0, 0.0])

    wf, wr = np.array([0.0, 0.0, 1.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0])
    K = np.zeros((4, 4))
    C = np.zeros((4, 4))
    for a, k, c in ((body(p.front_axle_m) - wf, p.susp_k_front, p.susp_c_front), (body(-p.rear_axle_m) - wr, p.susp_k_rear, p.susp_c_rear)):
        K += k * np.outer(a, a)
        C += c * np.outer(a, a)
    return VehicleModel(
        kind="car",
        M=np.diag([p.body_mass, p.body_pitch_inertia, p.wheel_mass_front, p.wheel_mass_rear]),
        K=K,
        C=C,
        T=np.vstack([wf, wr]),
        kt=np.array([p.tyre_k, p.tyre_k]),
        ct=np.array([p.tyre_c, p.tyre_c]),
        wheelbase=p.front_axle_m + p.rear_axle_m,
        mount_row=body(p.mount_x),
        mount_h=p.mount_h,
        pitch_idx=1,
        gravity=np.array([-p.body_mass * G, 0.0, -p.wheel_mass_front * G, -p.wheel_mass_rear * G]),
        tyre_radius=p.tyre_radius,
        patch_m=p.patch_m,
        force_rows={"engine": body(p.engine_x)},
    )


def nominal_model(kind: str) -> VehicleModel:
    if kind == "bike":
        return bike_model()
    if kind == "car":
        return car_model()
    raise ValueError(f"unknown vehicle kind {kind!r} (use 'bike' or 'car')")
