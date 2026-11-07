#!/usr/bin/env python3
"""
Road Reader listener.

Receives labelled IMU rows streamed by the Road Reader iPhone app (UDP or TCP)
and appends them to a CSV file. Standard library only.

    python3 listener.py                       # UDP on 0.0.0.0:5005 -> roadreader_<timestamp>.csv
    python3 listener.py --protocol tcp        # TCP server instead
    python3 listener.py --port 6000 --out run1.csv

Live road analysis (needs numpy/scipy: pip install -r ../../software/roadimu/requirements.txt):

    python3 listener.py --analyze             # also serves results on http://<this computer>:8081/live
    python3 listener.py --analyze --vehicle car

    The app polls /live every 2 s and shows road roughness and potholes while you ride.
    GET /live?vehicle=bike|car also switches the vehicle model.  GET /live/reset starts a new ride.

Row format (one line per sample, must match IMUSample.csvHeader in the app):
    ax,ay,az,gx,gy,gz,crack_pothole_detected,phone_time_ms,device_time_ms,
    latitude,longitude,altitude_m,speed_mps,horizontal_accuracy_m
"""

import argparse
import datetime as dt
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

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


def load_live_analyzer(vehicle: str):
    """Import roadimu from software/roadimu in this repo (only needed for --analyze)."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.normpath(os.path.join(here, "..", "..", "software", "roadimu")))
    try:
        from roadimu.live import LiveAnalyzer
    except ImportError as exc:
        sys.exit(f"--analyze needs numpy/scipy: pip install -r software/roadimu/requirements.txt ({exc})")
    return LiveAnalyzer(vehicle=vehicle)


def serve_analysis(port: int, analyzer) -> None:
    """Tiny JSON API the app polls for live results."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if "vehicle" in q:
                analyzer.set_vehicle(q["vehicle"][0])
            if url.path.rstrip("/") == "/live/reset":
                analyzer.reset()
            if url.path.rstrip("/") in ("", "/live", "/live/reset"):
                body = json.dumps(analyzer.snapshot()).encode()
                self.send_response(200)
            else:
                body = b'{"error": "not found"}'
                self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # the app polls every 2 s; keep the console clean
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


class Writer:
    def __init__(self, path: str, analyzer=None):
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        self.f = open(path, "a", buffering=1, encoding="utf-8")
        if new_file:
            self.f.write(",".join(HEADER) + "\n")
        self.rows = 0
        self.flagged = 0
        self.bad = 0
        self.analyzer = analyzer

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
        if self.analyzer is not None:
            self.analyzer.add_line(line)
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
    ap.add_argument("--analyze", action="store_true", help="run live road analysis and serve it to the app")
    ap.add_argument("--vehicle", choices=["bike", "car"], default="bike", help="vehicle model for --analyze")
    ap.add_argument("--analysis-port", type=int, default=8081, help="HTTP port for --analyze (default 8081)")
    args = ap.parse_args()

    analyzer = None
    if args.analyze:
        analyzer = load_live_analyzer(args.vehicle)
        serve_analysis(args.analysis_port, analyzer)

    out = args.out or f"roadreader_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    writer = Writer(out, analyzer)
    print(f"writing to {out}")
    print(f"in the app: Settings > Forwarding > Host = {local_ip()}, Port = {args.port}, Protocol = {args.protocol.upper()}")
    if analyzer is not None:
        print(f"live analysis ({args.vehicle}) on http://{local_ip()}:{args.analysis_port}/live  (app: Settings > Live analysis port = {args.analysis_port})")
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
