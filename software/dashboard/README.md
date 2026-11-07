# Dashboard

Live telemetry page for the GroundTruth demo: three charts (accelerometer X, Y, Z over time) fed straight from
the Supabase `readings` table that the Road Reader iPhone app writes to, with a bump signal and screen flash
whenever a row arrives with `anomaly_detected = 1` (Detect Bump pressed on the phone).

Plain HTML, CSS and JavaScript. No build step, no framework, no dependencies beyond Google Fonts.

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
4. Press **Detect Bump** on the phone: the signal tile turns earth brown, the screen flashes, the row range is shaded
   on all three charts, and the event is listed with its GPS position. Tick **Sound on bump** for a short tone.
5. **Stop** freezes the view. Start again to begin a fresh watermark.

## Design

Palette A, "Forest + Stone": Deep green `#151B1A`, Moss `#80957B` (data line, primary button), Earth brown
`#897560` (bump observations, alerts), Stone gray `#A7ABA4` (grid, muted text), Chalk `#F0F1EA` (page).
Inter for text, JetBrains Mono for eyebrows, numbers and labels. Each chart draws one series, so there is no
legend box; a single key under the charts names the line and the bump band.
