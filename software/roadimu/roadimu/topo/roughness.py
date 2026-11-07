"""Step 4: roughness indices and defect detection on the reconstructed profile.

* IRI (International Roughness Index, m/km): the standard "golden car" quarter
  car at 80 km/h over the profile (Sayers 1995, 250 mm moving average).
  Cities use it to rate pavements, so it's what a DOT would expect to see.
* RMS roughness (mm) of wavelengths < 5 m: closer to what a cyclist feels.
* Events: potholes (dips) and bumps (rises) relative to a running median.
"""

from __future__ import annotations

import numpy as np
from scipy import linalg, signal
from scipy.ndimage import median_filter, uniform_filter1d

# Golden car parameters (per unit sprung mass) and simulation speed
_K1, _K2, _C, _MU = 653.0, 63.3, 6.0, 0.15
_IRI_SPEED = 80.0 / 3.6

CONDITIONS = [(2.0, "good"), (4.0, "fair"), (6.0, "poor"), (np.inf, "very poor")]


def iri_slope(profile: np.ndarray, dx: float) -> np.ndarray:
    """Rectified golden-car suspension slope per sample; mean * 1000 = IRI in m/km."""
    k = max(1, int(round(0.25 / dx)))
    p = uniform_filter1d(profile, k, mode="nearest") if k > 1 else profile
    A = np.array(
        [[0, 1, 0, 0], [-_K2, -_C, _K2, _C], [0, 0, 0, 1], [_K2 / _MU, _C / _MU, -(_K1 + _K2) / _MU, -_C / _MU]],
        dtype=float,
    )
    B = np.array([0, 0, 0, _K1 / _MU], dtype=float)
    dt = dx / _IRI_SPEED
    aug = np.zeros((5, 5))
    aug[:4, :4], aug[:4, 4] = A, B
    E = linalg.expm(aug * dt)
    Ad, Bd = E[:4, :4], E[:4, 4]
    i11 = min(p.size - 1, int(round(11.0 / dx)))
    slope0 = (p[i11] - p[0]) / max(i11 * dx, dx)
    x = np.array([p[0], slope0 * _IRI_SPEED, p[0], slope0 * _IRI_SPEED])
    out = np.empty(p.size)
    for i in range(p.size):
        x = Ad @ x + Bd * p[i]
        out[i] = abs(x[1] - x[3]) / _IRI_SPEED
    return out


def condition(iri: float) -> str:
    return next(name for limit, name in CONDITIONS if iri < limit)


def segment_stats(s: np.ndarray, profile: np.ndarray, dx: float, segment_m: float = 20.0) -> list[dict]:
    slope = iri_slope(profile, dx)
    hp = signal.butter(2, 1.0 / 5.0, btype="high", fs=1.0 / dx, output="sos")
    short = signal.sosfiltfilt(hp, profile) if profile.size > 30 else profile - profile.mean()
    out = []
    edges = np.arange(s[0], s[-1] + 1e-9, segment_m)
    for a, b in zip(edges[:-1], edges[1:]):
        sel = (s >= a) & (s < b)
        if sel.sum() < 10:
            continue
        iri = float(slope[sel].mean() * 1000.0)
        out.append(
            {
                "start_m": float(a),
                "end_m": float(b),
                "iri_m_per_km": iri,
                "rms_mm": float(np.sqrt(np.mean(short[sel] ** 2)) * 1e3),
                "condition": condition(iri),
            }
        )
    return out


def _runs(mask: np.ndarray, merge: int) -> list[tuple[int, int]]:
    """Contiguous True runs, merging runs separated by fewer than ``merge`` samples."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(int), [0]])))
    runs: list[list[int]] = []
    for a, b in zip(edges[::2], edges[1::2]):
        if runs and a - runs[-1][1] < merge:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    return [(a, b) for a, b in runs]


def _local_sigma(x: np.ndarray, dx: float, window_m: float = 20.0) -> np.ndarray:
    """Robust local roughness (scaled median absolute value) - so textures don't trigger events."""
    w = max(3, int(window_m / dx) | 1)
    return 1.4826 * median_filter(np.abs(x), size=w, mode="nearest")


def detect_events(
    s: np.ndarray,
    profile: np.ndarray,
    dx: float,
    pothole_m: float = 0.015,
    bump_m: float = 0.015,
    k_sigma: float = 4.0,
    min_width_m: float = 0.1,
    merge_m: float = 0.3,
    ringing_m: float = 1.5,
) -> list[dict]:
    """Potholes (dips) and bumps (rises) that stand out from the surrounding surface.

    Two passes: a 3 m running-median baseline finds short defects (potholes,
    plates, bumps); a 15 m baseline finds long raised features (speed humps,
    raised crosswalks).  The threshold is the larger of a fixed depth and
    ``k_sigma`` times the local roughness, so cobblestones or slab joints don't
    each count as a defect.
    """
    candidates: list[dict] = []
    passes = (  # (type, sign, baseline length, threshold, allowed width range)
        ("pothole", -1, 3.0, pothole_m, (min_width_m, 2.0)),
        ("bump", +1, 3.0, bump_m, (min_width_m, 2.0)),
        ("depression", -1, 15.0, pothole_m, (1.0, 8.0)),  # sunken patches, settled trenches
        ("bump", +1, 15.0, bump_m, (1.0, 8.0)),  # speed humps, plates, raised crosswalks
    )
    for kind, sign, base_m, thr, (wmin, wmax) in passes:
        base = median_filter(profile, size=max(3, int(base_m / dx) | 1), mode="nearest")
        resid = profile - base
        limit = np.maximum(thr, k_sigma * _local_sigma(resid, dx))
        for a, b in _runs(sign * resid > limit, int(merge_m / dx)):
            width = (b - a) * dx
            if not (wmin <= width <= wmax):
                continue
            i = a + int(np.argmax(sign * resid[a:b]))
            mag = float(resid[i])
            candidates.append(
                {
                    "type": kind,
                    "start_m": float(s[a]),
                    "end_m": float(s[min(b, s.size - 1)]),
                    "peak_m": float(s[i]),
                    "magnitude_m": mag,
                    "severity": float(min(1.0, abs(mag) / 0.08)),
                }
            )
    # The same defect is often found by several passes: keep the strongest.  A weaker
    # opposite-sign detection right next to a strong one is deconvolution ringing (the
    # overshoot either side of a sharp pothole), so drop that too.
    kept: list[dict] = []
    for c in sorted(candidates, key=lambda e: -abs(e["magnitude_m"])):
        same = any(
            (c["magnitude_m"] < 0) == (k["magnitude_m"] < 0) and c["start_m"] <= k["end_m"] + merge_m and k["start_m"] <= c["end_m"] + merge_m
            for k in kept
        )
        ringing = any(
            (c["magnitude_m"] < 0) != (k["magnitude_m"] < 0) and c["start_m"] <= k["end_m"] + ringing_m and k["start_m"] <= c["end_m"] + ringing_m
            for k in kept
        )
        if not (same or ringing):
            kept.append(c)
    return sorted(kept, key=lambda e: e["peak_m"])
