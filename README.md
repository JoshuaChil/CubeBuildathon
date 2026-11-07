# CubeBuildathon — Road Quality Measurer

> A low-cost IMU box that mounts on a bike (or car) and maps road damage as you ride.
> Built in 24 hours for the Cube Buildathon.

## The problem

NYC's road infrastructure is degrading faster than it is being repaired, and the money to fix it is limited.
Cities often don't have good, up-to-date data on **where** the worst damage is, or **which** repairs would help the most people.
Without that data, it's hard to argue for funding or to decide what to fix first.

## Our solution

A small box with an **ICM-42688-P IMU** and an **ESP32**, mounted on a bike or car. While you ride:

1. The IMU picks up vibration and sudden bumps from the road surface.
2. The ESP32 streams this data over Bluetooth to a **mobile app**, which tags each reading with **GPS (lat, lon)**.
3. The app uploads readings to a **database** in real time.
4. A **classifier** (trained on rides that we labeled by hand) flags potholes, cracks and rough patches.
5. A **map/dashboard** turns the data into a ranked repair list, so the city can see which roads are both rough *and* busy.

While collecting training data, a rider presses a **button** on the box each time they hit a bump. That label is stored with the IMU data, and the classifier learns from those labeled rides.

## How it works

```
 ┌──────────────── Hardware box ────────────────┐
 │  ICM-42688-P IMU ──SPI/I2C──► ESP32  ◄── Button (label "bump")
 └──────────────────────────────┬───────────────┘
                                │ BLE
                                ▼
                     ┌──── Mobile app ────┐
                     │  start/stop session │
                     │  + phone GPS        │
                     └─────────┬──────────┘
                               │ HTTPS
                               ▼
                ┌──── Backend / database ────┐
                │  sessions, readings, events │
                └──────┬───────────────┬─────┘
                       ▼               ▼
              Bump classifier    Map / priority dashboard
```

The full design is in [docs/architecture.md](docs/architecture.md). The data format that every subteam builds against is in [docs/data-schema.md](docs/data-schema.md).

## Try it

- **Test the full system with the hardware:** follow [TESTING.md](TESTING.md). It covers flashing the ESP32,
  installing the iPhone app, starting the analysis on a computer, a bench test and a real ride.
- **No hardware:** run `./scripts/setup.sh && ./scripts/demo.sh`. This simulates a ride and runs the whole
  computer side.

## Repo layout

| Folder | Owner | What lives here |
|---|---|---|
| [`hardware/`](hardware/) | Hardware | ESP32 firmware, wiring/pinout, enclosure and mount CAD, bill of materials |
| [`software/`](software/) | Software | IMU simulator + topography pipeline ([`roadimu`](software/roadimu/)), mobile app, backend/database, ML, dashboard |
| [`pitch/`](pitch/) | Pitch prep | Problem research, slide deck, demo script, judging Q&A |
| [`docs/`](docs/) | Everyone | Architecture, shared data schema, 24h timeline |
| [`data/`](data/) | Software + Hardware | Sample recordings and labeled ride logs (small files only) |

Each subteam folder has its own README with goals, a task checklist and open questions.

## Team

| Subteam | Members | Lead |
|---|---|---|
| Hardware | _TBD_ | _TBD_ |
| Software | _TBD_ | _TBD_ |
| Pitch prep | _TBD_ | _TBD_ |

## 24-hour plan (summary)

| Hours | Milestone |
|---|---|
| 0–2 | Lock the scope and the data schema; get the IMU reading on the ESP32 |
| 2–8 | BLE stream to the phone; app records sessions with GPS; database is up |
| 8–14 | Collect labeled rides; first classifier; enclosure printed and mounted |
| 14–20 | Map/dashboard; end-to-end demo works; pitch deck drafted |
| 20–24 | Polish, rehearse the demo, record a backup video, submit |

The detailed plan with checkpoints is in [docs/timeline.md](docs/timeline.md).

## Working in this repo

- Work on a branch per feature: `hw/<thing>`, `sw/<thing>`, `pitch/<thing>`.
- Open small PRs into `main` and get one teammate to look before merging.
- Never commit secrets (API keys, database URLs). Put them in a `.env` file, which is gitignored (copy [`.env.example`](.env.example)).
- Keep large raw data (videos, long ride logs) out of git. Share it in the team drive and link it from [`data/README.md`](data/README.md).
- If you change the data format, update [docs/data-schema.md](docs/data-schema.md) **first** and tell the other subteams.

## Links

- Team notes: [Google Doc](https://docs.google.com/document/d/1Rgl_Na47izucmJLSOjVHC3-dVTnzj8bKzssPqX0LksU/edit)
