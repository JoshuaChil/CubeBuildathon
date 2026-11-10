# Bump detector

Adaptive, real-time-capable bump detection from the IMU alone. It separates a bump from
a bike that is merely moving by comparing each sample with the roughness of the surface
over the last few seconds, instead of using a fixed g threshold.

See `plots/bump_detector_diagram.png` for the stages drawn on real session 10 data.

## Run it

```
python3 bump_detector/detect.py sessions/2026-11-07/session_10.csv                 # events to stdout
python3 bump_detector/detect.py sessions/2026-11-07/session_10.csv --out ev.csv    # events to CSV
python3 bump_detector/detect.py sessions/2026-11-07/session_10.csv --plot p.png    # stage plot
python3 bump_detector/detect.py sessions/2026-11-07/session_12.csv --no-motion-gate # stationary tap tests
```

Input is the CSV written by `export_supabase.py`. Output has one row per event:
time into the session, timestamp, peak z-score, peak |a| deviation in g, peak gyro z-score,
transient width, GPS fix, and whether the app's manual flag fired within one second.

## Stages

| # | Stage | Why |
|---|---|---|
| 1 | Acceleration magnitude, the norm of ax, ay, az | Mounting orientation does not matter. The bumps in our data show up on X and Y, not Z. |
| 2 | Causal high-pass, 2nd-order Butterworth, 3 Hz | Removes gravity, leaning and pedalling cadence. Bumps are short, so they pass. |
| 3 | Running noise level: MAD of the high-passed signal over the last 3 s, times 1.4826 | This is the roughness of the surface right now. MAD ignores the spikes themselves. |
| 4 | z-score = high-passed / noise | "How unusual is this sample for this surface?" Rough pavement raises the bar; smooth pavement lowers it. |
| 5 | Gates | Moving: RMS vibration over 2 s above 0.03 g, so handling the device never fires it. Gyro: the gyro z-score must also spike, because a bump pitches the frame. |
| 6 | Trigger | z above k (default 10), transient shorter than 0.3 s, then a 1 s refractory pause so one bump is one event. Only arms after 3 s of history. |

Everything is causal. `BumpDetector.update()` takes one sample at a time, so the same
class can run on the phone as readings arrive.

## Tuning

- `--k` is the main knob. 8 is sensitive, 12 is conservative. On the session 10 ride,
  k=10 gives about one event per minute.
- The flagged taps in sessions 12 and 13 were done on a hand-held device with heavy
  rotation. They are only 0.5 to 0.7 g and sit near z of 6 to 8, so they need `--k 6
  --no-gyro-gate --no-motion-gate` to show up. Real road bumps on sessions 10 and 11 reach
  z of 15 to 55.

## What is not validated yet

The rides have no reliable ground truth. The app's flag is a button press with about
0.4 s of reaction jitter, and sessions 5 and 6 were flagged with the sensor at rest.
Events the detector reports on sessions 10 and 14 may be real bumps or false alarms.
To score it, ride with the button pressed on every bump and match presses to events with
a one-second window.

## Simpler alternative: `simple_detect.py`

Magnitude only, no filtering, no gates. The threshold is the median plus k times
1.4826 times the MAD of |a| over the previous 10 seconds, and a bump is any reading
above it (crossings within 1 s merge into one event).

```
python3 bump_detector/simple_detect.py sessions/2026-11-07/session_10.csv --k 8 --plot p.png
```

It catches the hand-held tap tests that the full detector misses, but fires three to
four times more often on real rides because ordinary road texture crosses the bar.

## Bench method: `hp_detect.py` (also live in the dashboard)

Magnitude, a 3 Hz second-order Butterworth high-pass, and a fixed threshold on the result.
Validated on session 7's nine bench hits: every hit exceeds 1.2 g after the high-pass and
nothing else exceeds 0.77 g, so the default 0.8 g threshold scores 9 of 9 with no false
detections. The same filter runs live in `software/dashboard/filter.js`.

```
python3 bump_detector/hp_detect.py sessions/2026-11-07/session_07.csv
```

On real rides ordinary road vibration reaches about 1 g after the high-pass, so the fixed
bar fires often there; `detect.py` is the ride detector.
