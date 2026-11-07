"""Command line interface.

    python -m roadimu list
    python -m roadimu simulate --scenario nyc_mixed --vehicle bike
    python -m roadimu reconstruct out/sim/nyc_mixed_bike_imu.csv --vehicle bike
    python -m roadimu evaluate                      # all scenarios, bike + car
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .scenarios import SCENARIOS


def _vehicles(v: str) -> list[str]:
    return ["bike", "car"] if v == "both" else [v]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="roadimu", description="Simulate road IMU data and reconstruct road topography.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list road scenarios")

    s = sub.add_parser("simulate", help="simulate a ride and write IMU CSV + ground truth")
    s.add_argument("--scenario", default="nyc_mixed", choices=[*SCENARIOS, "all"])
    s.add_argument("--vehicle", default="bike", choices=["bike", "car", "both"])
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--odr", type=float, default=200.0, help="IMU output data rate, Hz")
    s.add_argument("--out", default="out/sim")
    s.add_argument(
        "--format",
        choices=["schema", "app", "demo"],
        default="schema",
        help="'app' = the Road Reader iPhone app's CSV; 'demo' = the app's bundled Demo ride (ESP32 lines + GPS)",
    )
    s.add_argument("--firmware", choices=["200hz", "current"], default="200hz", help="for --format app: updated esp32.ino (200 Hz + t_ms) or the original 50 Hz one")

    rp = sub.add_parser("replay", help="stream an app-format CSV to listener.py like the phone does")
    rp.add_argument("csv")
    rp.add_argument("--host", default="127.0.0.1")
    rp.add_argument("--port", type=int, default=5005)
    rp.add_argument("--protocol", choices=["udp", "tcp"], default="udp")
    rp.add_argument("--speedup", type=float, default=1.0, help="2 = twice real time")

    r = sub.add_parser("reconstruct", help="turn an IMU CSV (docs/data-schema.md format) into topography")
    r.add_argument("csv")
    r.add_argument("--vehicle", default="bike", choices=["bike", "car"])
    r.add_argument("--out", default="out/topo")
    r.add_argument("--no-plot", action="store_true")

    e = sub.add_parser("evaluate", help="simulate + reconstruct + score against ground truth")
    e.add_argument("--scenario", nargs="*", default=list(SCENARIOS), choices=list(SCENARIOS))
    e.add_argument("--vehicle", default="both", choices=["bike", "car", "both"])
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--out", default="out")
    e.add_argument("--no-plots", action="store_true")

    a = p.parse_args(argv)
    if a.cmd == "list":
        for sc in SCENARIOS.values():
            print(f"{sc.name:16s} {sc.description}")
    elif a.cmd == "simulate":
        from .sensors import ImuSpec
        from .simulate import SimConfig, run_scenario

        names = list(SCENARIOS) if a.scenario == "all" else [a.scenario]
        for sc in names:
            for veh in _vehicles(a.vehicle):
                if a.format in ("app", "demo"):
                    import numpy as np

                    from .appio import write_app_csv
                    from .simulate import CURRENT_FIRMWARE_IMU, app_rows

                    res = run_scenario(sc, veh, seed=a.seed, cfg=SimConfig(imu=CURRENT_FIRMWARE_IMU))
                    poll, ts = (50.0, False) if a.firmware == "current" and a.format == "app" else (200.0, True)
                    rows = app_rows(res, np.random.default_rng(a.seed + 1), poll_hz=poll, device_timestamps=ts)
                    if a.format == "demo":
                        # What the ESP32 sends (t_ms,ax..gz) + the GPS fix the phone would have at that moment.
                        path = Path(a.out) / "demo_ride.csv"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("w") as fh:
                            fh.write("t_ms,ax,ay,az,gx,gy,gz,lat,lon,speed_mps\n")
                            for r in rows:
                                fh.write(",".join([r[8], *(f"{float(v):.4f}" for v in r[:3]), *(f"{float(v):.2f}" for v in r[3:6]), r[9], r[10], r[12]]) + "\n")
                        print(f"{res.name}: {res.meta['duration_s']:.0f} s demo ride, {len(rows)} rows -> {path}")
                        continue
                    path = Path(a.out) / f"session_{res.name}_{a.firmware}.csv"
                    write_app_csv(path, rows)
                    print(f"{res.name}: {res.meta['duration_s']:.0f} s, {len(rows)} app rows ({a.firmware} firmware) -> {path}")
                    continue
                res = run_scenario(sc, veh, seed=a.seed, cfg=SimConfig(imu=ImuSpec(odr_hz=a.odr)), out_dir=a.out)
                m = res.meta
                print(f"{res.name}: {m['duration_s']:.0f} s, {m['distance_m']:.0f} m, {m['samples_written']} samples -> {Path(a.out) / (res.name + '_imu.csv')}")
    elif a.cmd == "replay":
        _replay(a)
    elif a.cmd == "reconstruct":
        from .plots import report_figure
        from .topo import TopoConfig, reconstruct

        res = reconstruct(a.csv, TopoConfig(vehicle=a.vehicle))
        paths = res.save(a.out)
        if not a.no_plot:
            paths["report"] = report_figure(res, Path(a.out) / f"{res.name}_report.png")
        print(json.dumps({k: v for k, v in res.summary().items() if k != "config"}, indent=2))
        for k, v in paths.items():
            print(f"{k:11s} {v}")
    elif a.cmd == "evaluate":
        from .evaluate import run

        run(a.scenario, _vehicles(a.vehicle), out_dir=a.out, seed=a.seed, plots=not a.no_plots)
        print(f"\nsummary -> {Path(a.out) / 'summary.md'}")


def _replay(a) -> None:
    """Send rows with their original timing (phone_time_ms), shifted to 'now'."""
    import socket
    import time

    lines = Path(a.csv).read_text().splitlines()[1:]
    if not lines:
        return
    t_first = float(lines[0].split(",")[7])
    now0 = int(time.time() * 1000)
    if a.protocol == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send = lambda b: sock.sendto(b, (a.host, a.port))  # noqa: E731
    else:
        sock = socket.create_connection((a.host, a.port))
        send = sock.sendall
    start = time.time()
    for i, line in enumerate(lines):
        f = line.split(",")
        dt_ms = float(f[7]) - t_first
        f[7] = str(int(now0 + dt_ms / a.speedup))  # re-stamp as if recorded now
        wait = start + dt_ms / 1000.0 / a.speedup - time.time()
        if wait > 0:
            time.sleep(wait)
        send((",".join(f) + "\n").encode())
        if i % 500 == 0:
            print(f"\rsent {i}/{len(lines)} rows", end="", flush=True)
    print(f"\rsent {len(lines)} rows to {a.host}:{a.port} ({a.protocol})")
    sock.close()


if __name__ == "__main__":
    main()
