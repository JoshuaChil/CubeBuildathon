#!/usr/bin/env bash
# Start everything the iPhone app talks to, on this computer:
#   - listener.py --analyze : receives the live stream (UDP 5005), saves it, serves live analysis on :8081
#   - upload_server.py      : receives session uploads (:8080), analyses each one, serves results + map data
#
#   ./scripts/start_servers.sh                 # bike
#   ./scripts/start_servers.sh --vehicle car
#   ./scripts/start_servers.sh --protocol tcp  # any other listener.py options pass through
#
# Ctrl-C stops both.  Recorded rides: roadreader_app/listener/roadreader_*.csv
# Uploaded sessions + analysis: roadreader_app/listener/uploads/<device_id>/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Run ./scripts/setup.sh first."; exit 1; }

IP="$("$PY" -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); print(s.getsockname()[0])' 2>/dev/null || echo "<this computer's IP>")"

UPLOAD_PORT="${UPLOAD_PORT:-8080}"  # some apps already use 8080: UPLOAD_PORT=9090 ./scripts/start_servers.sh
if lsof -ti ":$UPLOAD_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Note: another program already listens on port $UPLOAD_PORT ($(ps -o comm= -p "$(lsof -ti ":$UPLOAD_PORT" -sTCP:LISTEN | head -1)" 2>/dev/null))."
  echo "      If uploads fail, rerun with UPLOAD_PORT=9090 ./scripts/start_servers.sh"
fi

cd "$ROOT/roadreader_app/listener"
"$PY" upload_server.py --port "$UPLOAD_PORT" > upload_server.log 2>&1 &
UPLOAD_PID=$!
trap 'kill $UPLOAD_PID 2>/dev/null || true' EXIT INT TERM

cat <<EOF

=====================================================================
 In the Road Reader app (gear icon -> Settings), enter:
   Forwarding  : Host = $IP   Port = 5005   Protocol = UDP
   Road analysis: Vehicle = (bike/car)   Live analysis = on   Port = 8081
   Web database: Upload URL = http://$IP:$UPLOAD_PORT/upload
 Phone and computer must be on the same Wi-Fi (or the computer on the phone's hotspot).
 macOS: click "Allow" if asked whether Python may accept incoming connections.
 Upload server log: roadreader_app/listener/upload_server.log
=====================================================================

EOF
"$PY" listener.py --analyze "$@"
