# Road Reader

iPhone app (SwiftUI) that reads IMU rows from an ESP32 over Bluetooth LE, labels each row
as a crack/pothole or not while you drive, tags it with the phone's GPS, saves the dataset on the
phone, and streams it live to a computer on the same network.

```
ESP32 (ICM-42688) --BLE--> iPhone (Road Reader) --UDP/TCP over Wi-Fi--> computer (listener.py -> CSV)
                              |  + GPS
                              +-> Documents/RoadReader/session_*.csv  --Sessions > Upload--> web database
```

## Layout

| Path | What it is |
| --- | --- |
| `RoadReader.xcodeproj` | Xcode 16 project (folder-synced: any file you add under `RoadReader/` is picked up automatically) |
| `RoadReader/RoadReaderApp.swift` | App entry point |
| `RoadReader/Models/IMUSample.swift` | Row model, line parser, CSV formatting (the single source of truth for columns) |
| `RoadReader/Models/AppSettings.swift` | Persisted settings (device name, host/port, protocol, tap duration) |
| `RoadReader/Services/BluetoothManager.swift` | Scan on launch, auto-connect, reconnect, newline framing |
| `RoadReader/Services/LocationManager.swift` | GPS fixes for geotagging |
| `RoadReader/Services/StreamForwarder.swift` | UDP/TCP sender (Network framework) with auto-retry |
| `RoadReader/Services/DatasetRecorder.swift` | Buffered CSV writer in Documents |
| `RoadReader/ViewModels/SessionViewModel.swift` | Wires it all together; hold/tap labelling logic; 10 Hz UI refresh |
| `RoadReader/Services/SessionStore.swift` | Lists saved sessions, computes rows/flagged/duration, tracks what has been uploaded |
| `RoadReader/Services/SessionUploader.swift` | Multipart POST of a session CSV to the web database |
| `RoadReader/Views/` | `ScanView` → `ReadingsView` (Start Readings, live table, Detect Bump), `SettingsView`, `SessionsView` |
| `listener/listener.py` | Computer-side live receiver, writes CSV. Standard library only. |
| `listener/upload_server.py` | Stand-in web database: accepts session uploads and stores them. Standard library only. |
| `../roadreader_code /esp32/esp32.ino` | The ESP32 firmware (ICM-42688 over SPI, BLE Nordic UART Service at 50 Hz) |

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

## Upload sessions to the web database

The app POSTs each session as `multipart/form-data` to the URL set in *Settings → Web database*:

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

## ESP32

The firmware lives in `../roadreader_code /esp32/esp32.ino` (ICM-42688 on SPI, CS on GPIO 5, LED on GPIO 2).
Flash it with the Arduino IDE (ESP32 board package plus the `ICM42688` library). It advertises as `RoadReader`
with the Nordic UART Service, streams `ax,ay,az,gx,gy,gz` at 50 Hz once the phone subscribes, honours
`START`/`STOP`, and shows a blinking LED while advertising and a solid LED while the phone is connected.
Change `SAMPLE_PERIOD_MS` for a different rate. Any other firmware works as long as it notifies newline-terminated
CSV lines on a characteristic; set the service UUID in the app's Settings if it is not the Nordic UART Service.

## Troubleshooting

- *Stuck on "Looking for Road Reader device…"*: check the ESP32 is advertising (serial monitor) and that the
  name prefix / service UUID in Settings match.
- *Live table shows "Unparsed lines"*: the firmware is sending something other than 6 or 7 numbers per line;
  the last raw line is shown so you can compare.
- *Forwarding shows an error*: the listener is not running, the IP is wrong, or the phone and computer are on
  different networks. UDP will show "Sending" even if nothing is listening; watch the listener's row count.
