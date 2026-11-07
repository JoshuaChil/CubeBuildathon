"""IMU log -> road topography.

Pipeline (see pipeline.reconstruct):

1. preprocess  uniform time base, GPS fixes, button labels; mounting calibration
2. attitude    GPS-aided attitude filter -> pitch/roll, fused speed, distance, hills
3. profile     model-based Wiener deconvolution -> road surface profile under the tyre
4. roughness   IRI and RMS roughness per segment; pothole / bump detection
"""

from .pipeline import TopoConfig, TopoResult, reconstruct

__all__ = ["TopoConfig", "TopoResult", "reconstruct"]
