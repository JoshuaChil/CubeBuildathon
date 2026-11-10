#!/usr/bin/env python3
"""
GroundTruth dashboard server: serves the live page and relays the phone's stream to it.

    Road Reader app (Live Broadcasting) --POST /ingest, JSON, ~4x/s--> this server --SSE /stream--> browser(s)

    python3 server.py                # http://0.0.0.0:8090, serves index.html from this folder
    python3 server.py --port 9000

Standard library only. The phone and the computer running this must be on the same Wi-Fi (or the
computer on the phone's hotspot); enter the printed URL under Settings > Live dashboard in the app.

HTTP API
    GET  /                 the dashboard (static files from this directory)
    GET  /status           {"ok": true, "last_id": N, "viewers": n, "phone": {"session": .., "rows": .., "seen_ms_ago": ..}}
    POST /ingest           {"device_id": .., "session": .., "rows": [{"t": ms, "ax", "ay", "az", "gx", "gy", "gz",
                                                                      "lat", "lon", "flag"}, ...]}
                           or {"device_id": .., "session": .., "event": "start" | "stop", "rows_sent": N}
                           -> {"ok": true, "id": <id of the last row>, "rows": <rows accepted>}
    GET  /stream           text/event-stream. One event per accepted POST:
                               id: <last row id>
                               data: {"rows": [{"id", "at", "ax", "ay", "az", "gx", "gy", "gz", "lat", "lon", "flag", "session"}, ...]}
                           or   data: {"event": "start"|"stop", "session": .., "device": .., "rows_sent": N}
                           ?after=<id> (or the Last-Event-ID header a reconnecting browser sends) replays the
                           buffered rows newer than that id first, so a dropped connection loses nothing.
"""

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROW_KEYS = ("ax", "ay", "az", "gx", "gy", "gz")


def local_ip() -> str:
    """Best-effort LAN IP to type into the app's Settings > Live dashboard field."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Relay:
    """Thread-safe row buffer plus fan-out to the browsers subscribed on /stream."""

    def __init__(self, keep_rows: int = 120_000):
        self.lock = threading.Lock()
        self.buffer: deque = deque(maxlen=keep_rows)
        self.subscribers: "set[queue.Queue]" = set()
        self.last_id = 0
        self.phone = {"session": None, "device": None, "rows": 0, "seen": None}
        self.total_rows = 0
        self._last_report = 0.0

    # -- phone side --------------------------------------------------------------------------------------------

    def ingest(self, payload: dict) -> dict:
        session = str(payload.get("session") or "session")
        device = str(payload.get("device_id") or "")
        event = payload.get("event")
        rows_in = payload.get("rows") or []
        if not isinstance(rows_in, list):
            raise ValueError("rows must be a list")

        normalized = []
        with self.lock:
            for r in rows_in:
                if not isinstance(r, dict):
                    continue
                try:
                    at = int(r["t"])
                    vals = {k: float(r[k]) for k in ROW_KEYS}
                except (KeyError, TypeError, ValueError):
                    continue
                self.last_id += 1
                row = {"id": self.last_id, "at": at, **vals,
                       "lat": _num_or_none(r.get("lat")), "lon": _num_or_none(r.get("lon")),
                       "flag": 1 if _num_or_none(r.get("flag")) else 0, "session": session}
                self.buffer.append(row)
                normalized.append(row)
            self.total_rows += len(normalized)
            now = time.time()
            self.phone.update(session=session, device=device or self.phone["device"], seen=now)
            if event == "start":
                self.phone["rows"] = 0
            self.phone["rows"] += len(normalized)
            subs = list(self.subscribers)
            last_id = self.last_id

        if normalized:
            self._broadcast(subs, _sse(last_id, {"rows": normalized}))
        if event in ("start", "stop"):
            self._broadcast(subs, _sse(last_id, {"event": event, "session": session, "device": device,
                                                 "rows_sent": payload.get("rows_sent")}))
            print(f"[{time.strftime('%H:%M:%S')}] phone {event}: {session}"
                  + (f" ({payload.get('rows_sent')} rows sent)" if event == "stop" else "")
                  + f" | viewers: {len(subs)}", flush=True)
        elif normalized and time.time() - self._last_report > 5:
            self._last_report = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] {session}: {self.phone['rows']} rows this session,"
                  f" {len(normalized)} in last batch | viewers: {len(subs)}", flush=True)
        return {"ok": True, "id": last_id, "rows": len(normalized)}

    @staticmethod
    def _broadcast(subs, message: bytes) -> None:
        for q in subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass  # a stalled browser; it will catch up through Last-Event-ID when it reconnects

    # -- browser side ------------------------------------------------------------------------------------------

    def subscribe(self, after):
        """Returns (queue, replay bytes or None). `after` is None for 'only new rows from now on'."""
        q: queue.Queue = queue.Queue(maxsize=4000)
        with self.lock:
            replay = [r for r in self.buffer if r["id"] > after] if after is not None else []
            self.subscribers.add(q)
            last_id = self.last_id
        return q, (_sse(last_id, {"rows": replay}) if replay else None)

    def unsubscribe(self, q) -> None:
        with self.lock:
            self.subscribers.discard(q)

    def status(self) -> dict:
        with self.lock:
            seen = self.phone["seen"]
            return {
                "ok": True, "last_id": self.last_id, "viewers": len(self.subscribers), "total_rows": self.total_rows,
                "phone": None if seen is None else {
                    "session": self.phone["session"], "device": self.phone["device"], "rows": self.phone["rows"],
                    "seen_ms_ago": int((time.time() - seen) * 1000),
                },
            }


def _num_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _sse(event_id: int, data: dict) -> bytes:
    return f"id: {event_id}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def make_handler(relay: Relay, directory: str):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Last-Event-ID")
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            if path == "/status":
                return self._json(200, relay.status())
            if path == "/stream":
                return self._stream(url)
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            if path != "/ingest":
                return self._json(404, {"ok": False, "error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("body must be a JSON object")
                result = relay.ingest(payload)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(400, {"ok": False, "error": str(exc)})
            self._json(200, result)

        def _json(self, status: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, url) -> None:
            after = None
            q = parse_qs(url.query)
            raw = (q.get("after") or [None])[0] or self.headers.get("Last-Event-ID")
            if raw is not None:
                try:
                    after = int(raw)
                except ValueError:
                    after = None
            sub, replay = relay.subscribe(after)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                self.wfile.write(replay if replay else b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = sub.get(timeout=15)
                    except queue.Empty:
                        msg = b": keepalive\n\n"
                    self.wfile.write(msg)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                relay.unsubscribe(sub)

        def log_message(self, fmt, *args):  # the phone posts 4x/s; keep the console for session events
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--dir", default=HERE, help="folder with index.html (default: this folder)")
    ap.add_argument("--keep-rows", type=int, default=120_000, help="rows kept for reconnecting browsers")
    args = ap.parse_args()

    relay = Relay(keep_rows=args.keep_rows)
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(relay, args.dir))
    httpd.daemon_threads = True
    ip = local_ip()
    print("GroundTruth dashboard server")
    print(f"  Dashboard : http://localhost:{args.port}   (or http://{ip}:{args.port} from another computer)")
    print(f"  Phone     : Settings > Live dashboard > URL = http://{ip}:{args.port}")
    print("  Ctrl-C stops.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
