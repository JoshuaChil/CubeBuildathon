"""Read/write IMU logs in the training-CSV format of docs/data-schema.md (section 4).

Columns: t_ms,lat,lon,speed_mps,ax,ay,az,gx,gy,gz,label
Units:   ms, deg, deg, m/s, g (x3), deg/s (x3), "bump" or empty
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

COLUMNS = ["t_ms", "lat", "lon", "speed_mps", "ax", "ay", "az", "gx", "gy", "gz", "label"]


@dataclass
class ImuLog:
    t_ms: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    speed: np.ndarray
    acc_g: np.ndarray  # (N, 3)
    gyro_dps: np.ndarray  # (N, 3)
    label: np.ndarray  # bool


def write_imu_csv(path: str | Path, log: ImuLog) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for i in range(log.t_ms.size):
            a, g = log.acc_g[i], log.gyro_dps[i]
            w.writerow(
                [
                    int(log.t_ms[i]),
                    f"{log.lat[i]:.7f}",
                    f"{log.lon[i]:.7f}",
                    f"{log.speed[i]:.2f}",
                    f"{a[0]:.5f}",
                    f"{a[1]:.5f}",
                    f"{a[2]:.5f}",
                    f"{g[0]:.3f}",
                    f"{g[1]:.3f}",
                    f"{g[2]:.3f}",
                    "bump" if log.label[i] else "",
                ]
            )


def read_imu_csv(path: str | Path) -> ImuLog:
    with Path(path).open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise ValueError(f"{path}: no data rows")

    def col(name: str) -> np.ndarray:
        return np.array([float(r[name]) if r[name] not in ("", None) else np.nan for r in rows])

    return ImuLog(
        t_ms=col("t_ms").astype(np.int64),
        lat=col("lat"),
        lon=col("lon"),
        speed=col("speed_mps"),
        acc_g=np.column_stack([col("ax"), col("ay"), col("az")]),
        gyro_dps=np.column_stack([col("gx"), col("gy"), col("gz")]),
        label=np.array([(r.get("label") or "").strip().lower() == "bump" for r in rows]),
    )
