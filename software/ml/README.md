# ML: bump & roughness detection

**Input:** labeled IMU windows (button presses mark bumps).
**Output:** a label for each window (`smooth` / `bump` / `rough`) plus a severity score.

Suggested layout:
```
ml/
├── notebooks/    Exploration and plots
├── src/          Feature extraction + training scripts
└── models/       Small exported models (keep them under a few MB)
```

Start with the threshold baseline so the demo always has detections. Then add the classifier on top.
