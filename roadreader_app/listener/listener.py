#!/usr/bin/env python3
"""
Road Reader listener.

Receives labelled IMU rows streamed by the Road Reader iPhone app (UDP or TCP)
and appends them to a CSV file. Standard library only.

    python3 listener.py                       # UDP on 0.0.0.0:5005 -> roadreader_<timestamp>.csv
    python3 listener.py --protocol tcp        # TCP server instead
    python3 listener.py --port 6000 --out run1.csv

Row format (one line per sample, must match IMUSample.csvHeader in the app):
    ax,ay,az,gx,gy,gz,crack_pothole_detected,phone_time_ms,device_time_ms,
    latitude,longitude,altitude_m,speed_mps,horizontal_accuracy_m
"""

import argparse
import datetime as dt
import os
import socket
import sys

HEADER = [
    "ax", "ay", "az", "gx", "gy", "gz",
    "crack_pothole_detected",
    "phone_time_ms", "device_time_ms",
    "latitude", "longitude", "altitude_m", "speed_mps", "horizontal_accuracy_m",
]
LABEL_COLUMN = 6  # 0-based index of "crack_pothole_detected" (the 7th column)


def local_ip() -> str:
    """Best-effort LAN IP to type into the app's Settings > Host field."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Writer:
    def __init__(self, path: str):
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        self.f = open(path, "a", buffering=1, encoding="utf-8")
        if new_file:
            self.f.write(",".join(HEADER) + "\n")
        self.rows = 0
        self.flagged = 0
        self.bad = 0

    def handle(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        fields = line.split(",")
        if len(fields) < LABEL_COLUMN + 1:
            self.bad += 1
            return
        if fields[0] == HEADER[0]:  # header echoed by a sender; skip
            return
        self.f.write(line + "\n")
        self.rows += 1
        if fields[LABEL_COLUMN] == "1":
            self.flagged += 1
        if self.rows % 10 == 0 or fields[LABEL_COLUMN] == "1":
            mark = "  <-- BUMP" if fields[LABEL_COLUMN] == "1" else ""
            sys.stdout.write(
                f"\rrows={self.rows:7d} flagged={self.flagged:6d} bad={self.bad:4d} "
                f"last={','.join(fields[:7])}{mark:<12}"
            )
            sys.stdout.flush()

    def close(self) -> None:
        self.f.close()


def serve_udp(host: str, port: int, writer: Writer) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    print(f"UDP listening on {host}:{port}")
    while True:
        data, _addr = sock.recvfrom(65535)
        for line in data.decode("utf-8", errors="replace").splitlines():
            writer.handle(line)


def serve_tcp(host: str, port: int, writer: Writer) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    print(f"TCP listening on {host}:{port}")
    while True:
        conn, addr = srv.accept()
        print(f"\nphone connected from {addr[0]}")
        buf = b""
        try:
            while True:
                chunk = conn.recv(65535)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    writer.handle(line.decode("utf-8", errors="replace"))
        finally:
            conn.close()
            print("\nphone disconnected; waiting for next connection")


def main() -> None:
    ap = argparse.ArgumentParser(description="Receive Road Reader rows and write them to CSV.")
    ap.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--protocol", choices=["udp", "tcp"], default="udp")
    ap.add_argument("--out", default=None, help="CSV path (default: roadreader_<timestamp>.csv)")
    args = ap.parse_args()

    out = args.out or f"roadreader_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    writer = Writer(out)
    print(f"writing to {out}")
    print(f"in the app: Settings > Forwarding > Host = {local_ip()}, Port = {args.port}, Protocol = {args.protocol.upper()}")
    print("press Ctrl-C to stop\n")

    try:
        if args.protocol == "udp":
            serve_udp(args.host, args.port, writer)
        else:
            serve_tcp(args.host, args.port, writer)
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
        print(f"\nsaved {writer.rows} rows ({writer.flagged} flagged) to {out}")


if __name__ == "__main__":
    main()
