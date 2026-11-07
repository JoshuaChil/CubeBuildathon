# Software

**Goal:** get data from the box to a map that shows where roads need repair.

## Folder layout

```
software/
├── mobile-app/   Phone app: BLE connection, sessions, GPS, upload
├── backend/      Database + API (sessions, readings, events)
├── ml/           Bump/roughness classifier: notebooks, training scripts, models
└── dashboard/    Web map + repair priority list
```

## Task checklist

**Mobile app** ([mobile-app/](mobile-app/))
- [ ] Choose the framework (it must support BLE on the phones we have)
- [ ] Scan, connect to `RoadIMU-XX` and subscribe to IMU notifications
- [ ] Start/stop a session; show live accel values
- [ ] Tag readings with phone GPS + speed
- [ ] Upload in batches; buffer while offline

**Backend** ([backend/](backend/))
- [ ] Choose the stack and create the tables from [docs/data-schema.md](../docs/data-schema.md)
- [ ] Endpoints/SDK calls: create a session, append readings, list events
- [ ] Keep secrets in `.env` (copy [`.env.example`](../.env.example))

**ML** ([ml/](ml/))
- [ ] Export labeled sessions to CSV → `data/labeled/`
- [ ] Baseline: a threshold on vertical-acceleration spikes
- [ ] Features per window (RMS, peak, std, frequency bands) → simple classifier
- [ ] Evaluate on a held-out ride; report precision/recall for the pitch

**Dashboard** ([dashboard/](dashboard/))
- [ ] Map of readings colored by roughness
- [ ] Event markers (bumps/potholes)
- [ ] Priority ranking: roughness × road busyness

## Running locally

_Each subfolder should add its own setup and run steps._
