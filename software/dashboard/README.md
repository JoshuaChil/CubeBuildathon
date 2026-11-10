# Dashboard

Live telemetry page for the GroundTruth demo: four charts (accelerometer X, Y, Z and the bump filter output over
time) read from the Supabase `readings` table that the Road Reader iPhone app writes to, with a bump
signal and screen flash whenever a row arrives with `anomaly_detected = 1` (Detect Bump pressed on the phone)
**or** the live bump filter fires.

Plain HTML, CSS and JavaScript. No build step, no framework, no dependencies beyond Google Fonts.

## Live bump filter (`filter.js`)

Every incoming row is run through the method validated on the bench data
(`data analysis/bump_detector/hp_detect.py`, session 7):

1. `|a| = sqrt(ax² + ay² + az²)` — orientation does not matter.
2. Second-order Butterworth **high-pass at 3 Hz**, run sample by sample. Gravity, tilt and pedalling cadence are
   slower than 3 Hz and are removed; bumps are faster and pass.
3. A bump is any sample whose high-passed magnitude exceeds the **threshold** (default 0.8 g; on session 7 every hit
   exceeds 1.2 g and nothing else exceeds 0.77 g). One detection per second at most.

The threshold is editable in the controls strip while a session runs. **Adaptive (MAD)** replaces the fixed bar
with `median + 15 × 1.4826 × MAD` of the last 10 s of filter output, which follows the road on real rides. The
fourth chart shows the filter output with the threshold as a dashed line; filter detections are dashed brown ticks
on every chart, listed with the phone flags, and counted in their own tile. The sample rate is estimated from the
row timestamps and the filter re-designed if it differs from the assumed 50 Hz.

## Run

```
cd software/dashboard
python3 -m http.server 8090
```

Open http://localhost:8090. (Opening `index.html` directly from the file system also works in most browsers.)

Credentials are in `config.js`: the same Supabase URL and publishable key as
`groundtruth_app/RoadReader/Config/SupabaseConfig.swift`. The **Settings** button overrides them for one browser.

## Use

1. Run `groundtruth_app/supabase/schema.sql` in the Supabase SQL editor once (creates the `readings` table and the
   anon read policy the page needs). The connection pill in the header says "Connected" when it can read the table.
2. Click **Start Reading Session**. The page notes the current highest row id and from then on polls every
   500 ms for `id > watermark`, so only rows inserted *after* the click are collected and drawn.
3. On the phone, turn on **Live Broadcasting** and press **Start Live Session**. Rows appear within a second or so.
   Each chart autoscales to the data in the rolling window (10, 20 or 60 s) and shows a crosshair + tooltip on hover.
   The charts redraw every animation frame and the time axis follows the clock, held slightly behind the newest
   batch (a little more than the interval between batches), so the line scrolls smoothly instead of stepping
   forward each time a batch of rows lands. The hold adapts: if the phone uploads every 5 s the display runs
   about 6 s behind; if it uploads twice a second the display runs under a second behind.
4. Press **Detect Bump** on the phone: the signal tile turns earth brown, the screen flashes, the row range is shaded
   on all three charts, and the event is listed with its GPS position. Tick **Sound on bump** for a short tone.
5. **Stop** freezes the view. Start again to begin a fresh watermark.

## Design

Palette A, "Forest + Stone": Deep green `#151B1A`, Moss `#80957B` (data line, primary button), Earth brown
`#897560` (bump observations, alerts), Stone gray `#A7ABA4` (grid, muted text), Chalk `#F0F1EA` (page).
Inter for text, JetBrains Mono for eyebrows, numbers and labels. Each chart draws one series, so there is no
legend box; a single key under the charts names the line and the bump band.
