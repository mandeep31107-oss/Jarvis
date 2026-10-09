"""PCM audio: reading, writing, and measuring it.

Standard library only. ``audioop`` would be the obvious tool but it is deprecated
and scheduled for removal in Python 3.13, and this project turns
``DeprecationWarning`` into an error - so the handful of operations actually
needed (RMS, zero crossings, slicing) are done directly on the samples. They are
a dozen lines and they will still work on the next interpreter.

What this module deliberately does *not* do is recognise words. Transcription
needs a model, and a provider that is not configured is reported as such rather
than approximated.
"""

from __future__ import annotations

import array
import math
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "AudioError",
    "PcmAudio",
    "read_wav",
    "write_wav",
    "silence",
    "tone",
    "envelope",
]


class AudioError(ValueError):
    """The audio is not something this module can handle, and why."""


@dataclass(slots=True)
class PcmAudio:
    """Signed 16-bit little-endian PCM, the format ``wave`` handles natively.

    Attributes
    ----------
    sample_rate:
        Samples per second, per channel.
    channels:
        1 for mono, 2 for stereo.
    samples:
        Interleaved sample values as an ``array('h')``.
    """

    sample_rate: int
    channels: int
    samples: array.array

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise AudioError(f"sample rate must be positive, not {self.sample_rate}")
        if self.channels not in (1, 2):
            raise AudioError(f"expected mono or stereo, not {self.channels} channel(s)")
        if self.samples.typecode != "h":
            raise AudioError(
                f"expected 16-bit samples (array typecode 'h'), got {self.samples.typecode!r}"
            )

    # --- shape ---------------------------------------------------------------
    @property
    def frames(self) -> int:
        """Number of time steps - sample count divided by the channel count."""

        return len(self.samples) // self.channels

    @property
    def duration(self) -> float:
        """Length in seconds."""

        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def __len__(self) -> int:
        return self.frames

    def raw(self) -> bytes:
        """Little-endian PCM bytes, suitable for a WAV data chunk."""

        return self.samples.tobytes()

    def slice(self, start: float, end: float) -> PcmAudio:
        """A time range, in seconds. Clamped to the available audio."""

        first = max(0, min(self.frames, int(start * self.sample_rate)))
        last = max(first, min(self.frames, int(end * self.sample_rate)))
        return PcmAudio(
            self.sample_rate,
            self.channels,
            array.array("h", self.samples[first * self.channels : last * self.channels]),
        )

    def peak(self) -> int:
        """Largest absolute sample value, 0-32768."""

        if not self.samples:
            return 0
        return max(abs(min(self.samples)), max(self.samples))

    def rms(self) -> float:
        """Root mean square amplitude, normalised to 0.0-1.0.

        RMS rather than peak, because a single click has a high peak and no
        energy, and would otherwise read as loud speech.
        """
        if not self.samples:
            return 0.0
        total = 0
        for value in self.samples:
            total += value * value
        return math.sqrt(total / len(self.samples)) / 32768.0

    def zero_crossing_rate(self) -> float:
        """Sign changes per second.

        Voiced speech sits roughly in 300-3000 Hz; unvoiced fricatives and hiss
        are far higher. Combined with energy this separates speech from a door
        slamming, which is all the voice-activity detector needs.
        """
        if len(self.samples) < 2:
            return 0.0
        crossings = 0
        previous = self.samples[0]
        for value in self.samples[1:]:
            if (previous < 0) != (value < 0):
                crossings += 1
            previous = value
        return crossings * self.sample_rate / (len(self.samples) - 1)

    def as_dict(self) -> dict[str, object]:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "duration": round(self.duration, 4),
            "peak": self.peak(),
            "rms": round(self.rms(), 5),
            "zero_crossing_rate": round(self.zero_crossing_rate(), 1),
        }


# ------------------------------------------------------------------------ I/O


def read_wav(source: str | Path | BinaryIO) -> PcmAudio:
    """Read a WAV file into :class:`PcmAudio`.

    Anything that is not 16-bit PCM is refused with a reason. Resampling or
    converting other bit depths would need a codec, and silently reinterpreting
    the bytes would produce noise.
    """
    closer = None
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise AudioError(f"no such audio file: {path}")
        handle: BinaryIO = path.open("rb")
        closer = handle.close
    else:
        handle = source

    try:
        with wave.open(handle, "rb") as wav:
            width = wav.getsampwidth()
            channels = wav.getnchannels()
            rate = wav.getframerate()
            count = wav.getnframes()
            if width != 2:
                raise AudioError(
                    f"{width * 8}-bit audio is not supported; only 16-bit PCM"
                )
            if channels not in (1, 2):
                raise AudioError(f"{channels} channels are not supported; use 1 or 2")
            if rate <= 0:
                raise AudioError(f"the file declares an invalid rate of {rate} Hz")
            data = wav.readframes(count)
    except wave.Error as exc:
        raise AudioError(f"not a readable WAV file: {exc}") from exc
    finally:
        if closer is not None:
            closer()

    expected = count * channels * 2
    if len(data) != expected:
        raise AudioError(
            f"the file holds {len(data)} bytes of audio but its header promises {expected}"
        )
    samples = array.array("h")
    samples.frombytes(data)
    if len(samples) != count * channels:
        raise AudioError("the sample count does not match the audio data")
    return PcmAudio(rate, channels, samples)


def write_wav(audio: PcmAudio, destination: str | Path) -> Path:
    """Write :class:`PcmAudio` to a 16-bit PCM WAV file."""

    path = Path(destination)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(audio.channels)
            wav.setsampwidth(2)
            wav.setframerate(audio.sample_rate)
            wav.writeframes(audio.raw())
    except (wave.Error, OSError) as exc:
        raise AudioError(f"could not write {path}: {exc}") from exc
    return path


# ------------------------------------------------------------------- synthesis


def silence(duration: float, *, sample_rate: int = 16000, channels: int = 1) -> PcmAudio:
    """Digital silence - useful as a test fixture and as padding."""

    count = max(0, int(duration * sample_rate)) * channels
    return PcmAudio(sample_rate, channels, array.array("h", bytes(2 * count)))


def tone(
    frequency: float,
    duration: float,
    *,
    sample_rate: int = 16000,
    amplitude: float = 0.5,
    channels: int = 1,
) -> PcmAudio:
    """A sine tone, for tests that need a signal with known properties.

    Deliberately a pure sine rather than recorded speech: a test can assert its
    frequency and amplitude exactly, which recorded audio cannot offer.
    """
    if frequency <= 0:
        raise AudioError(f"frequency must be positive, not {frequency}")
    if frequency >= sample_rate / 2:
        raise AudioError(
            f"{frequency} Hz exceeds the Nyquist limit of {sample_rate / 2} Hz - "
            "it would alias into a different tone"
        )
    if not 0.0 < amplitude <= 1.0:
        raise AudioError(f"amplitude must be in (0, 1], not {amplitude}")

    count = int(duration * sample_rate)
    scale = amplitude * 32767.0
    samples = array.array(
        "h",
        (int(scale * math.sin(2 * math.pi * frequency * i / sample_rate)) for i in range(count)),
    )
    if channels == 2:
        stereo = array.array("h", [0]) * (count * 2)
        stereo[0::2] = samples
        stereo[1::2] = samples
        samples = stereo
    return PcmAudio(sample_rate, channels, samples)


def envelope(
    audio: PcmAudio, window: float = 0.02, hop: float | None = None
) -> list[tuple[float, float, float]]:
    """The amplitude envelope as ``(time, rms, zero_crossing_rate)`` triples.

    This is what the voice-activity detector looks at: a short window is enough
    to tell voiced speech from silence or from a click, and returning the
    numbers means a decision can be audited rather than taken on trust.
    """
    if window <= 0:
        raise AudioError(f"window must be positive, not {window}")
    hop = window if hop is None else hop
    if hop <= 0:
        raise AudioError(f"hop must be positive, not {hop}")

    size = max(1, int(window * audio.sample_rate))
    step = max(1, int(hop * audio.sample_rate))
    out: list[tuple[float, float, float]] = []
    for start in range(0, max(0, len(audio.samples) - size + 1), step * audio.channels):
        chunk = PcmAudio(
            audio.sample_rate,
            audio.channels,
            array.array("h", audio.samples[start : start + size * audio.channels]),
        )
        if not chunk.samples:
            break
        out.append(
            (
                (start // audio.channels) / audio.sample_rate,
                chunk.rms(),
                chunk.zero_crossing_rate(),
            )
        )
    return out


def frames(
    audio: PcmAudio, window: float = 0.02, hop: float | None = None
) -> Iterator[PcmAudio]:
    """Yield successive windows of audio."""

    for when, _rms, _zcr in envelope(audio, window, hop):
        yield audio.slice(when, when + window)
