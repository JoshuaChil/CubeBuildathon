#!/usr/bin/env python3
"""
Road Reader upload test server.

Accepts the multipart POST that the app's "Upload" button sends and stores each session
CSV plus its metadata, so you can test the upload flow before the real web database exists.
Standard library only.

    python3 upload_server.py                 # http://0.0.0.0:8080/upload, saves into ./uploads/
    python3 upload_server.py --port 9000 --dir /path/to/store --api-key secret123

In the app: Settings > Web database > Upload URL = http://<this computer's IP>:8080/upload

Endpoints:
    POST /upload   multipart/form-data: file (CSV), session_name, device_id, rows, flagged, duration_s, started_at,
                   vehicle (bike|car, optional)
    GET  /         JSON list of stored sessions (uploads/index.json)
    GET  /results/<device_id>/<session_name>   road analysis of that session (JSON)
    GET  /report/<device_id>/<session_name>    one-page report figure (PNG)

Road analysis: if numpy/scipy are installed (pip install -r ../../software/roadimu/requirements.txt), every
upload is analysed with software/roadimu and the summary is returned in the upload response under "analysis".
Pass --no-analyze to just store files.
"""

import argparse
import datetime as dt
import json
import os
import socket
import sys
import traceback
from urllib.parse import unquote
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE_DIR = "uploads"
API_KEY = ""
ANALYZE = True
_ROADIMU = None


def _roadimu():
    """Import software/roadimu lazily; returns None (and analysis is skipped) if numpy/scipy are missing."""
    global _ROADIMU
    if _ROADIMU is None:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.normpath(os.path.join(here, "..", "..", "software", "roadimu")))
        try:
            from roadimu.plots import report_figure
            from roadimu.topo import TopoConfig, reconstruct
            from roadimu.topo.roughness import condition

            _ROADIMU = (TopoConfig, reconstruct, report_figure, condition)
        except ImportError as exc:
            print(f"road analysis disabled ({exc}); pip install -r software/roadimu/requirements.txt")
            _ROADIMU = False
    return _ROADIMU or None


def analysis_paths(csv_path: str) -> tuple[str, str]:
    base = os.path.splitext(csv_path)[0]
    return base + ".analysis.json", base + ".report.png"


def analyze(csv_path: str, vehicle: str) -> dict | None:
    """Run the road-topography pipeline on an uploaded session; returns a short summary."""
    mod = _roadimu()
    if not mod:
        return None
    TopoConfig, reconstruct, report_figure, condition = mod
    try:
        res = reconstruct(csv_path, TopoConfig(vehicle=vehicle if vehicle in ("bike", "car") else "bike"))
        result = res.to_dict()
        result["vehicle"] = vehicle
        json_path, png_path = analysis_paths(csv_path)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
        report_figure(res, png_path)
        s = result["summary"]
        iri = s.get("mean_iri_m_per_km")
        return {
            "ok": True,
            "vehicle": vehicle,
            "distance_m": round(s.get("distance_m") or 0.0, 1),
            "mean_iri_m_per_km": round(iri, 2) if iri is not None else None,
            "condition": condition(iri) if iri is not None else None,
            "potholes": s.get("potholes", 0),
            "bumps": s.get("bumps", 0),
            "notes": result.get("notes", []),
        }
    except Exception as exc:  # never fail the upload because analysis failed
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def index_path() -> str:
    return os.path.join(STORE_DIR, "index.json")


def load_index() -> list:
    try:
        with open(index_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def save_index(entries: list) -> None:
    with open(index_path(), "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def parse_multipart(content_type: str, body: bytes) -> tuple[dict, dict]:
    """Returns (fields, files) where files maps field name -> (filename, bytes)."""
    msg = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = (filename, payload)
        else:
            fields[name] = payload.decode("utf-8", errors="replace").strip()
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "RoadReaderUpload/1.0"

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/sessions"):
            return self._json(200, {"sessions": load_index()})
        parts = [unquote(p) for p in path.split("/") if p]
        if len(parts) == 3 and parts[0] in ("results", "report"):
            csv_path = os.path.join(STORE_DIR, os.path.basename(parts[1]), os.path.basename(parts[2]))
            json_path, png_path = analysis_paths(csv_path)
            if parts[0] == "results" and os.path.exists(json_path):
                with open(json_path, "rb") as f:
                    data = f.read()
                return self._send(200, "application/json", data)
            if parts[0] == "report" and os.path.exists(png_path):
                with open(png_path, "rb") as f:
                    data = f.read()
                return self._send(200, "image/png", data)
            return self._json(404, {"error": "no analysis for that session (was it uploaded with analysis enabled?)"})
        self._json(404, {"error": "not found"})

    def _send(self, status: int, ctype: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.rstrip("/") != "/upload":
            return self._json(404, {"error": "not found"})

        if API_KEY and self.headers.get("Authorization") != f"Bearer {API_KEY}":
            return self._json(401, {"error": "bad or missing API key"})

        length = int(self.headers.get("Content-Length") or 0)
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            return self._json(400, {"error": "expected multipart/form-data"})

        body = self.rfile.read(length)
        fields, files = parse_multipart(ctype, body)
        if "file" not in files:
            return self._json(400, {"error": "missing 'file' part"})

        filename, payload = files["file"]
        safe_name = os.path.basename(fields.get("session_name") or filename or "session.csv")
        device_dir = os.path.join(STORE_DIR, os.path.basename(fields.get("device_id") or "unknown-device"))
        os.makedirs(device_dir, exist_ok=True)
        dest = os.path.join(device_dir, safe_name)
        with open(dest, "wb") as f:
            f.write(payload)

        entry = {
            "session_name": safe_name,
            "device_id": fields.get("device_id", ""),
            "rows": int(fields.get("rows") or 0),
            "flagged": int(fields.get("flagged") or 0),
            "duration_s": float(fields.get("duration_s") or 0),
            "started_at": fields.get("started_at", ""),
            "bytes": len(payload),
            "received_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "path": dest,
        }
        if ANALYZE:
            entry["analysis"] = analyze(dest, fields.get("vehicle", "bike"))
        entries = [e for e in load_index() if not (e["session_name"] == safe_name and e["device_id"] == entry["device_id"])]
        entries.append(entry)
        save_index(entries)

        print(f"stored {dest} ({entry['rows']} rows, {entry['flagged']} flagged, {len(payload)} bytes)")
        a = entry.get("analysis")
        if a and a.get("ok"):
            print(f"  analysis: {a['distance_m']:.0f} m, IRI {a['mean_iri_m_per_km']} m/km ({a['condition']}), {a['potholes']} potholes, {a['bumps']} bumps")
        elif a:
            print(f"  analysis failed: {a.get('error')}")
        self._json(200, {"ok": True, "stored": entry, "analysis": a})

    def log_message(self, fmt, *args):  # quieter default log
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    global STORE_DIR, API_KEY, ANALYZE
    ap = argparse.ArgumentParser(description="Receive Road Reader session uploads.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dir", default=STORE_DIR, help="where to store uploaded CSVs")
    ap.add_argument("--api-key", default="", help="if set, require Authorization: Bearer <key>")
    ap.add_argument("--no-analyze", action="store_true", help="store uploads without running road analysis")
    args = ap.parse_args()

    STORE_DIR = args.dir
    API_KEY = args.api_key
    ANALYZE = not args.no_analyze
    if ANALYZE:
        _roadimu()
    os.makedirs(STORE_DIR, exist_ok=True)

    print(f"storing uploads in {os.path.abspath(STORE_DIR)}")
    print(f"in the app: Settings > Web database > Upload URL = http://{local_ip()}:{args.port}/upload")
    if API_KEY:
        print("API key required")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
