# How Road Reader turns vibration into a road map

This document explains, in plain language, how the processing algorithm turns a ride recording into a map of road conditions. A short technical section at the end points to the code for anyone who wants the details.

## The problem in one sentence

The box on the handlebar can't see the road. It only feels the bike shaking. The software works backwards from the shaking to figure out what the road must have looked like.

## What goes in, what comes out

**In:** a ride recording from the box and the phone:
- the accelerometer (how hard the box is being pushed, in each direction)
- the gyroscope (how fast the box is rotating)
- the phone's GPS (where you are and how fast you're going)
- optional button presses from the rider

**Out:**
- a map of the ride coloured by road condition (good / fair / poor / very poor, every 20 m)
- pins for potholes, bumps, sunken patches and speed humps, each with a severity. They're found from the box's shaking alone; GPS only says where to put the pin
- the shape of the road surface, accurate to a few millimetres, every 5 cm along the ride
- a rough height profile of the hills

## The five steps

```
recording ─► 1. tidy up ─► 2. how is the box mounted? ─► 3. tilt, speed, hills ─► 4. shaking → road shape ─► 5. grade + find potholes
```

### 1. Tidy up the recording

The box sends its readings to the phone over Bluetooth. Some readings get lost, and the phone receives them in bunches rather than one at a time.

The software:
- lines the readings back up at a steady beat
- marks where pieces are missing, so later steps know not to trust those moments
- picks out the real GPS updates (the phone only gets a new position about once a second)
- throws away GPS positions that are too inaccurate (worse than 30 m)

### 2. Work out how the box is mounted

The box can be strapped on at any angle, even sideways. The software figures out which way it's facing by itself.

- **Which way is down:** at the start of the ride you stand still for a couple of seconds. The only thing the box feels then is gravity, so the software learns which direction is "down" for the box. It also learns the gyroscope's small built-in error so it can subtract it later.
- **Which way is forward:** once you start riding, the software checks which direction the box feels a push whenever the GPS says you're speeding up or slowing down. That direction is "forward".

This is why every ride should start with about 2 seconds standing still on level ground.

### 3. Track the bike's tilt, speed and hills

At every moment, the software keeps track of how the bike is tilted.

- **Tilt:** the gyroscope is good at tracking quick changes in tilt but slowly drifts. Gravity gives a steady reference but is noisy. The software combines the two, trusting the gyroscope from moment to moment and gravity over the long run.
- **Not getting fooled:** braking feels a lot like pointing downhill, and leaning into a turn feels like the road is tilted. GPS tells the software when you're braking, speeding up or turning, so it subtracts those effects before using gravity as a reference.
- **A subtle correction:** on a rigid bike, the constant shaking itself creates a tiny false tilt, enough to invent about 3 m of fake hill per kilometre. The software measures this effect and removes it.
- **Speed and distance:** GPS speed is combined with the box's own sense of speeding up and slowing down, which gives a smoother speed than GPS alone. Adding up speed over time gives the distance travelled.
- **Hills:** if the bike points slightly nose-up over a long stretch, you're going uphill. Adding that up over distance gives the hills.

After this step, the software has the pure up-and-down shaking caused by the road, with gravity, braking and turning taken out.

### 4. Turn shaking back into road shape (the core of the algorithm)

This is the clever part.

A bike reacts to a bump in a predictable way:
1. The front wheel hits the bump.
2. The frame and rider bounce.
3. A moment later, the rear wheel hits the same bump, and the bike bounces again.

The software has a model of that reaction (how springy the tyres are, how heavy the rider is, the distance between the wheels, and so on). Because it knows how the bike responds to a bump, it can run that in reverse: *"this shaking pattern means there was a bump about this big, right here."*

A few things make this work well:

- **Two clues instead of one.** Besides the up-and-down shaking, the software uses how the bike rocks forward and back. A front-wheel hit and a rear-wheel hit rock the bike in opposite directions, which helps tell them apart.
- **Not trusting every wiggle.** Some of the shaking is just noise: the rider shifting their weight, pedalling, or tiny sensor errors. The software knows roughly what a typical road looks like and how noisy the sensors are. Where a signal is more likely to be noise than road, it leaves it out instead of inventing a bump.
- **Allowing for a different bike.** The model is a typical bike, not your exact bike. The software assumes the real bike could be about 30% different and doesn't lean too hard on the model's details.
- **Short chunks of ride.** It works on overlapping 4-second chunks, because over 4 seconds your speed barely changes, and speed decides how quickly bumps arrive.
- **Missing data is left blank.** Where Bluetooth lost readings, the software fills the gap with "nothing happened". Guessing a smooth line across a gap in violent shaking would create fake potholes.

The result is the shape of the road surface under the front wheel, laid out every 5 cm along the ride.

**Why not just add up the shaking?** The obvious approach is to take the box's up-and-down movement as the road's up-and-down shape. That ignores the bike's suspension and bounce. It works okay on a stiff bike but fails badly on a car, where the body floats on springs. The software computes this simple version too, as a comparison, and the model-based method beats it on every test road.

### 5. Grade the road and find potholes

**Road condition score.** Every 20 m stretch gets an International Roughness Index (IRI). City road departments use this standard score: it imagines a standard car driving over the road at 80 km/h and measures how much its suspension moves. The score is labelled:

| IRI (m/km) | Label |
|---|---|
| under 2 | good |
| 2–4 | fair |
| 4–6 | poor |
| 6 and up | very poor |

The software also reports how rough the surface is at short distances (under 5 m), which is closer to what a cyclist actually feels.

**Finding potholes and bumps, without GPS.** Whether you rode over a pothole is decided from the box alone. GPS is only used afterwards to put the pin on the map, so a bad GPS fix (tall buildings, tunnels, no signal) can move a pin but can't add, remove or resize a pothole. To do this, the box works out by itself:
- **which way is up**, from gravity (the slow part of the accelerometer signal), so it doesn't need the mounting calibration;
- **which way the bike pitches**, from the axis the gyro wobbles around most when you hit bumps;
- **how fast you're going**, from the echo: the rear wheel hits every bump a moment after the front wheel. That delay is the wheelbase divided by your speed. On the simulated rides this speed is within about 0.1–0.3 m/s of the truth.

With those, it rebuilds the road shape under the tyre the same way as step 4, then looks for spots that dip or rise noticeably compared with the road around them. It looks at two sizes:
- **Short features** (10 cm to 2 m long): potholes, steel plates, small bumps.
- **Long features** (1 m to 8 m long): sunken patches, speed humps, raised crosswalks.

To count, a dip or rise must be at least 20 mm (about an inch), and stand out 5 times more than the usual roughness around it (4 times for a car). On roads that are rough everywhere, like cobblestones, that raises the bar, so every cobblestone doesn't become a "pothole".

**Cleaning up the list.** The same pothole is often found more than once, so only the strongest detection is kept. A sharp pothole can also leave a small fake "bump" right next to it, an echo of the reverse calculation, so those are removed too.

Each pothole or bump gets:
- a GPS location (looked up from the time it happened)
- the time it happened in the ride
- a severity from 0 to 1 (an 8 cm or deeper defect counts as 1)

## When things are missing

- **No GPS:** potholes and bumps are found exactly as with GPS, but they can't go on the map. For the road shape and roughness, the software guesses when you were moving from how much the box is shaking and assumes a speed of 5 m/s, so distances are approximate, and the result carries a note saying so.
- **No standing start:** the software still runs but warns that the mounting calibration may be off.
- **Slow sample rate:** a bike frame vibrates 28–40 times a second, so the box needs to take readings at least about 120 times a second (200 is the target). Below that, the software warns that the bike's road profile is unreliable.

## How well it works

These results come from simulated rides, where the true road is known and the software's answer can be checked against it.

- **Road roughness: very reliable.** The reconstructed road shape matches the real one closely on every type of road tested (smooth and worn asphalt, potholes, cobblestones, gravel, speed bumps, concrete slabs, a mixed New York City route), for both bikes and cars. The roughness score is usually within 1–1.5 m/km of the truth.
- **Potholes: most are found, with few false alarms.** On a heavily potholed street, it averaged about one false alarm every 800 m on a bike. It tends to report potholes as **shallower** than they really are (about 60–80% of the true depth), partly because a bike wheel skips over short, deep holes. Use depth to rank how bad potholes are, not as an exact measurement.
- **Hills: only approximate on a bike.** Expect about 0.5–1 m of error per kilometre on straight routes, and up to about 3 m with lots of turns, because leaning confuses the tilt estimate. Cars do better. Logging the phone's barometer would improve this.

## Limits

- It assumes a typical bike. A real bike would benefit from a quick calibration ride over a known bump.
- It assumes the box is mounted rigidly. A rubber mount would change the readings in ways the model doesn't know about.
- Not modelled: frame flex, tyre pressure changing during a ride, the rider standing on the pedals, and wet roads.

## For developers: where each step lives

All paths are relative to `software/roadimu/roadimu/`.

| Step | Code | Main technique |
|---|---|---|
| Read the app's CSV, rebuild timing, detect lost samples | `appio.py` | Minimum-filter on arrival delay to spot dropped samples |
| 1. Tidy up | `topo/preprocess.py` (`prepare`, `gps_speed`) | Uniform resampling, gap flags, smoothed GPS speed and dv/dt |
| 2. Mount calibration | `topo/preprocess.py` (`find_still`, `calibrate`) | Gravity vector at standstill → tilt; correlation with GPS dv/dt → yaw |
| 3. Tilt, speed, hills | `topo/attitude.py` (`estimate`, `elevation_from_pitch`) | Mahony complementary filter with GPS-known acceleration removed; vibration-rectification correction |
| 4. Road shape | `topo/profile.py` (`wiener_profile`) | Two-channel (acceleration + pitch rate) Wiener deconvolution in 4 s windows, ISO 8608 road prior, vehicle model from `vehicle.py` |
| 5. Grading | `topo/roughness.py` (`segment_stats`) | Golden-car IRI per 20 m |
| Potholes and bumps (no GPS) | `topo/impacts.py` (`detect`), `topo/roughness.py` (`detect_events`) | Gravity from the accelerometer; pitch axis from gyro PCA, sign by model fit; speed from the front-rear wheel delay; Wiener deconvolution; running-median residual with an adaptive threshold at 3 m and 15 m scales |
| Whole pipeline and outputs | `topo/pipeline.py` (`reconstruct`, `TopoConfig`) | Ties the steps together; all tuning settings live in `TopoConfig` |

The full simulator, scoring and results table are in [`software/roadimu/README.md`](../software/roadimu/README.md).
