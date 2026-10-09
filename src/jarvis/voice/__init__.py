"""Voice audio: PCM I/O, digital signal processing, and voice-activity detection.

Standard library only. ``audioop`` would be the obvious dependency but it is
deprecated and slated for removal in Python 3.13, and this project turns
``DeprecationWarning`` into an error.

What is here is real and runs with no microphone:

* :mod:`jarvis.voice.audio` - 16-bit PCM WAV reading and writing, RMS, zero
  crossing rate, envelopes, and synthesised test tones.
* :mod:`jarvis.voice.vad` - noise-floor-calibrated voice activity detection,
  endpointing, and barge-in detection.

What is *not* here is transcription. Turning samples into words needs a model,
and an unconfigured provider is reported as unconfigured rather than
approximated.
"""

from jarvis.voice.audio import (
    AudioError,
    PcmAudio,
    envelope,
    read_wav,
    silence,
    tone,
    write_wav,
)
from jarvis.voice.vad import (
    Utterance as AudioUtterance,
)
from jarvis.voice.vad import (
    VadSettings,
    VoiceActivityDetector,
    barge_in,
    detect_utterances,
    is_speech,
)

__all__ = [
    "AudioError",
    "AudioUtterance",
    "PcmAudio",
    "VadSettings",
    "VoiceActivityDetector",
    "barge_in",
    "detect_utterances",
    "envelope",
    "is_speech",
    "read_wav",
    "silence",
    "tone",
    "write_wav",
]
