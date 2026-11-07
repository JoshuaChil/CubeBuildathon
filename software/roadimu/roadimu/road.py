"""Ground-truth road surfaces.

A road has two scales of topography:

* **macro** - hills and grades over hundreds of metres (elevation in m).
* **micro** - the surface the tyre actually rolls over (mm to cm): ISO 8608
  random roughness, plus discrete defects (potholes, cracks, speed bumps,
  manholes, steel plates...) and surface textures (cobblestones, gravel,
  concrete slabs).

Distances are along the road centre line, in metres, sampled every ``dx``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import uniform_filter1d

# ISO 8608 displacement PSD at n0 = 0.1 cycles/m, geometric mean of each class (m^3).
ISO8608_GD0 = {
    "A": 16e-6,  # very good (new asphalt)
    "B": 64e-6,  # good
    "C": 256e-6,  # average / worn
    "D": 1024e-6,  # poor
    "E": 4096e-6,  # very poor
    "F": 16384e-6,
    "G": 65536e-6,
    "H": 262144e-6,
}
N0 = 0.1  # reference spatial frequency, cycles/m
EARTH_RADIUS_M = 6_371_000.0

# Features a rider would notice and that the event detector should find.
EVENT_KINDS = ("pothole", "speed_bump", "speed_hump", "raised_crosswalk", "manhole", "steel_plate", "patch", "crack")


def iso8608_psd(n: np.ndarray, road_class: str, waviness: float = 2.0) -> np.ndarray:
    """One-sided displacement PSD Gd(n) in m^3 for spatial frequency n (cycles/m)."""
    n = np.maximum(np.asarray(n, dtype=float), 1e-6)
    return ISO8608_GD0[road_class] * (n / N0) ** (-waviness)


def iso8608_unit_profile(
    n_samples: int,
    dx: float,
    rng: np.random.Generator,
    n_min: float = 0.011,
    n_max: float = 10.0,
    waviness: float = 2.0,
) -> np.ndarray:
    """Random profile whose PSD is (n/n0)^-w, i.e. ISO 8608 with Gd(n0) = 1.

    Multiply by sqrt(Gd0) to get a profile of a given class.  Built as a sum of
    cosines with random phase (inverse FFT), amplitude sqrt(2 Gd dn) per bin.
    """
    freqs = np.fft.rfftfreq(n_samples, dx)
    dn = freqs[1] - freqs[0]
    band = (freqs >= n_min) & (freqs <= n_max)
    amp = np.zeros_like(freqs)
    amp[band] = np.sqrt(2.0 * (freqs[band] / N0) ** (-waviness) * dn)
    phase = rng.uniform(0.0, 2.0 * np.pi, freqs.size)
    spectrum = amp * np.exp(1j * phase) * (n_samples / 2.0)
    return np.fft.irfft(spectrum, n=n_samples)


@dataclass
class Feature:
    """A discrete road feature.  ``magnitude_m`` > 0 is raised, < 0 is a depression."""

    kind: str
    at_m: float  # start, relative to the segment start
    length_m: float
    magnitude_m: float
    edge_m: float = 0.02  # width of the sloped edge

    def shape(self, x_rel: np.ndarray) -> np.ndarray:
        """Height profile; ``x_rel`` is measured from the feature start."""
        length = self.length_m
        if self.kind in ("speed_bump", "speed_hump"):
            u = np.clip(x_rel / length, 0.0, 1.0)
            return self.magnitude_m * np.sin(np.pi * u) ** 2
        edge = min(self.edge_m, length / 2.0)
        rise = np.clip(x_rel / edge, 0.0, 1.0)
        fall = np.clip((length - x_rel) / edge, 0.0, 1.0)
        w = np.minimum(rise, fall)
        return self.magnitude_m * (0.5 - 0.5 * np.cos(np.pi * w))


@dataclass
class Segment:
    """A stretch of road with one roughness class and surface type."""

    name: str
    length_m: float
    road_class: str = "B"
    surface: str = "asphalt"  # asphalt | cobblestone | gravel | concrete_slabs
    features: list[Feature] = field(default_factory=list)
    turn_deg: float = 0.0  # heading change at the end of the segment (+ = left)
    speed_factor: float = 1.0  # vehicles slow down on bad surfaces


@dataclass
class Road:
    dx: float
    x: np.ndarray  # distance, m
    micro: np.ndarray  # surface profile, m (relative to macro elevation)
    macro: np.ndarray  # elevation, m
    heading: np.ndarray  # rad, counter-clockwise from east
    curvature: np.ndarray  # 1/m (+ = turning left)
    east: np.ndarray
    north: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    events: list[dict]  # discrete features in absolute coordinates
    segments: list[dict]

    @property
    def length_m(self) -> float:
        return float(self.x[-1])

    def at(self, values: np.ndarray, xq: np.ndarray) -> np.ndarray:
        return np.interp(xq, self.x, values)


# --------------------------------------------------------------------------- textures


def _cobblestones(n: int, dx: float, rng: np.random.Generator) -> np.ndarray:
    z = np.zeros(n)
    x = np.arange(n) * dx
    pos = 0.0
    total = n * dx
    while pos < total:
        stone = rng.uniform(0.10, 0.16)
        joint = rng.uniform(0.015, 0.030)
        i0, i1 = int(pos / dx), min(n, int((pos + stone) / dx))
        u = (x[i0:i1] - pos) / stone
        # stones settle unevenly (height offset) and have rounded tops
        z[i0:i1] = rng.normal(0.0, 0.006) + rng.uniform(0.003, 0.008) * np.sin(np.pi * u)
        j1 = min(n, int((pos + stone + joint) / dx))
        z[i1:j1] = -rng.uniform(0.008, 0.015)
        pos += stone + joint
    return z


def _gravel(n: int, dx: float, rng: np.random.Generator) -> np.ndarray:
    white = rng.normal(size=n)
    freqs = np.fft.rfftfreq(n, dx)
    spec = np.fft.rfft(white)
    spec[(freqs < 3.0) | (freqs > 30.0)] = 0.0
    z = np.fft.irfft(spec, n=n)
    z *= 0.004 / max(np.std(z), 1e-12)
    # loose stones: short sharp bumps
    for _ in range(int(n * dx / 2.0)):
        i = rng.integers(0, n)
        w = max(1, int(rng.uniform(0.03, 0.05) / dx))
        z[i : i + w] += rng.uniform(0.008, 0.02)
    return z


def _concrete_slabs(n: int, dx: float, rng: np.random.Generator) -> np.ndarray:
    z = np.zeros(n)
    pos = 0.0
    total = n * dx
    while pos < total:
        slab = rng.uniform(4.5, 6.0)
        i0, i1 = int(pos / dx), min(n, int((pos + slab) / dx))
        z[i0:i1] = rng.normal(0.0, 0.004)  # faulting: slabs settle unevenly
        j1 = min(n, i0 + max(1, int(0.012 / dx)))
        z[i0:j1] -= rng.uniform(0.004, 0.010)  # joint groove
        pos += slab
    return z


_TEXTURES = {"cobblestone": _cobblestones, "gravel": _gravel, "concrete_slabs": _concrete_slabs}


# --------------------------------------------------------------------------- builder


def build_road(
    segments: list[Segment],
    rng: np.random.Generator,
    dx: float = 0.01,
    max_grade: float = 0.0,
    origin: tuple[float, float] = (40.7295, -73.9965),
    heading0_deg: float = 0.0,
    turn_radius_m: float = 10.0,
    level_start_m: float = 30.0,
) -> Road:
    """Concatenate segments into a ground-truth road.

    ``max_grade`` sets the hilliness (0.03 = up to ~3 % grades).  The first
    ``level_start_m`` metres are kept level so the vehicle starts on flat ground
    (the calibration step assumes it).
    """
    total = sum(s.length_m for s in segments)
    n = int(round(total / dx)) + 1
    x = np.arange(n) * dx

    # ISO 8608 roughness with per-segment class, cross-faded over 2 m
    amp = np.zeros(n)
    starts = np.concatenate([[0.0], np.cumsum([s.length_m for s in segments])])
    for seg, s0, s1 in zip(segments, starts[:-1], starts[1:]):
        amp[(x >= s0) & (x <= s1)] = np.sqrt(ISO8608_GD0[seg.road_class])
    amp = uniform_filter1d(amp, max(1, int(2.0 / dx)), mode="nearest")
    micro = amp * iso8608_unit_profile(n, dx, rng)

    events: list[dict] = []
    seg_info: list[dict] = []
    curvature = np.zeros(n)
    for seg, s0, s1 in zip(segments, starts[:-1], starts[1:]):
        idx = np.flatnonzero((x >= s0) & (x < s1))
        if seg.surface in _TEXTURES and idx.size:
            micro[idx] += _TEXTURES[seg.surface](idx.size, dx, rng)
        for f in seg.features:
            a0 = s0 + f.at_m
            sel = (x >= a0) & (x <= a0 + f.length_m)
            shape = f.shape(x[sel] - a0)
            if f.kind == "pothole":  # irregular bottom
                shape = shape * (1.0 + 0.15 * np.sin(2 * np.pi * (x[sel] - a0) / max(f.length_m, 0.1) * rng.uniform(1, 3)))
            micro[sel] += shape
            events.append(
                {"kind": f.kind, "start_m": a0, "end_m": a0 + f.length_m, "magnitude_m": f.magnitude_m, "segment": seg.name}
            )
        if seg.turn_deg:
            dpsi = np.deg2rad(seg.turn_deg)
            arc = min(seg.length_m * 0.8, turn_radius_m * abs(dpsi))
            sel = (x >= s1 - arc) & (x < s1)
            curvature[sel] = dpsi / arc
        seg_info.append(
            {
                "name": seg.name,
                "start_m": s0,
                "end_m": s1,
                "road_class": seg.road_class,
                "surface": seg.surface,
                "speed_factor": seg.speed_factor,
            }
        )

    # macro topography: a few long hills, level at the start
    grade = np.zeros(n)
    if max_grade > 0:
        for _ in range(3):
            lam = rng.uniform(200.0, 800.0)
            grade += (max_grade / 3.0) * np.cos(2 * np.pi * x / lam + rng.uniform(0, 2 * np.pi))
        ramp = np.clip((x - level_start_m) / 30.0, 0.0, 1.0)
        grade *= 0.5 - 0.5 * np.cos(np.pi * ramp)
    macro = np.concatenate([[0.0], np.cumsum(0.5 * (grade[1:] + grade[:-1]) * dx)])

    heading = np.deg2rad(heading0_deg) + np.concatenate([[0.0], np.cumsum(curvature[:-1] * dx)])
    east = np.concatenate([[0.0], np.cumsum(np.cos(heading[:-1]) * dx)])
    north = np.concatenate([[0.0], np.cumsum(np.sin(heading[:-1]) * dx)])
    lat, lon = enu_to_latlon(east, north, origin)

    return Road(dx, x, micro, macro, heading, curvature, east, north, lat, lon, events, seg_info)


def enu_to_latlon(east: np.ndarray, north: np.ndarray, origin: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    lat0, lon0 = origin
    lat = lat0 + np.rad2deg(north / EARTH_RADIUS_M)
    lon = lon0 + np.rad2deg(east / (EARTH_RADIUS_M * np.cos(np.deg2rad(lat0))))
    return lat, lon


def latlon_to_enu(lat: np.ndarray, lon: np.ndarray, origin: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    lat0, lon0 = origin
    north = np.deg2rad(np.asarray(lat) - lat0) * EARTH_RADIUS_M
    east = np.deg2rad(np.asarray(lon) - lon0) * EARTH_RADIUS_M * np.cos(np.deg2rad(lat0))
    return east, north


def tyre_envelope(profile: np.ndarray, dx: float, radius_m: float, patch_m: float, max_drop_m: float = 0.15) -> np.ndarray:
    """Profile actually 'felt' at the wheel centre.

    A tyre can't drop into a crack narrower than its contact patch, and it rolls
    over the edge of a pothole instead of following the vertical wall.  Modelled
    as a rigid disc rolling over the surface, then averaged over the contact patch.
    """
    n = profile.size
    umax = min(0.95 * radius_m, np.sqrt(radius_m**2 - max(radius_m - max_drop_m, 0.0) ** 2))
    k = int(umax / dx)
    padded = np.pad(profile, k, mode="edge")
    env = np.full(n, -np.inf)
    for j in range(-k, k + 1):
        drop = radius_m - np.sqrt(radius_m**2 - (j * dx) ** 2)
        np.maximum(env, padded[k + j : k + j + n] - drop, out=env)
    w = max(1, int(round(patch_m / dx)))
    return uniform_filter1d(env, w, mode="nearest") if w > 1 else env
