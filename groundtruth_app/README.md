# Road Reader

iPhone app (SwiftUI) that reads IMU rows from an ESP32 over Bluetooth LE, labels each row
as a crack/pothole or not while you drive, tags it with the phone's GPS, saves the dataset on the
phone, and streams it live to a computer on the same network. The computer turns the stream into
**road roughness and potholes** (see [Road analysis](#road-analysis)), and the app shows them live and on a map.

```
ESP32 (ICM-42688) --BLE--> iPhone (Road Reader) --UDP/TCP over Wi-Fi--> computer (listener.py -> CSV)
                              |  + GPS        ^                            |  --analyze: software/roadimu
                              |               +---- GET /live (every 2 s) -+  road condition, potholes
                              +-> Documents/RoadReader/session_*.csv  --Sessions > Upload--> upload_server.py
                                                    map + results <-- GET /results/... --+  (analyses every upload)
```

## Layout

| Path | What it is |
| --- | --- |
| `RoadReader.xcodeproj` | Xcode 16 project (folder-synced: any file you add under `RoadReader/` is picked up automatically) |
| `RoadReader/RoadReaderApp.swift` | App entry point |
| `RoadReader/Models/IMUSample.swift` | Row model, line parser, CSV formatting (the single source of truth for columns) |
| `RoadReader/Models/AppSettings.swift` | Persisted settings (device name, host/port, protocol, tap duration, Supabase, upload server) |
| `RoadReader/Config/SupabaseConfig.swift` | **Put your Supabase URL and anon key here** |
| `RoadReader/Services/BluetoothManager.swift` | Scan on launch, auto-connect, reconnect, newline framing |
| `RoadReader/Services/LocationManager.swift` | GPS fixes for geotagging |
| `RoadReader/Services/StreamForwarder.swift` | UDP/TCP sender (Network framework) with auto-retry |
| `RoadReader/Services/DatasetRecorder.swift` | Buffered CSV writer in Documents |
| `RoadReader/ViewModels/SessionViewModel.swift` | Wires it all together; hold/tap labelling logic; 10 Hz UI refresh |
| `RoadReader/Services/SessionStore.swift` | Lists saved sessions, computes rows/flagged/duration, tracks what has been uploaded |
| `RoadReader/Services/SupabaseUploader.swift` | Inserts a session into Supabase, one row per reading (PostgREST, no SDK) |
| `RoadReader/Services/SessionUploader.swift` | Multipart POST of a session CSV to the optional upload/analysis server |
| `supabase/schema.sql` | SQL for the Supabase `readings` table and its row-level-security policies |
| `RoadReader/Views/` | `ScanView` → `ReadingsView` (Start Readings, live table, Detect Bump), `SettingsView`, `SessionsView` |
| `listener/listener.py` | Computer-side live receiver, writes CSV. Standard library only. |
| `listener/upload_server.py` | Stand-in web database: accepts session uploads and stores them. Standard library only. |
| `../groundtruth_code/esp32/esp32.ino` | The ESP32 firmware (ICM-42688 over SPI, BLE Nordic UART Service at 50 Hz) |

## App flow

1. **Open** → the app immediately scans for Bluetooth devices. It auto-connects to the first device whose
   name starts with `RoadReader` or that advertises the Nordic UART Service. Any other named device can be tapped.
2. **Connected** → a **Start Readings** button appears. Tapping it subscribes to notifications, writes
   `START` to the ESP32, opens a new CSV, starts GPS, and opens the network connection.
3. **Live** → rows scroll in a table (ax ay az gx gy gz | Bump) with rows/second, flagged count and sent count.
4. **Detect Bump** → press and hold while crossing an obstruction. Every row received while the button is
   down gets `crack_pothole_detected = 1`. A quick tap keeps the label on for at least the
   "minimum mark duration" (default 0.5 s, adjustable in Settings) so a single hit still labels a run of rows.
5. **Stop Readings** closes the file and the connection.
6. **Sessions** (document icon, top left) lists every CSV on the phone with its date, row count, flagged count,
   duration and size, plus a summary of how many are uploaded. Each row has **Upload**; the toolbar has
   **Upload All**. Uploaded sessions show a green check. Long-press a row to share the CSV (AirDrop/Files) or
   delete it; swipe to delete. Files also appear in the Files app under *On My iPhone → Road Reader*.

## Data format

**ESP32 → phone** (one line per sample, `\n` terminated, comma separated):

```
ax,ay,az,gx,gy,gz             # 6 fields
t_ms,ax,ay,az,gx,gy,gz        # or 7 fields; the first is the ESP32 millis() clock
```

**Phone → CSV file and → computer** (same line format for both):

| # | Column | Notes |
| --- | --- | --- |
| 1-6 | `ax, ay, az, gx, gy, gz` | As received from the ESP32 |
| 7 | `crack_pothole_detected` | **1** while Detect Bump is held/tapped, else **0** |
| 8 | `phone_time_ms` | Unix epoch ms when the phone received the row |
| 9 | `device_time_ms` | ESP32 clock if it sent one, else blank |
| 10-14 | `latitude, longitude, altitude_m, speed_mps, horizontal_accuracy_m` | Latest GPS fix (blank until a fix exists) |

## Build and run on an iPhone

Bluetooth does not work in the Simulator, so use a real phone.

1. `open RoadReader.xcodeproj`
2. Select the `RoadReader` target → *Signing & Capabilities* → pick your Team (a free personal team works).
   Change the bundle identifier if Xcode complains it is taken.
3. Plug in the iPhone, select it as the run destination, press Run. Accept the Bluetooth, Location and
   Local Network prompts on first launch.
4. Background modes (`bluetooth-central`, `location`) are already in `Info.plist`, so recording continues with the screen off.

Command-line check that it compiles:

```
xcodebuild -project RoadReader.xcodeproj -scheme RoadReader -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

## Receive on the computer

```
cd listener
python3 listener.py                  # UDP, port 5005, writes roadreader_<timestamp>.csv
python3 listener.py --protocol tcp   # if you prefer guaranteed delivery
```

It prints the computer's LAN IP; enter it in the app under *Settings → Forwarding → Host* with the matching
port and protocol. Phone and computer must be on the same Wi-Fi, or run the listener on a laptop joined to the
phone's Personal Hotspot (then the host is usually `172.20.10.1`-ish, shown by the script).

## Upload sessions to Supabase

1. In the Supabase dashboard open *SQL editor* and run [`supabase/schema.sql`](supabase/schema.sql). It creates the
   `readings` table and lets the anon key insert rows (and read `session_number` so numbering continues).
2. Open *Project Settings → API* and copy the **Project URL** and the **anon public** key into
   [`RoadReader/Config/SupabaseConfig.swift`](RoadReader/Config/SupabaseConfig.swift). Rebuild. (Or type them into
   *Settings → Supabase* on the phone; the phone's values override the file.) Never use the `service_role` key.
3. *Sessions → Upload* (or *Upload All*). Each CSV becomes one table row per reading:

| Table column | From |
| --- | --- |
| `session_date` | The phone's local date of the first row |
| `session_number` | 1 + the highest number already in the table for that date (checked via `GET ...?select=session_number`) |
| `recorded_at` | `phone_time_ms` as a UTC timestamp |
| `ax, ay, az, gx, gy, gz` | As recorded |
| `latitude, longitude` | GPS, `NULL` for rows before the first fix |
| `anomaly_detected` | `crack_pothole_detected` (1 while Detect Bump was held) |

Rows are inserted in chunks of 1000 with `POST https://<project>.supabase.co/rest/v1/readings` and the headers
`apikey` / `Authorization: Bearer <anon key>` (see `SupabaseUploader.swift`). The row shows a progress ring while
inserting, then "Supabase <date> · session N". A session that failed half-way is retried from the start on the next
Upload (delete the partial rows in Supabase first, or they will carry a different session number).

## Live Broadcasting (real-time into Supabase)

Turn on **Live Broadcasting** on the Ready screen (or Settings → Supabase). The Start button becomes
**Start Live Session** and the app streams every reading into the Supabase `readings` table while you ride:

- On start it reserves the next `session_number` for today's `session_date`, retrying until Supabase answers.
- Rows are queued as they arrive (200 Hz) and inserted in batches about once a second, up to 1000 rows per
  request. The LIVE bar shows rows in Supabase, rows queued, and any reconnect errors.
- If the connection drops, batches are retried with backoff and nothing is lost unless the phone is offline
  for roughly five minutes (60,000 rows), after which the oldest rows are dropped and counted.
- On Stop the remaining rows are drained. If every row arrived, the session is marked as uploaded in the
  Sessions list; otherwise an alert says how many rows were missed and you can Upload the CSV in full later
  (that creates a new session number).
- The local CSV and the computer stream keep working exactly as before. Live Broadcasting is additive.

For a dashboard that follows the ride, run the Realtime block at the end of `supabase/schema.sql` once, then
subscribe to `INSERT` on `public.readings`, or poll the `public.live_session` view (today's latest session).
Two phones broadcasting on the same day could pick the same session number; add `device_id` to the table if
that matters for your demo.

## Upload sessions to the optional upload/analysis server

If an upload URL is also set in *Settings → Upload server*, Upload sends the CSV there too (used for the road analysis).
Both destinations are tracked separately; the green check appears when every configured destination has the session.

The app POSTs each session as `multipart/form-data` to the URL set in *Settings → Upload server*:

| Part | Content |
| --- | --- |
| `file` | the CSV, `Content-Type: text/csv`, filename = session name |
| `session_name` | e.g. `session_20261107_143012.csv` |
| `device_id` | stable per-install UUID so sessions can be grouped by phone |
| `rows`, `flagged`, `duration_s`, `started_at` | summary stats; `started_at` is ISO 8601 |
| header `Authorization: Bearer <key>` | only when an API key is set in Settings |

Any 2xx response marks the session as uploaded on the phone. Until the real backend exists, test with the
bundled stand-in:

```
cd listener
python3 upload_server.py                 # http://<your IP>:8080/upload, stores into ./uploads/<device_id>/
python3 upload_server.py --api-key abc   # require the Bearer token
curl http://localhost:8080/              # JSON list of stored sessions
```

Plain `http://` works for local-network addresses (the app allows local networking in `Info.plist`). A hosted
database must use `https://`.

## Road analysis

The road-topography pipeline in [`../software/roadimu`](../software/roadimu/README.md) runs on the computer and
the app shows its results. Install its dependencies once:

```
pip install -r ../software/roadimu/requirements.txt
```

**While riding (live):**

```
cd listener
python3 listener.py --analyze                # bike; add --vehicle car for a car
```

The listener still writes the CSV as before, and also re-analyses the ride every few seconds. It serves the
result on `http://<computer>:8081/live`. In the app, *Settings → Road analysis* sets the vehicle, the live toggle
and the port (default 8081; the host is the forwarding host). While recording, a **Road analysis** card under the
stats shows:
- the current road condition (good / fair / poor / very poor, from IRI per 20 m),
- potholes and bumps found so far,
- the distance ridden and the last detected event.

Stand still for ~2 s after tapping Start Readings, because that's when the box's mounting is calibrated.

**After the ride:** `upload_server.py` now analyses every upload (if numpy/scipy are installed). The upload
response includes a summary, which the Sessions list shows under each session (for example "Poor · IRI 5.2 ·
3 potholes · 0.9 km"). Tap a session to see:
- a **map** of the route coloured by condition,
- pothole/bump pins,
- the list of events,
- the full report figure.

The server also serves `GET /results/<device_id>/<session_name>` (JSON) and `/report/...` (PNG).

**Demo ride in the app (no sensor):** on the scan screen, tap **Demo ride (no sensor)**, then **Start Readings**.
The app plays a recorded 3-minute NYC bike ride (`RoadReader/Resources/demo_ride.csv`, 200 Hz ESP32 lines with GPS)
through exactly the same path as the sensor:
- saved to a session CSV,
- streamed to the computer,
- live Road analysis card,
- Detect Bump labels,
- upload and the results map.

Tested on an iPhone 16 Pro. Regenerate the file with
`python -m roadimu simulate --scenario nyc_mixed --vehicle bike --format demo --out ../groundtruth_app/RoadReader/Resources`
(run from `software/roadimu`).

Settings can also be passed as launch arguments, and they're saved: for example
`xcrun devicectl device process launch --device <id> -- <bundle id> -forwardHost 10.0.0.5 -uploadURL http://10.0.0.5:8080/upload`.

**Demo without the phone:** simulate a ride in the app's format and stream it like the phone would:

```
cd ../software/roadimu
python -m roadimu simulate --scenario nyc_mixed --vehicle bike --format app --out out/app
python -m roadimu replay out/app/session_nyc_mixed_bike_200hz.csv --speedup 4   # to listener.py on this computer
```

You can also copy the CSV into the app's Documents/RoadReader folder (Files app) and upload it from Sessions.

## ESP32

The firmware lives in `../groundtruth_code/esp32/esp32.ino` (ICM-42688 on SPI, CS on GPIO 5, LED on GPIO 2).
Flash it with the Arduino IDE (ESP32 board package plus the `ICM42688` library). It advertises as `RoadReader`
with the Nordic UART Service, and honours `START`/`STOP`. The LED blinks while advertising and stays solid while
the phone is connected. Once the phone subscribes, it streams `t_ms,ax,ay,az,gx,gy,gz`:
- at **200 Hz**,
- with up to 3 lines per notification,
- timed with `micros()`, so the rate doesn't drift.

**Why 200 Hz and `t_ms`** (changed from the original 50 Hz). A bike frame vibrates at about 28–40 Hz. At 50 Hz
that vibration aliases into fake road frequencies. In simulation, the bike road profile dropped from a
correlation of 0.86 (200 Hz) to 0.13 (50 Hz), with about 60 false potholes per 4 rides. Cars were fine at 50 Hz.
The `t_ms` timestamp lets the analysis place every sample exactly, even when a Bluetooth packet is lost.

Any other firmware works as long as it notifies newline-terminated CSV lines on a characteristic; set the service
UUID in the app's Settings if it is not the Nordic UART Service.

## Troubleshooting

- *Stuck on "Looking for Road Reader device…"*: check the ESP32 is advertising (serial monitor) and that the
  name prefix / service UUID in Settings match.
- *Live table shows "Unparsed lines"*: the firmware is sending something other than 6 or 7 numbers per line;
  the last raw line is shown so you can compare.
- *Forwarding shows an error*: the listener is not running, the IP is wrong, or the phone and computer are on
  different networks. UDP will show "Sending" even if nothing is listening; watch the listener's row count.
