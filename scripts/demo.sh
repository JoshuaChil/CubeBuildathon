#!/usr/bin/env bash
# Self-test of the computer side, no hardware needed (~1 min):
# simulates a bike ride in the app's exact format, streams it to listener.py like the phone does,
# prints the live analysis as it updates, then uploads it to upload_server.py and prints the result.
# Uses ports 5005/8080/8081, so stop ./scripts/start_servers.sh first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Run ./scripts/setup.sh first."; exit 1; }
TMP="$(mktemp -d)"
cleanup() { kill ${LISTEN_PID:-} ${UPLOAD_PID:-} 2>/dev/null || true; }
trap cleanup EXIT INT TERM

cd "$ROOT/software/roadimu"
echo "1/4 Simulating a 0.9 km NYC bike ride in the app's CSV format..."
"$PY" -m roadimu simulate --scenario nyc_mixed --vehicle bike --format app --out "$TMP" >/dev/null
CSV="$TMP/session_nyc_mixed_bike_200hz.csv"

echo "2/4 Starting listener.py --analyze and upload_server.py..."
(cd "$ROOT/roadreader_app/listener" && exec "$PY" listener.py --analyze --out "$TMP/live.csv" >"$TMP/listener.log" 2>&1) &
LISTEN_PID=$!
(cd "$ROOT/roadreader_app/listener" && exec "$PY" upload_server.py --dir "$TMP/uploads" >"$TMP/upload.log" 2>&1) &
UPLOAD_PID=$!
sleep 2

echo "3/4 Streaming the ride like the phone does (4x real time); live analysis:"
"$PY" -m roadimu replay "$CSV" --speedup 4 >/dev/null &
REPLAY_PID=$!
while kill -0 $REPLAY_PID 2>/dev/null; do
  sleep 4
  curl -s localhost:8081/live | "$PY" -c 'import json,sys; d=json.load(sys.stdin); c=d.get("current") or {}; print(f"   {d["status"]:10s} {d["duration_s"]:6.1f} s  {d.get("distance_m") or 0:5.0f} m  now: {c.get("condition","-"):9s}  potholes {d.get("potholes","-")}  bumps {d.get("bumps","-")}  | {d["message"][:50]}")'
done

echo "4/4 Uploading the session like the app's Upload button..."
curl -s -F "file=@$CSV;type=text/csv" -F session_name=session_demo.csv -F device_id=DEMO -F rows=0 -F flagged=0 \
     -F duration_s=0 -F vehicle=bike localhost:8080/upload \
  | "$PY" -c 'import json,sys; a=json.load(sys.stdin)["analysis"]; print("   analysis:", a)'
curl -s -o /dev/null -w "   map data: GET /results/DEMO/session_demo.csv -> HTTP %{http_code}\n" localhost:8080/results/DEMO/session_demo.csv
echo
echo "Demo OK: the computer side works. Files are in $TMP"
