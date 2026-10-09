"""Voice interaction and interruption handling (spec sections 12, 13).

Phase 2 of the roadmap. The *audio* backends (speech-to-text, text-to-speech,
VAD) are optional extras, but the conversational protocol around them is real and
runs today:

    TASK -> SUBTASK 1 -> SUBTASK 2 -> INTERRUPTION -> SAVE STATE ->
    NEW REQUEST -> HANDLE NEW REQUEST -> RESTORE PREVIOUS STATE -> CONTINUE

:class:`VoiceSession` owns that state machine and is fully testable without a
microphone, because the interesting part is not the audio - it is deciding what
to stop, what to keep and what to resume.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol

from jarvis.core.tasks import Task, TaskManager
from jarvis.interaction.lang import detect, respond_in


class SpeechState(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


class SpeechToText(Protocol):
    name: str

    def transcribe(self, audio: bytes, *, hint_language: str | None = None) -> str:
        ...  # pragma: no cover - protocol


class TextToSpeech(Protocol):
    name: str

    def synthesise(self, text: str, *, language: str = "en") -> bytes:
        ...  # pragma: no cover - protocol


class NullSpeechToText:
    """Offline stand-in. It refuses rather than inventing a transcript."""

    name = "offline"

    def transcribe(self, audio: bytes, *, hint_language: str | None = None) -> str:
        raise RuntimeError(
            "No speech-to-text backend is configured. Install the 'voice' extra or "
            "register a provider - Jarvis will not guess at what was said."
        )


class NullTextToSpeech:
    name = "offline"

    def synthesise(self, text: str, *, language: str = "en") -> bytes:
        raise RuntimeError(
            "No text-to-speech backend is configured. Install the 'voice' extra or "
            "register a provider."
        )


@dataclass
class Utterance:
    text: str
    language: str
    confidence: float
    at: str = ""
    interrupted: bool = False


@dataclass
class Interruption:
    """What happened when the user cut in (spec section 13)."""

    stopped_speech: bool
    stopped_task_id: str | None
    saved_checkpoint: int | None
    new_utterance: Utterance
    previous_task_title: str = ""
    resumed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "stopped_speech": self.stopped_speech,
            "stopped_task_id": self.stopped_task_id,
            "saved_checkpoint": self.saved_checkpoint,
            "new_utterance": {
                "text": self.new_utterance.text,
                "language": self.new_utterance.language,
                "confidence": self.new_utterance.confidence,
            },
            "previous_task_title": self.previous_task_title,
            "resumed": self.resumed,
        }


@dataclass
class VoiceSession:
    """Owns speaking state, barge-in handling and task save/restore."""

    tasks: TaskManager | None = None
    stt: SpeechToText = field(default_factory=NullSpeechToText)
    tts: TextToSpeech = field(default_factory=NullTextToSpeech)
    language_policy: str = "match"
    state: SpeechState = SpeechState.IDLE
    current_speech: str = ""
    current_task_id: str | None = None
    history: list[Utterance] = field(default_factory=list)
    #: Task the user interrupted, kept so it can be resumed later.
    suspended: list[str] = field(default_factory=list)

    # --- listening -----------------------------------------------------------
    def listen(self, audio: bytes, *, hint_language: str | None = None) -> Utterance:
        """Transcribe audio. Raises if no backend exists - never guesses."""
        self.state = SpeechState.LISTENING
        text = self.stt.transcribe(audio, hint_language=hint_language)
        return self.utterance(text)

    def utterance(self, text: str) -> Utterance:
        """Wrap text (typed or transcribed) as an utterance with its language."""
        detected = detect(text)
        reply_language = respond_in(text, self.language_policy)
        item = Utterance(
            text=text,
            language=reply_language,
            confidence=detected.confidence,
            interrupted=self.state is SpeechState.SPEAKING,
        )
        self.history.append(item)
        return item

    # --- speaking ------------------------------------------------------------
    def start_speaking(self, text: str, *, task_id: str | None = None) -> None:
        self.state = SpeechState.SPEAKING
        self.current_speech = text
        self.current_task_id = task_id

    def stop_speaking(self) -> str:
        """Stop immediately. Returns whatever was left unsaid, for context."""
        spoken = self.current_speech
        self.current_speech = ""
        if self.state is SpeechState.SPEAKING:
            self.state = SpeechState.INTERRUPTED
        return spoken

    # --- interruption --------------------------------------------------------
    def interrupt(self, new_text: str) -> Interruption:
        """Spec section 13, steps 1-5, in order.

        1. stop current speech
        2. stop or pause the current task *if that is safe*
        3. parse the new instruction
        4. preserve useful context
        5. hand back control so the new instruction can be handled
        """
        stopped_speech = bool(self.current_speech)
        self.stop_speaking()

        stopped_task_id: str | None = None
        checkpoint: int | None = None
        title = ""
        task: Task | None = None
        if self.tasks is not None and self.current_task_id:
            task = self.tasks.get(self.current_task_id)
        if task is not None and not task.state.terminal:
            stopped_task_id = task.id
            title = task.title
            # Interrupting is always safe when the task checkpoints itself between
            # steps. A task with no steps cannot be checkpointed, so it is cancelled
            # rather than left half-done.
            if task.steps:
                self.tasks.interrupt(task.id)
                checkpoint = task.checkpoint
                self.suspended.append(task.id)
            else:
                self.tasks.cancel(task.id)

        parsed = self.utterance(new_text)
        return Interruption(
            stopped_speech=stopped_speech,
            stopped_task_id=stopped_task_id,
            saved_checkpoint=checkpoint,
            new_utterance=parsed,
            previous_task_title=title,
        )

    # --- audio-driven barge-in -----------------------------------------------
    def detect_barge_in(
        self,
        audio: bytes,
        *,
        sample_rate: int = 16000,
        playing_since: float = 0.0,
    ) -> dict[str, Any]:
        """Decide whether the user talked over Jarvis, from the audio itself.

        This is the part that used to be assumed rather than done: something has
        to look at the samples and say whether a voice is present. The detector
        is the stdlib VAD in :mod:`jarvis.voice.vad`, so it runs with no
        microphone and no model, and its numbers are reported rather than hidden.

        ``playing_since`` is the offset at which Jarvis started speaking. Speech
        before that point is the user's original request and is not an
        interruption - counting it would make Jarvis interrupt itself.
        """
        import array

        from jarvis.voice.audio import AudioError, PcmAudio
        from jarvis.voice.vad import barge_in, detect_utterances

        samples = array.array("h")
        try:
            samples.frombytes(audio[: len(audio) - len(audio) % 2])
        except ValueError as exc:
            return {"interrupted": False, "reason": f"audio could not be read: {exc}"}
        if not samples:
            return {"interrupted": False, "reason": "the audio was empty"}

        try:
            recording = PcmAudio(sample_rate, 1, samples)
            spans = detect_utterances(recording)
            moment = barge_in(recording, playing_since=playing_since)
        except AudioError as exc:
            return {"interrupted": False, "reason": str(exc)}

        return {
            "interrupted": moment is not None,
            "at": moment,
            "speech_started_before_playback": any(
                span.start < playing_since for span in spans
            ),
            "utterances": [span.as_dict() for span in spans],
            #: Recorded so the decision can be audited rather than trusted.
            "state": self.state.value,
        }

    # --- resume --------------------------------------------------------------
    def resume_previous(self) -> dict[str, Any]:
        """Restore the interrupted task and continue from its checkpoint."""
        if self.tasks is None or not self.suspended:
            return {"resumed": False, "reason": "nothing suspended"}
        task_id = self.suspended.pop()
        task = self.tasks.get(task_id)
        if task is None:
            return {"resumed": False, "reason": f"task {task_id} no longer exists"}
        if task.state.terminal:
            return {"resumed": False, "reason": f"task {task.title} already finished"}
        from jarvis.core.tasks import TaskState

        task.state = TaskState.PENDING
        resumed_at = task.checkpoint
        return {
            "resumed": True,
            "task_id": task.id,
            "title": task.title,
            "resumed_at_step": resumed_at,
            "completed_steps": list(task.results),
        }

    # --- state ---------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "speaking": bool(self.current_speech),
            "current_task": self.current_task_id,
            "suspended": list(self.suspended),
            "utterances": len(self.history),
            "last_language": self.history[-1].language if self.history else None,
            "stt": getattr(self.stt, "name", "unknown"),
            "tts": getattr(self.tts, "name", "unknown"),
            #: Voice activity detection is stdlib, so it is always available even
            #: when no STT or TTS backend is configured. Saying so avoids the
            #: impression that "no audio backend" means "cannot hear".
            "vad": "builtin",
        }

    def idle(self) -> None:
        self.state = SpeechState.IDLE
        self.current_speech = ""
        self.current_task_id = None
