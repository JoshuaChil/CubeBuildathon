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
    POST /upload   multipart/form-data: file (CSV), session_name, device_id, rows, flagged, duration_s, started_at
    GET  /         JSON list of stored sessions (uploads/index.json)
"""

import argparse
import datetime as dt
import json
import os
import socket
import sys
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE_DIR = "uploads"
API_KEY = ""


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
        if self.path.rstrip("/") in ("", "/sessions"):
            self._json(200, {"sessions": load_index()})
        else:
            self._json(404, {"error": "not found"})

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
        entries = [e for e in load_index() if not (e["session_name"] == safe_name and e["device_id"] == entry["device_id"])]
        entries.append(entry)
        save_index(entries)

        print(f"stored {dest} ({entry['rows']} rows, {entry['flagged']} flagged, {len(payload)} bytes)")
        self._json(200, {"ok": True, "stored": entry})

    def log_message(self, fmt, *args):  # quieter default log
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    global STORE_DIR, API_KEY
    ap = argparse.ArgumentParser(description="Receive Road Reader session uploads.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--dir", default=STORE_DIR, help="where to store uploaded CSVs")
    ap.add_argument("--api-key", default="", help="if set, require Authorization: Bearer <key>")
    args = ap.parse_args()

    STORE_DIR = args.dir
    API_KEY = args.api_key
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
