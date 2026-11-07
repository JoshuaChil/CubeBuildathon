# Shared data schema

> **This is the contract between subteams.** If you change it, update this file first and tell the other subteams.
> Status: **DRAFT**. Lock it by hour 2.

## 1. BLE (ESP32 → phone)

| Item | Value |
|---|---|
| Device name | `RoadIMU-XX` |
| Service UUID | _TBD_ |
| IMU data characteristic (notify) | _TBD_ |
| Control characteristic (write: start/stop, sample rate) | _TBD_ |

**IMU packet** (one sample, little-endian; several samples can be batched per notification):

| Field | Type | Unit | Notes |
|---|---|---|---|
| `t_ms` | uint32 | ms | Milliseconds since the box booted |
| `ax, ay, az` | int16 ×3 | raw (scale by accel range) | Accelerometer |
| `gx, gy, gz` | int16 ×3 | raw (scale by gyro range) | Gyroscope |
| `flags` | uint8 | — | bit0 = button pressed (bump label) |

Decide and write down here: the accel range (e.g. ±16 g), the gyro range and the sample rate.

## 2. Session + readings (phone → backend)

```json
{
  "session_id": "uuid",
  "device_id": "RoadIMU-01",
  "vehicle": "bike",            // "bike" | "car"
  "mount": "handlebar",
  "started_at": "2026-11-07T18:00:00Z",
  "readings": [
    {
      "t": "2026-11-07T18:00:01.250Z",
      "lat": 40.7128, "lon": -74.0060, "speed_mps": 4.2,
      "ax": 0.02, "ay": -0.01, "az": 1.03,   // in g
      "gx": 0.5,  "gy": -0.2, "gz": 0.1,     // in deg/s
      "label": null                          // "bump" when the button was pressed
    }
  ]
}
```

## 3. Database tables

| Table | Key columns |
|---|---|
| `sessions` | `id`, `device_id`, `vehicle`, `mount`, `started_at`, `ended_at`, `notes` |
| `readings` | `session_id`, `t`, `lat`, `lon`, `speed_mps`, `ax..gz`, `label` |
| `events` | `id`, `session_id`, `t`, `lat`, `lon`, `type` (`bump`/`rough`/`pothole`), `severity` (0–1), `source` (`button`/`threshold`/`model`) |

## 4. Training data CSV (for ML)

`data/labeled/<session_id>.csv` has these columns:

```
t_ms,lat,lon,speed_mps,ax,ay,az,gx,gy,gz,label
```
