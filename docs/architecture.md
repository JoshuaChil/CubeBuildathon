# System architecture

## Components

| Component | Tech (proposed) | Responsibility |
|---|---|---|
| Sensor box | ESP32 + ICM-42688-P IMU + push button + battery | Sample the accelerometer and gyro, mark button presses, stream over BLE |
| Mobile app | _TBD_ (e.g. React Native/Expo or Flutter) | Pair with the box, start/stop a session, add phone GPS, upload to the backend |
| Backend + DB | _TBD_ (e.g. Supabase/Firebase or FastAPI + Postgres) | Store sessions, readings and detected events; serve them to the dashboard |
| Classifier | Python (scikit-learn to start) | Label windows of IMU data as `smooth` / `bump` / `rough` |
| Dashboard | Web map (e.g. Leaflet/Mapbox) | Show road quality on a map and rank segments for repair |

## Data flow

1. **Box → phone (BLE).** The ESP32 samples the IMU at ~100–200 Hz and sends packets over a BLE notify characteristic. Button presses are sent as `label` events with the same timestamp clock.
2. **Phone adds location.** The app stamps each packet (or each window) with phone GPS. The parts list has no GPS module, so **the phone is the GPS source** (see open questions).
3. **Phone → backend.** The app batches readings and uploads them every few seconds. While offline, it buffers them locally.
4. **Detection.** First a simple threshold (vertical acceleration spike) runs; later, the trained classifier replaces it. Detection can run on the backend or in the app.
5. **Dashboard.** It groups events by road segment and computes a **priority score** = roughness × road busyness. This is the "to-do list for the government" from our notes.

## Interfaces between subteams

These are the contracts that let everyone work in parallel. They are defined in [data-schema.md](data-schema.md).

- **Hardware ↔ Software:** BLE service/characteristic UUIDs and the packet format.
- **App ↔ Backend:** the session and reading JSON shape.
- **Backend ↔ ML/Dashboard:** the tables (`sessions`, `readings`, `events`).
- **Software ↔ Pitch:** demo flow and which screenshots/numbers the deck needs.

## Open questions

- [ ] GPS: use phone GPS (simplest), or add a GPS module to the box?
- [ ] Does detection run on the ESP32, the phone or the backend? (Backend is the easiest place to iterate.)
- [ ] Mounting: handlebar, seat post or frame? The choice changes the vibration signature, so we should pick one and stick with it for all training data.
- [ ] Mentor feedback (Derek): focus on **road applications, including cars**. Do we demo on a bike, a car or both?
- [ ] Where do "road busyness" numbers come from? (e.g. NYC Open Data traffic counts)
