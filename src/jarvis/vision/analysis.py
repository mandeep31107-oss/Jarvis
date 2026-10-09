"""Frame analysis - brightness, colour, motion.

Everything here works on the decoded pixels from :mod:`jarvis.vision.png` using
only the standard library. There is no NumPy and no OpenCV in the runtime, so the
implementations are plain Python loops. That is slower than it needs to be, and
it is honest: for screen-activity detection and presence detection on a modest
frame it is fast enough, and it keeps the zero-dependency core intact.

The measurements are deliberately simple and explainable, because they feed
permission decisions. "Mean brightness 0.71, motion 0.03" can be audited; a
neural-net confidence score cannot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.vision.png import Image

__all__ = [
    "FrameStats",
    "MotionReport",
    "difference",
    "dominant_colour",
    "frame_stats",
    "has_motion",
    "histogram",
    "luma",
]


def luma(r: int, g: int, b: int) -> float:
    """Perceived brightness, 0.0-1.0, using the Rec. 601 luma weights.

    A plain average of the channels would call a saturated blue as bright as a
    mid grey, which makes "is the screen dark" answers wrong.
    """
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _rgb(image: Image, index: int) -> tuple[int, int, int]:
    """Read RGB at a pixel offset, whatever the channel count.

    Indexing ``data[i+2]`` directly assumes three or more channels, which throws
    IndexError on a 1-channel greyscale image and reads past the end when two
    frames being compared disagree. Going through here makes both impossible.
    """
    channels = image.channels
    if channels >= 3:
        return image.data[index], image.data[index + 1], image.data[index + 2]
    if channels == 2:  # greyscale + alpha
        grey = image.data[index]
        return grey, grey, grey
    grey = image.data[index]
    return grey, grey, grey


@dataclass(slots=True)
class FrameStats:
    """A description of one frame.

    Attributes
    ----------
    brightness:
        Mean luma, 0.0-1.0.
    darkest / brightest:
        The extremes, so a mostly-dark frame with one bright dialog is not
        mistaken for a bright frame.
    dominant_colour:
        The most common RGB triple and the share of pixels it covers. A desktop
        that is mostly one colour is usually idle.
    histogram:
        8-bin luma histogram, normalised so the bins sum to 1.0.
    edge_density:
        Fraction of pixels whose luma differs sharply from their right-hand
        neighbour - a crude proxy for "there is text or detail here".
    """

    width: int = 0
    height: int = 0
    brightness: float = 0.0
    darkest: float = 0.0
    brightest: float = 0.0
    dominant_colour: tuple[int, int, int] = (0, 0, 0)
    dominant_share: float = 0.0
    histogram: list[float] = field(default_factory=list)
    edge_density: float = 0.0
    sampled: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "brightness": round(self.brightness, 4),
            "darkest": round(self.darkest, 4),
            "brightest": round(self.brightest, 4),
            "dominant_colour": list(self.dominant_colour),
            "dominant_share": round(self.dominant_share, 4),
            "histogram": [round(v, 4) for v in self.histogram],
            "edge_density": round(self.edge_density, 4),
            "sampled": self.sampled,
        }


@dataclass(slots=True)
class MotionReport:
    """How much changed between two frames.

    Attributes
    ----------
    score:
        Mean absolute luma difference, 0.0-1.0.
    changed_share:
        Fraction of compared pixels that moved more than ``threshold``.
    centre:
        The mean (x, y) of the changed pixels, normalised to 0.0-1.0, so a
        consumer can say *where* the activity was without being given pixels.
    """

    score: float = 0.0
    changed_share: float = 0.0
    centre: tuple[float, float] = (0.0, 0.0)
    compared: int = 0
    threshold: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "changed_share": round(self.changed_share, 4),
            "centre": [round(v, 4) for v in self.centre],
            "compared": self.compared,
            "threshold": self.threshold,
        }


def frame_stats(image: Image, *, step: int = 1) -> FrameStats:
    """Describe a frame.

    ``step`` samples every Nth pixel in each axis. Sampling is explicit rather
    than automatic, because a caller making a decision deserves to know how much
    of the frame was actually looked at; the count is returned in ``sampled``.
    """
    step = max(1, int(step))
    if image.width <= 0 or image.height <= 0:
        return FrameStats()

    counts: dict[tuple[int, int, int], int] = {}
    buckets = [0] * 8
    total = 0
    luma_sum = 0.0
    darkest = 1.0
    brightest = 0.0
    edge_hits = 0
    edge_pairs = 0

    for y in range(0, image.height, step):
        row_start = y * image.width * image.channels
        for x in range(0, image.width, step):
            start = row_start + x * image.channels
            r, g, b = _rgb(image, start)
            value = luma(r, g, b)
            luma_sum += value
            total += 1
            darkest = min(darkest, value)
            brightest = max(brightest, value)
            buckets[min(7, int(value * 8))] += 1
            counts[(r, g, b)] = counts.get((r, g, b), 0) + 1

            # Compare with the neighbour `step` pixels to the right.
            nx = x + step
            if nx < image.width:
                nstart = row_start + nx * image.channels
                edge_pairs += 1
                if abs(value - luma(*_rgb(image, nstart))) > 0.2:
                    edge_hits += 1

    dominant = max(counts.items(), key=lambda kv: kv[1])
    return FrameStats(
        width=image.width,
        height=image.height,
        brightness=luma_sum / total if total else 0.0,
        darkest=darkest,
        brightest=brightest,
        dominant_colour=dominant[0],
        dominant_share=dominant[1] / total if total else 0.0,
        histogram=[n / total for n in buckets] if total else [],
        edge_density=edge_hits / edge_pairs if edge_pairs else 0.0,
        sampled=total,
    )


def histogram(image: Image, bins: int = 8, *, step: int = 1) -> list[float]:
    """A normalised luma histogram."""
    bins = max(1, min(64, int(bins)))
    step = max(1, int(step))
    counts = [0] * bins
    total = 0
    for y in range(0, image.height, step):
        row_start = y * image.width * image.channels
        for x in range(0, image.width, step):
            start = row_start + x * image.channels
            counts[min(bins - 1, int(luma(*_rgb(image, start)) * bins))] += 1
            total += 1
    return [n / total for n in counts] if total else []


def dominant_colour(image: Image, *, step: int = 1) -> tuple[tuple[int, int, int], float]:
    """The most common colour and the share of sampled pixels it covers."""
    stats = frame_stats(image, step=step)
    return stats.dominant_colour, stats.dominant_share


def difference(
    first: Image, second: Image, *, threshold: float = 0.08, step: int = 1
) -> MotionReport:
    """Compare two frames and report how much moved.

    Frames must be the same size; mismatched frames are a caller error, not a
    reason to silently compare the overlapping corner.
    """
    if (first.width, first.height) != (second.width, second.height):
        raise ValueError(
            f"cannot compare frames of different sizes: "
            f"{first.width}x{first.height} vs {second.width}x{second.height}"
        )
    #: Channel counts may legitimately differ (an RGB frame against an RGBA one);
    #: what must not happen is reading one frame with the other's stride.
    step = max(1, int(step))
    total = 0
    moved = 0
    sum_delta = 0.0
    cx = 0.0
    cy = 0.0
    for y in range(0, first.height, step):
        first_row = y * first.width * first.channels
        second_row = y * second.width * second.channels
        for x in range(0, first.width, step):
            a = luma(*_rgb(first, first_row + x * first.channels))
            b = luma(*_rgb(second, second_row + x * second.channels))
            delta = abs(a - b)
            total += 1
            sum_delta += delta
            if delta > threshold:
                moved += 1
                cx += x
                cy += y
    if total == 0:
        return MotionReport(threshold=threshold)
    return MotionReport(
        score=sum_delta / total,
        changed_share=moved / total,
        centre=(cx / moved / max(1, first.width - 1), cy / moved / max(1, first.height - 1))
        if moved
        else (0.0, 0.0),
        compared=total,
        threshold=threshold,
    )


def has_motion(first: Image, second: Image, *, threshold: float = 0.08, step: int = 1) -> bool:
    """True when something on screen changed between two frames."""
    return difference(first, second, threshold=threshold, step=step).changed_share > 0.01
