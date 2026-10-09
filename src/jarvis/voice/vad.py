"""Voice activity detection and endpointing.

Answers three questions a voice interface actually needs:

* is anyone speaking in this chunk of audio?
* when did the utterance start and stop? (endpointing - without it the agent
  either cuts people off or waits forever)
* did the user start talking *while* Jarvis was speaking? (barge-in, spec
  section 13: an interruption must stop the reply, not queue behind it)

The detection is energy plus zero-crossing rate, calibrated against the audio's
own noise floor rather than a fixed threshold. A fixed threshold is what makes
these systems fail: it is too quiet in a silent room and deaf in a loud one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.voice.audio import PcmAudio, envelope

__all__ = [
    "Utterance",
    "VadSettings",
    "VoiceActivityDetector",
    "detect_utterances",
    "is_speech",
]


@dataclass(slots=True, frozen=True)
class VadSettings:
    """Tunable detection parameters, with conservative defaults.

    Attributes
    ----------
    window / hop:
        Analysis window and step, in seconds. 20 ms is short enough to catch a
        syllable and long enough for a stable energy estimate.
    noise_floor_margin:
        A frame counts as speech when its energy exceeds the estimated noise
        floor by this factor. Relative, so the same settings work in a quiet
        study and a noisy office.
    minimum_speech:
        Seconds of continuous speech before an utterance is believed. Filters
        clicks and knocks.
    maximum_gap:
        Seconds of silence tolerated *inside* an utterance. Longer than a natural
        pause between words, shorter than a change of mind.
    zcr_low / zcr_high:
        Zero-crossing bounds, in Hz, for plausible speech. Energy alone cannot
        separate a slammed door from a voice; this can.
    """

    window: float = 0.02
    hop: float = 0.01
    noise_floor_margin: float = 3.0
    absolute_floor: float = 0.004
    minimum_speech: float = 0.12
    maximum_gap: float = 0.45
    zcr_low: float = 120.0
    zcr_high: float = 4200.0


@dataclass(slots=True)
class Utterance:
    """A detected span of speech.

    Attributes
    ----------
    start / end:
        Offsets in seconds from the beginning of the audio.
    peak_rms:
        The loudest frame, so a caller can tell a confident utterance from a
        marginal one.
    """

    start: float
    end: float
    peak_rms: float = 0.0
    mean_rms: float = 0.0
    frames: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def as_dict(self) -> dict[str, object]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "peak_rms": round(self.peak_rms, 5),
            "mean_rms": round(self.mean_rms, 5),
            "frames": self.frames,
        }


def is_speech(
    rms: float,
    zcr: float,
    *,
    noise_floor: float,
    settings: VadSettings | None = None,
) -> bool:
    """Whether one analysis frame looks like speech.

    Both energy and spectral character must agree. Requiring only energy lets a
    knock through; requiring only zero crossings lets hiss through.
    """
    settings = settings or VadSettings()
    threshold = max(settings.absolute_floor, noise_floor * settings.noise_floor_margin)
    if rms < threshold:
        return False
    return settings.zcr_low <= zcr <= settings.zcr_high


@dataclass(slots=True)
class VoiceActivityDetector:
    """Stateful detector, so it can be fed a live stream chunk by chunk.

    A stateless function would need the whole recording up front, which is no
    use for barge-in detection where the point is to react before it ends.
    """

    settings: VadSettings = field(default_factory=VadSettings)
    #: Running estimate of the background level, in RMS.
    noise_floor: float = 0.0
    _frames_seen: int = 0
    _speech_frames: int = 0

    def observe(self, rms: float, *, speech: bool) -> None:
        """Update the noise-floor estimate.

        Only non-speech frames move the floor: including speech would let a
        long utterance drag the threshold up behind itself until the speaker
        stopped being detected.
        """
        self._frames_seen += 1
        if speech:
            self._speech_frames += 1
            return
        # Exponential moving average with a modest smoothing factor.
        alpha = 0.05 if self._frames_seen > 1 else 1.0
        self.noise_floor = self.noise_floor * (1 - alpha) + rms * alpha

    def classify(self, rms: float, zcr: float) -> bool:
        """Whether this frame is speech, and feed the result back in."""

        speech = is_speech(rms, zcr, noise_floor=self.noise_floor, settings=self.settings)
        self.observe(rms, speech=speech)
        return speech

    def reset(self) -> None:
        self.noise_floor = 0.0
        self._frames_seen = 0
        self._speech_frames = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "noise_floor": round(self.noise_floor, 6),
            "frames_seen": self._frames_seen,
            "speech_frames": self._speech_frames,
            "settings": {
                "window": self.settings.window,
                "hop": self.settings.hop,
                "noise_floor_margin": self.settings.noise_floor_margin,
                "minimum_speech": self.settings.minimum_speech,
                "maximum_gap": self.settings.maximum_gap,
            },
        }


def detect_utterances(
    audio: PcmAudio, *, settings: VadSettings | None = None
) -> list[Utterance]:
    """Split a recording into the spans where someone was talking.

    Silence inside an utterance shorter than ``maximum_gap`` does not split it,
    so natural pauses between words stay inside one utterance. A run of speech
    shorter than ``minimum_speech`` is discarded as a click.
    """
    settings = settings or VadSettings()
    detector = VoiceActivityDetector(settings=settings)
    measurements = envelope(audio, settings.window, settings.hop)
    if not measurements:
        return []

    # First pass: decide per frame. The noise floor has to be estimated before
    # it can be used, so a short quiet preamble is what makes this work; with no
    # preamble the absolute floor carries the decision.
    marks: list[tuple[float, float, float, bool]] = []
    for when, rms, zcr in measurements:
        speech = detector.classify(rms, zcr)
        marks.append((when, rms, zcr, speech))

    utterances: list[Utterance] = []
    current: Utterance | None = None
    last_speech_at = 0.0

    for when, rms, _zcr, speech in marks:
        if speech:
            if current is None or when - last_speech_at > settings.maximum_gap:
                if current is not None:
                    utterances.append(current)
                current = Utterance(start=when, end=when + settings.window, peak_rms=rms, mean_rms=rms, frames=1)
            else:
                current.end = when + settings.window
                current.peak_rms = max(current.peak_rms, rms)
                current.mean_rms = (
                    (current.mean_rms * (current.frames - 1) + rms) / current.frames
                )
                current.frames += 1
            last_speech_at = when
    if current is not None:
        utterances.append(current)

    return [u for u in utterances if u.duration >= settings.minimum_speech]


def barge_in(
    audio: PcmAudio,
    *,
    playing_since: float,
    settings: VadSettings | None = None,
) -> float | None:
    """When, if at all, the user started talking over Jarvis.

    ``playing_since`` is the offset in ``audio`` at which Jarvis began speaking.
    Returns the offset of the first sustained speech after that point, or None.
    Anything before ``playing_since`` is ignored - it is part of the user's
    original request, not an interruption.
    """
    utterances = detect_utterances(audio, settings=settings)
    for utterance in utterances:
        if utterance.start >= playing_since:
            return utterance.start
    return None
