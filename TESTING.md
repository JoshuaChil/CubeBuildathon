# Testing the full system with real hardware

This guide is for whoever has the **ESP32 + ICM-42688 box** and an **iPhone**. It takes you from a fresh clone to
seeing real road analysis in the app, in about 45 minutes. At each step, the "Pass" line tells you whether to move
on.

```
ESP32 + IMU ──BLE──► iPhone (Road Reader app) ──Wi-Fi──► your computer: listener.py --analyze  (live)
   (USB to the                                                          upload_server.py        (after the ride)
    computer only for                       ◄── live road condition / map + potholes ──┘
    flashing + serial monitor)
```

## What you need

- **A Mac** with Xcode 16+ (the app is iOS-only) and **Python 3.10+**: check with `python3 --version`.
- **An iPhone** running iOS 17+, with a USB cable. A free Apple ID is enough to install the app.
- **The ESP32 box** and its USB cable. You also need the **Arduino IDE**, with:
  - the ESP32 board package (*Boards Manager → "esp32" by Espressif*),
  - the **ICM42688** library (*Library Manager → "ICM42688"*, the one the sketch's `#include "ICM42688.h"` uses).
- **Network:** the phone and the computer on **the same Wi-Fi**. If the Wi-Fi isolates clients (common on campus
  networks), turn on the phone's Personal Hotspot and join it from the computer instead.

## 1. Get the code and set up Python (5 min)

```bash
git clone https://github.com/JoshuaChil/CubeBuildathon.git && cd CubeBuildathon
```
```bash
./scripts/setup.sh
```
**Pass:** the tests run, and it ends with `Setup OK`.

## 2. Check the computer side with no hardware (1 min)

```bash
./scripts/demo.sh
```
This simulates a bike ride in the app's exact format, streams it to the listener the way the phone would, and
prints the live analysis. Then it uploads the ride.

**Pass:** the status goes `collecting` → `ok`, the distance grows to ~900 m, and the run ends with `Demo OK`.
If this fails, the problem is on the computer, not the hardware.

## 3. Flash the ESP32 (10 min)

1. Plug the ESP32 into the computer and open `roadreader_code /esp32/esp32.ino` in the Arduino IDE. Note the
   **space** at the end of the folder name.
2. Choose your ESP32 board and port, then click **Upload**.
3. Open the **Serial Monitor** at **115200** baud.

**Pass:**
- **Startup:** you see `ICM42688 ready` and `BLE advertising as RoadReader`.
- **Status lines:** about twice a second, a line like
  `[advertising] rows=0  123456,0.0123,-0.0045,0.9987,0.12,-0.30,0.05`.
  The **first number is a timestamp** (this firmware runs at 200 Hz).
- **Box lying flat:** the 4th number (`az`) is close to **1.0**, and the last three (the gyro) are close to **0**.
- **LED:** it blinks (advertising).

If you see `IMU initialization failed`, check the SPI wiring (CS = GPIO 5).

## 4. Install the app on the iPhone (10 min)

1. Run `open roadreader_app/RoadReader.xcodeproj`.
2. Select the **RoadReader** target, go to **Signing & Capabilities**, and choose your **Team** (sign in with your
   Apple ID if asked). If Xcode says the bundle identifier is taken, change it (e.g. add your initials).
3. Plug in the iPhone, pick it as the run destination, and press **Run** (▶).
   - The first time, approve the developer on the phone: *Settings → General → VPN & Device Management*.
   - On iOS 16+, also turn on *Settings → Privacy & Security → Developer Mode*.
4. On first launch, **allow** Bluetooth, Location ("While Using") and Local Network.

**Pass:** the app opens on "Looking for Road Reader device…", then connects by itself (the ESP32's LED turns
solid).

## 5. Start the servers and point the app at them (5 min)

```bash
./scripts/start_servers.sh
```
It prints a box with the exact values to type into the app (*gear icon → Settings*):

| Section | Setting |
|---|---|
| Forwarding | Host = *the IP it printed*, Port = 5005, Protocol = UDP, Stream to computer **on** |
| Road analysis | Vehicle = **Bike** (or Car), Live analysis **on**, Port = 8081 |
| Web database | Upload URL = `http://<IP>:8080/upload` (the script prints a different port if 8080 is taken) |

On macOS, click **Allow** if asked whether Python may accept incoming connections. Keep this terminal open:
Ctrl-C stops both servers.

## 6. Bench test with the box on the table (5 min)

Tap **Start Readings** and keep the box **completely still for 3 seconds**, which is when it calibrates.

| Check | Pass | If not |
|---|---|---|
| **Hz** stat in the app | **~200** | ~50 means the old firmware is still on the board: redo step 3. Steadily under ~150 means the Bluetooth link can't keep up. Tell the software team. |
| Live table | 6 numbers per row and no "Unparsed lines" | Note the raw line the app shows and send it to the software team. |
| Terminal | `rows=` climbs while you record | Check the host IP, the same Wi-Fi, and that Stream to computer is on. |
| **Road analysis** card | "Stand still… / Collecting data…" | "offline" means the phone can't reach port 8081: check the IP and the firewall prompt. |
| Shake/tap the box for ~15 s | The card switches to a condition and pothole/bump counts | On a table these numbers mean nothing. You're only checking that data flows. |

Tap **Stop Readings**.

## 7. Real ride (15–20 min)

1. **Mount the box rigidly** on the handlebar with tape or zip ties, no foam. Any orientation is fine; it
   calibrates itself. In a car, put it on the dashboard and set Vehicle = Car.
2. Pick a **5–10 min route** with open sky for GPS. Include a stretch of **smooth road**, and ideally **one
   pothole or speed bump you can find again**.
3. **Start Readings → stand still 3 s → ride.** Tap **Detect Bump** every time you hit something. Those taps
   are what we compare the detections against.
4. During the ride, the Road analysis card updates every ~2 s (as long as the phone can still reach the
   computer's Wi-Fi). Losing Wi-Fi is fine: everything is still saved on the phone.
5. **Stop Readings**. Open **Sessions** (document icon, top left) and tap **Upload** on the ride. Then tap the
   ride itself.

**Pass:**
- **Session list:** the row shows a summary like `Poor · IRI 5.2 · 2 potholes · 1.3 km`.
- **Ride screen:** a map of your route coloured by condition, with ▼ potholes and ▲ bumps.
- **Colours:** the smooth stretch is mostly green/yellow, and rough streets are orange/red.
- **Known bump:** the pothole or bump you know about shows up near where you tapped Detect Bump.

## 8. Send the data back

The most useful thing you can send is the raw ride CSV. With it, the software team can compare the real
signals to the simulation and tune the model to your bike and mount. Any of these work:
- **From the phone:** in *Sessions*, long-press a ride and choose **Share CSV** (AirDrop/Files).
- **On the computer:** files are written to:
  - `roadreader_app/listener/roadreader_<time>.csv` (the live stream),
  - `roadreader_app/listener/uploads/<device>/session_*.csv`, plus `*.analysis.json` and `*.report.png`.

Put them in the team drive and link them in [`data/README.md`](data/README.md). Small rides (< 10 MB) can also be
committed under `data/sample/`. With each file, note the vehicle and mount, the route, and any known bumps.

To re-run the analysis on any saved ride (it writes CSVs and a report PNG to `out/real/`):

```bash
cd software/roadimu && ../../.venv/bin/python -m roadimu reconstruct ../../roadreader_app/listener/roadreader_<time>.csv --vehicle bike --out out/real
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| App stuck on "Looking for Road Reader device…" | Serial Monitor should say `advertising`. In the app's Settings, the name prefix must be `RoadReader`. Restart the ESP32. |
| Uploads fail / "Server returned …" | Another app may already use port 8080 (WeChat, for example). Run `UPLOAD_PORT=9090 ./scripts/start_servers.sh` and use `:9090` in the Upload URL. |
| Card says "offline" but the terminal shows rows | Port 8081 is blocked: allow Python in *System Settings → Network → Firewall*, or use the phone's hotspot. |
| Note: "No GPS in this log" | Ride outdoors and wait for "GPS ±… m" in the app's status card before you start. |
| Note: "No clear standing-still period" | Stand still ~3 s after Start Readings. |
| Note: "Sample rate is 50 Hz" (bike) | The old firmware is still flashed. Redo step 3. |
| `setup.sh`: Python too old | Install Python 3.10+ (e.g. `brew install python`) and run `PYTHON=python3.12 ./scripts/setup.sh`. |

For more detail: [roadreader_app/README.md](roadreader_app/README.md) covers the app and servers, and
[software/roadimu/README.md](software/roadimu/README.md) covers how the analysis works and its accuracy.
