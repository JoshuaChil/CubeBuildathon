"""roadimu - simulate bike/car IMU data over real road surfaces and turn IMU data back into road topography.

Modules
-------
road       ground-truth road surfaces (ISO 8608 roughness + potholes, cracks, bumps, textures)
vehicle    bike and car vertical-dynamics models (with tyre lift-off)
sensors    ICM-42688-P IMU, phone GPS and rider label-button models
scenarios  ready-made road conditions
simulate   runs a scenario end-to-end and writes CSV in the docs/data-schema.md format
topo       signal processing: CSV -> calibrated motion -> road profile + elevation + roughness + events
evaluate   compares reconstructions against simulator ground truth
"""

G = 9.80665  # standard gravity, m/s^2

__all__ = ["G"]
__version__ = "0.1.0"
