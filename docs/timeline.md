# 24-hour timeline

Fill in owners as tasks are claimed. At each **checkpoint**, all leads do a 5-minute sync.

| Hour | Hardware | Software | Pitch prep |
|---|---|---|---|
| 0–2 | IMU reads on ESP32 over serial | Pick the app/backend stack; stub the DB | Problem research; pick the target city stats |
| **✅ Checkpoint 1 (h2)** | **Data schema locked** | | |
| 2–5 | BLE streaming + button flag | App connects over BLE, shows live values | Outline the deck; find funding/infra data |
| 5–8 | Battery power; enclosure CAD | App records sessions + GPS → DB | Draft problem + solution slides |
| **✅ Checkpoint 2 (h8)** | **A full ride is saved to the DB end-to-end** | | |
| 8–11 | Print/assemble enclosure; mount it | Threshold bump detector; label tooling | Draft impact + business slides |
| 11–14 | **Collect labeled rides** (with software) | Train the first classifier | Collect photos/video of the build |
| **✅ Checkpoint 3 (h14)** | **Classifier output appears on a map** | | |
| 14–18 | Hardening, cable management, spare unit | Dashboard + priority ranking | Full deck draft; demo script |
| 18–20 | Help with the demo setup | Bug fixes; seed demo data | First full rehearsal |
| **✅ Checkpoint 4 (h20)** | **Feature freeze** | | |
| 20–24 | Backup hardware ready | Record the backup demo video | Rehearse ×3; Q&A prep; submit |

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| BLE is flaky | Log to SD card / serial, then upload after the ride |
| Not enough labeled data | Use the threshold detector for the demo; show the classifier as "in progress" |
| Live demo fails | Pre-recorded ride + seeded dashboard data |
| GPS is inaccurate indoors | Use recorded outdoor sessions for the demo |
