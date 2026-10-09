"""Vision - decoding, measuring, and deciding what to do with an image.

Deliberately dependency-free. The PNG codec and the frame analysis are pure
Python on top of the standard library, which is what makes them testable in an
environment with no camera, no display, and no NumPy.
"""

from jarvis.vision.analysis import (
    FrameStats,
    MotionReport,
    difference,
    dominant_colour,
    frame_stats,
    has_motion,
    histogram,
    luma,
)
from jarvis.vision.png import Image, PngError, make_image, read_png, write_png

__all__ = [
    "FrameStats",
    "Image",
    "MotionReport",
    "PngError",
    "difference",
    "dominant_colour",
    "frame_stats",
    "has_motion",
    "histogram",
    "luma",
    "make_image",
    "read_png",
    "write_png",
]
