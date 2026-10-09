"""Tests for the audio pipeline: PCM I/O, DSP, and voice-activity detection.

The signal-processing assertions are written against values that can be derived
by hand - a 440 Hz sine at 16 kHz crosses zero 880 times a second, and a sine of
amplitude 0.5 has an RMS of 0.5/sqrt(2). Those are not numbers my code chose, so
agreement means something.

WAV structure is verified by parsing the RIFF header directly with ``struct``
rather than through the ``wave`` module, so a mistake shared by both the writer
and the reader cannot hide.
"""

from __future__ import annotations

import array
import math
import struct
import wave
from pathlib import Path

import pytest

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
    Utterance,
    VadSettings,
    VoiceActivityDetector,
    barge_in,
    detect_utterances,
    is_speech,
)

RATE = 16000


# --------------------------------------------------------------------- helpers


def speechish(duration: float, *, rate: int = RATE, f0: float = 180.0, amplitude: float = 0.3) -> array.array:
    """A harmonic stack that behaves like voiced speech.

    Not a pure tone: real speech has several harmonics, and a detector tuned to
    one frequency would pass on this. Using it keeps the test honest about
    detecting *speech*, not detecting *a sine wave*.
    """
    count = int(duration * rate)
    out = array.array("h")
    for i in range(count):
        t = i / rate
        value = (
            math.sin(2 * math.pi * f0 * t)
            + 0.6 * math.sin(2 * math.pi * f0 * 2.3 * t)
            + 0.4 * math.sin(2 * math.pi * f0 * 3.7 * t)
            + 0.3 * math.sin(2 * math.pi * 1100 * t)
        )
        out.append(int(amplitude * 32767 * value / 2.3))
    return out


def recording(*segments: array.array, rate: int = RATE) -> PcmAudio:
    joined = array.array("h")
    for segment in segments:
        joined += segment
    return PcmAudio(rate, 1, joined)


def quiet(duration: float, rate: int = RATE) -> array.array:
    return array.array("h", bytes(2 * int(duration * rate)))


# ------------------------------------------------------------------ PCM basics


def test_a_tone_has_the_frequency_and_level_it_was_built_with() -> None:
    """Two hand-derivable values, so this pins the DSP rather than restating it."""
    audio = tone(440.0, 1.0, sample_rate=RATE, amplitude=0.5)

    # A 440 Hz sine crosses zero twice per cycle.
    assert audio.zero_crossing_rate() == pytest.approx(880.0, rel=0.01)
    # RMS of a sine is amplitude / sqrt(2).
    assert audio.rms() == pytest.approx(0.5 / math.sqrt(2), rel=0.02)


def test_duration_and_frame_count_follow_the_sample_rate() -> None:
    audio = tone(200.0, 0.25, sample_rate=8000)

    assert audio.sample_rate == 8000
    assert audio.frames == 2000
    assert audio.duration == pytest.approx(0.25)


def test_silence_has_no_energy_at_all() -> None:
    audio = silence(0.3)

    assert audio.rms() == 0.0
    assert audio.peak() == 0
    assert audio.zero_crossing_rate() == 0.0


def test_stereo_interleaves_both_channels() -> None:
    audio = tone(300.0, 0.1, channels=2)

    assert audio.channels == 2
    assert len(audio.samples) == audio.frames * 2
    # Both channels carry the same tone, so they must agree sample for sample.
    assert audio.samples[0::2] == audio.samples[1::2]


def test_slicing_returns_the_requested_time_range() -> None:
    audio = tone(100.0, 1.0, sample_rate=RATE)
    cut = audio.slice(0.25, 0.5)

    assert cut.duration == pytest.approx(0.25)
    assert cut.sample_rate == audio.sample_rate


def test_a_slice_beyond_the_end_is_clamped_not_padded() -> None:
    audio = tone(100.0, 0.5)

    assert audio.slice(0.0, 99.0).duration == pytest.approx(0.5)
    assert audio.slice(99.0, 100.0).frames == 0


def test_a_tone_above_nyquist_is_refused_because_it_would_alias() -> None:
    """Aliasing would silently produce a *different* tone - worse than an error."""
    with pytest.raises(AudioError, match="Nyquist"):
        tone(9000.0, 0.1, sample_rate=16000)


def test_an_out_of_range_amplitude_is_refused() -> None:
    with pytest.raises(AudioError, match="amplitude"):
        tone(440.0, 0.1, amplitude=1.5)


def test_an_invalid_channel_count_is_refused() -> None:
    with pytest.raises(AudioError, match="mono or stereo"):
        PcmAudio(RATE, 5, array.array("h", [0, 0, 0]))


def test_a_buffer_of_the_wrong_sample_width_is_refused() -> None:
    with pytest.raises(AudioError, match="16-bit"):
        PcmAudio(RATE, 1, array.array("i", [0, 0, 0]))


# --------------------------------------------------------------------- WAV I/O


def test_a_written_wav_round_trips_byte_for_byte(tmp_path: Path) -> None:
    original = tone(440.0, 0.2, amplitude=0.4)
    path = write_wav(original, tmp_path / "tone.wav")

    back = read_wav(path)

    assert back.sample_rate == original.sample_rate
    assert back.channels == original.channels
    assert back.samples == original.samples


def test_a_written_wav_has_a_structurally_valid_riff_header(tmp_path: Path) -> None:
    """Parsed with struct, not with the wave module that wrote it."""
    path = write_wav(tone(300.0, 0.1), tmp_path / "header.wav")
    raw = path.read_bytes()

    assert raw[:4] == b"RIFF"
    assert raw[8:12] == b"WAVE"
    assert struct.unpack("<I", raw[4:8])[0] + 8 == len(raw)

    fmt = struct.unpack("<HHIIHH", raw[20:36])
    assert fmt[0] == 1, "format 1 is uncompressed PCM"
    assert fmt[1] == 1, "one channel"
    assert fmt[2] == RATE
    assert fmt[5] == 16, "16 bits per sample"


def test_a_stereo_wav_records_its_channel_count(tmp_path: Path) -> None:
    path = write_wav(tone(300.0, 0.1, channels=2), tmp_path / "stereo.wav")

    back = read_wav(path)
    assert back.channels == 2
    assert back.frames == int(0.1 * RATE)


def test_missing_directories_are_created(tmp_path: Path) -> None:
    path = write_wav(silence(0.01), tmp_path / "nested" / "deeper" / "a.wav")
    assert path.exists()


def test_reading_a_missing_file_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="no such audio file"):
        read_wav(tmp_path / "absent.wav")


def test_reading_a_non_wav_file_reports_it(tmp_path: Path) -> None:
    path = tmp_path / "text.wav"
    path.write_bytes(b"this is not audio data at all")

    with pytest.raises(AudioError, match="not a readable WAV"):
        read_wav(path)


def test_eight_bit_audio_is_refused_rather_than_reinterpreted(tmp_path: Path) -> None:
    """Treating 8-bit unsigned data as 16-bit signed would produce noise."""
    path = tmp_path / "eight.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(8000)
        wav.writeframes(bytes([128] * 100))

    with pytest.raises(AudioError, match="16-bit"):
        read_wav(path)


def test_five_channel_audio_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "surround.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(5)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(bytes(2 * 5 * 10))

    with pytest.raises(AudioError, match="channels"):
        read_wav(path)


def test_audio_can_be_read_from_a_file_like_object() -> None:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(bytes(2 * 50))
    buffer.seek(0)

    assert read_wav(buffer).frames == 50


# -------------------------------------------------------------------- envelope


def test_the_envelope_separates_a_tone_from_silence() -> None:
    audio = recording(quiet(0.2), speechish(0.3), quiet(0.2))
    points = envelope(audio, window=0.02, hop=0.01)

    assert points, "an envelope must return something"
    energies = [rms for _when, rms, _zcr in points]
    assert min(energies) < 0.001, "the silent sections must read as silent"
    assert max(energies) > 0.05, "the voiced section must read as loud"
    # Timestamps must be monotonically increasing.
    times = [when for when, _rms, _zcr in points]
    assert times == sorted(times)


def test_the_envelope_window_must_be_positive() -> None:
    with pytest.raises(AudioError, match="window"):
        envelope(tone(200.0, 0.1), window=0.0)


def test_audio_reports_itself_as_a_json_safe_dict() -> None:
    import json

    payload = tone(440.0, 0.1).as_dict()
    json.dumps(payload)
    assert payload["sample_rate"] == RATE
    assert payload["duration"] == pytest.approx(0.1)


# ------------------------------------------------------------------------- VAD


def test_a_voiced_frame_is_speech_and_silence_is_not() -> None:
    settings = VadSettings()

    assert is_speech(0.1, 900.0, noise_floor=0.001, settings=settings) is True
    assert is_speech(0.0001, 900.0, noise_floor=0.001, settings=settings) is False


def test_energy_alone_is_not_enough_to_call_something_speech() -> None:
    """A door slam is loud but not voiced; the zero-crossing range rejects it."""
    settings = VadSettings()

    # Loud and very low frequency - a thump.
    assert is_speech(0.4, 40.0, noise_floor=0.001, settings=settings) is False
    # Loud and hissy.
    assert is_speech(0.4, 7000.0, noise_floor=0.001, settings=settings) is False


def test_a_loud_environment_raises_the_threshold() -> None:
    """A fixed threshold is why these systems fail in noisy rooms."""
    settings = VadSettings()
    quiet_room = is_speech(0.02, 900.0, noise_floor=0.001, settings=settings)
    noisy_room = is_speech(0.02, 900.0, noise_floor=0.01, settings=settings)

    assert quiet_room is True
    assert noisy_room is False


def test_a_full_recording_yields_one_utterance_for_one_phrase() -> None:
    audio = recording(quiet(0.4), speechish(0.6), quiet(0.4))

    utterances = detect_utterances(audio)

    assert len(utterances) == 1
    found = utterances[0]
    assert isinstance(found, Utterance)
    # The speech occupies 0.4s-1.0s; allow the window size as slack.
    assert found.start == pytest.approx(0.4, abs=0.05)
    assert found.end == pytest.approx(1.0, abs=0.06)
    assert found.peak_rms > found.mean_rms * 0.5


def test_pure_silence_yields_no_utterances() -> None:
    assert detect_utterances(silence(1.5)) == []


def test_a_short_click_is_not_an_utterance() -> None:
    """Minimum duration filters knocks, which would otherwise interrupt Jarvis."""
    audio = recording(quiet(0.5), speechish(0.03), quiet(0.5))

    assert detect_utterances(audio) == []


def test_two_phrases_separated_by_a_long_pause_are_two_utterances() -> None:
    audio = recording(quiet(0.3), speechish(0.4), quiet(0.9), speechish(0.4), quiet(0.3))

    utterances = detect_utterances(audio)

    assert len(utterances) == 2
    assert utterances[0].end < utterances[1].start


def test_a_short_pause_inside_a_phrase_does_not_split_it() -> None:
    """Pauses between words are normal; splitting on them is the classic bug."""
    audio = recording(quiet(0.3), speechish(0.3), quiet(0.15), speechish(0.3), quiet(0.3))

    utterances = detect_utterances(audio)

    assert len(utterances) == 1


def test_utterances_serialise_to_json_safe_dicts() -> None:
    import json

    audio = recording(quiet(0.3), speechish(0.4), quiet(0.3))
    payload = detect_utterances(audio)[0].as_dict()

    json.dumps(payload)
    assert payload["duration"] > 0.3


def test_a_loud_recording_does_not_drag_the_noise_floor_up_behind_itself() -> None:
    """The floor is estimated from non-speech frames only.

    If speech moved the floor, a long utterance would raise the threshold until
    the speaker stopped being detected part way through their own sentence.
    """
    detector = VoiceActivityDetector()
    for _ in range(20):
        detector.observe(0.001, speech=False)
    floor_before = detector.noise_floor

    for _ in range(50):
        detector.observe(0.5, speech=True)

    assert detector.noise_floor == pytest.approx(floor_before)


def test_the_detector_adapts_to_a_noisier_room() -> None:
    detector = VoiceActivityDetector()
    for _ in range(40):
        detector.observe(0.02, speech=False)

    assert detector.noise_floor == pytest.approx(0.02, rel=0.05)


def test_the_detector_reports_its_own_state_for_audit() -> None:
    import json

    detector = VoiceActivityDetector()
    detector.classify(0.001, 500.0)
    detector.classify(0.3, 900.0)

    payload = detector.as_dict()
    json.dumps(payload)
    assert payload["frames_seen"] == 2


# --------------------------------------------------------------------- barge-in


def test_speech_after_playback_starts_is_detected_as_an_interruption() -> None:
    """Spec section 13: an interruption must be noticed, not queued."""
    audio = recording(quiet(0.3), speechish(0.5), quiet(0.3))

    assert barge_in(audio, playing_since=0.2) == pytest.approx(0.3, abs=0.05)


def test_speech_that_finished_before_playback_is_not_an_interruption() -> None:
    """The user's original request must not count as interrupting the answer."""
    audio = recording(quiet(0.3), speechish(0.5), quiet(0.3))

    assert barge_in(audio, playing_since=0.9) is None


def test_silence_during_playback_is_not_an_interruption() -> None:
    assert barge_in(silence(1.0), playing_since=0.0) is None


# ------------------------------------------------- voice session integration


def test_the_voice_session_detects_barge_in_from_raw_samples() -> None:
    """The session decides from the audio, not from an assumed flag."""
    from jarvis.interaction.voice import SpeechState, VoiceSession

    audio = recording(quiet(0.3), speechish(0.5), quiet(0.3))
    session = VoiceSession()
    session.start_speaking("Here is a long answer you did not want.")
    assert session.state is SpeechState.SPEAKING

    found = session.detect_barge_in(audio.raw(), playing_since=0.2)

    assert found["interrupted"] is True
    assert found["at"] == pytest.approx(0.3, abs=0.06)
    assert found["utterances"], "the detected spans are reported, not hidden"
    assert found["state"] == "speaking"


def test_the_users_own_request_is_not_treated_as_an_interruption() -> None:
    """Speech that finished before Jarvis started talking is not a barge-in.

    Counting it would make Jarvis interrupt its own answer immediately.
    """
    from jarvis.interaction.voice import VoiceSession

    audio = recording(quiet(0.3), speechish(0.4), quiet(0.5))
    session = VoiceSession()

    found = session.detect_barge_in(audio.raw(), playing_since=0.8)

    assert found["interrupted"] is False
    assert found["speech_started_before_playback"] is True


def test_silence_during_a_reply_is_not_an_interruption() -> None:
    from jarvis.interaction.voice import VoiceSession

    session = VoiceSession()
    session.start_speaking("Still talking.")

    found = session.detect_barge_in(silence(1.0).raw(), playing_since=0.0)

    assert found["interrupted"] is False
    assert found["utterances"] == []


def test_empty_and_malformed_audio_are_reported_rather_than_crashing() -> None:
    from jarvis.interaction.voice import VoiceSession

    session = VoiceSession()

    assert session.detect_barge_in(b"")["interrupted"] is False
    assert "reason" in session.detect_barge_in(b"")
    # An odd number of bytes cannot be 16-bit samples; the stray byte is dropped.
    assert session.detect_barge_in(b"\x01")["interrupted"] is False


def test_the_session_reports_that_vad_is_available_without_a_backend() -> None:
    """No STT/TTS configured must not read as 'cannot hear'."""
    from jarvis.interaction.voice import VoiceSession

    snapshot = VoiceSession().snapshot()

    assert snapshot["vad"] == "builtin"
    assert snapshot["stt"] == "offline"
    assert snapshot["tts"] == "offline"
