"""Ready-made road conditions.

Every scenario starts and ends with a short level, smooth stretch: the rider
stands still for a few seconds at the start (IMU calibration) and stops at the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .road import Feature, Segment


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    build: Callable[[np.random.Generator], list[Segment]]
    cruise_mps: dict  # by vehicle kind
    max_grade: float = 0.0


def _lead() -> Segment:
    return Segment("start (level)", 25.0, "A")


def _tail() -> Segment:
    return Segment("end", 25.0, "A")


def _cracks(rng, length, every=(6.0, 20.0), start=5.0) -> list[Feature]:
    out, pos = [], start + rng.uniform(*every)
    while pos < length - 5:
        out.append(Feature("crack", pos, rng.uniform(0.01, 0.035), -rng.uniform(0.006, 0.02), edge_m=0.004))
        pos += rng.uniform(*every)
    return out


def _spread(rng, length, count, margin=15.0) -> np.ndarray:
    """Positions spread along the segment with jitter (keeps features apart)."""
    slots = np.linspace(margin, length - margin, count + 1)
    return np.array([rng.uniform(a + 3, b - 3) for a, b in zip(slots[:-1], slots[1:])])


def smooth_asphalt(rng):
    return [_lead(), Segment("fresh asphalt", 300.0, "A"), _tail()]


def worn_asphalt(rng):
    feats = _cracks(rng, 300.0)
    feats.append(Feature("patch", 140.0, 3.0, -0.012, edge_m=0.01))
    return [_lead(), Segment("worn asphalt", 300.0, "C", features=feats), _tail()]


def potholes(rng):
    sizes = [(-0.02, 0.4), (-0.035, 0.6), (-0.05, 0.8), (-0.03, 0.3), (-0.07, 1.0), (-0.045, 0.5), (-0.025, 1.5)]
    pos = _spread(rng, 350.0, len(sizes) + 2)
    feats = [Feature("pothole", p, L, d, edge_m=0.03) for p, (d, L) in zip(pos, sizes)]
    feats.append(Feature("manhole", pos[-2], 0.65, -0.015, edge_m=0.01))
    feats.append(Feature("manhole", pos[-1], 0.65, 0.010, edge_m=0.01))
    return [_lead(), Segment("damaged asphalt", 350.0, "C", features=feats), _tail()]


def cobblestone(rng):
    return [
        _lead(),
        Segment("asphalt", 60.0, "B"),
        Segment("cobblestones", 150.0, "B", surface="cobblestone", speed_factor=0.75),
        Segment("asphalt ", 60.0, "B"),
        _tail(),
    ]


def gravel(rng):
    return [
        _lead(),
        Segment("asphalt", 60.0, "B"),
        Segment("gravel path", 200.0, "D", surface="gravel", speed_factor=0.8),
        Segment("asphalt ", 60.0, "B"),
        _tail(),
    ]


def speed_bumps(rng):
    feats = [
        Feature("speed_hump", 70.0, 3.7, 0.075),  # NYC-style hump
        Feature("speed_bump", 170.0, 0.45, 0.06),  # parking-lot bump
        Feature("raised_crosswalk", 270.0, 6.0, 0.075, edge_m=1.8),
    ]
    return [_lead(), Segment("residential street", 360.0, "B", features=feats), _tail()]


def concrete_slabs(rng):
    return [_lead(), Segment("concrete slabs", 300.0, "B", surface="concrete_slabs"), _tail()]


def nyc_mixed(rng):
    """~1 km demo loop through typical NYC conditions, with turns and gentle hills."""
    return [
        _lead(),
        Segment("avenue (good asphalt)", 150.0, "B", features=_cracks(rng, 150.0, every=(30, 60)), turn_deg=90.0),
        Segment(
            "side street (potholes)",
            130.0,
            "C",
            features=[
                Feature("pothole", 25.0, 0.6, -0.04, edge_m=0.03),
                Feature("manhole", 55.0, 0.65, -0.015, edge_m=0.01),
                Feature("pothole", 80.0, 0.9, -0.06, edge_m=0.03),
                Feature("pothole", 110.0, 0.35, -0.025, edge_m=0.03),
            ],
        ),
        Segment("historic cobblestones", 80.0, "B", surface="cobblestone", speed_factor=0.75, turn_deg=-90.0),
        Segment(
            "construction zone",
            90.0,
            "C",
            features=[Feature("steel_plate", 30.0, 2.4, 0.025, edge_m=0.02), Feature("patch", 60.0, 4.0, -0.015, edge_m=0.01)],
        ),
        Segment("concrete slabs", 150.0, "B", surface="concrete_slabs"),
        Segment("school street", 100.0, "B", features=[Feature("speed_hump", 45.0, 3.7, 0.075)], turn_deg=90.0),
        Segment(
            "worn asphalt",
            150.0,
            "D",
            features=_cracks(rng, 150.0) + [Feature("pothole", 70.0, 0.5, -0.05, edge_m=0.03)],
        ),
        _tail(),
    ]


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario("smooth_asphalt", "Fresh asphalt, ISO class A", smooth_asphalt, {"bike": 7.0, "car": 13.0}),
        Scenario("worn_asphalt", "Worn asphalt (class C) with cracks and a patch", worn_asphalt, {"bike": 6.5, "car": 11.0}),
        Scenario("potholes", "Class C asphalt with 7 potholes and 2 manholes", potholes, {"bike": 6.0, "car": 10.0}),
        Scenario("cobblestone", "150 m of cobblestones between asphalt", cobblestone, {"bike": 5.5, "car": 8.0}),
        Scenario("gravel", "200 m gravel path (class D + loose stones)", gravel, {"bike": 5.5, "car": 8.0}),
        Scenario("speed_bumps", "Speed hump, speed bump and raised crosswalk", speed_bumps, {"bike": 6.5, "car": 11.0}),
        Scenario("concrete_slabs", "Concrete slabs with joints and faulting", concrete_slabs, {"bike": 6.5, "car": 12.0}),
        Scenario("nyc_mixed", "~1 km NYC mix: turns, hills, potholes, cobbles, plates, hump", nyc_mixed, {"bike": 6.0, "car": 10.0}, max_grade=0.03),
    ]
}
