"""Voice session and interruption handling (spec sections 12, 13)."""

from __future__ import annotations

import threading

import pytest

from jarvis.core.tasks import Task, TaskState
from jarvis.interaction.voice import (
    Interruption,
    NullTextToSpeech,
    SpeechState,
    VoiceSession,
)


class ScriptedSTT:
    name = "scripted"

    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, audio: bytes, *, hint_language: str | None = None) -> str:
        return self.text


# --------------------------------------------------------------- the protocol
def test_the_spec_example_interruption_sequence(tasks):
    """"Open my project and-" -> "No, wait. Open the other project."

    Jarvis must stop speaking, stop the running task safely, save its state,
    parse the new instruction, and be able to resume the old one.
    """
    session = VoiceSession(tasks=tasks)
    started = threading.Event()

    def long_step(ctx):
        started.set()
        for _ in range(400):
            ctx.check_interrupt()
        return "opened"

    task = Task(title="open the first project")
    task.add_step("launch", long_step)
    task.add_step("load", lambda ctx: "loaded")
    tasks.submit(task)
    worker = threading.Thread(target=tasks.run, daemon=True)
    worker.start()
    assert started.wait(2.0)

    session.start_speaking("Opening-", task_id=task.id)
    interruption = session.interrupt("No, wait. Open the other project.")

    assert isinstance(interruption, Interruption)
    assert interruption.stopped_speech is True
    assert session.current_speech == "", "speech must stop immediately"
    assert interruption.stopped_task_id == task.id
    assert interruption.new_utterance.text == "No, wait. Open the other project."
    worker.join(timeout=5)
    assert task.state is TaskState.PAUSED


def test_the_interrupted_task_can_be_restored(tasks):
    session = VoiceSession(tasks=tasks)
    task = Task(title="interrupted work")
    task.add_step("one", lambda ctx: "one")
    task.add_step("two", lambda ctx: "two")
    tasks.submit(task)

    session.current_task_id = task.id
    session.start_speaking("Working on it")
    tasks.interrupt(task.id)
    session.suspended.append(task.id)

    report = session.resume_previous()
    assert report["resumed"] is True
    assert report["task_id"] == task.id
    tasks.run()
    assert task.state is TaskState.DONE


def test_resume_with_nothing_suspended_is_honest():
    session = VoiceSession()
    assert session.resume_previous() == {"resumed": False, "reason": "nothing suspended"}


def test_a_task_with_no_steps_is_cancelled_not_left_half_done(tasks):
    session = VoiceSession(tasks=tasks)
    task = Task(title="atomic, cannot be checkpointed")
    tasks.submit(task)
    session.current_task_id = task.id
    interruption = session.interrupt("stop")
    assert interruption.saved_checkpoint is None
    assert task.state is TaskState.CANCELLED


# ------------------------------------------------------------------ language
def test_language_switches_with_the_latest_utterance():
    session = VoiceSession(language_policy="match")
    assert session.utterance("Open my project.").language == "en"
    assert session.utterance("Ab mera project kholo.").language == "hi"
    assert session.utterance("Now explain what you're doing.").language == "en"


def test_an_interruption_is_flagged_as_one():
    session = VoiceSession()
    session.start_speaking("Speaking now")
    utterance = session.utterance("stop")
    assert utterance.interrupted is True


# --------------------------------------------------------------------- state
def test_state_machine_transitions():
    session = VoiceSession()
    assert session.state is SpeechState.IDLE
    session.start_speaking("hello")
    assert session.state is SpeechState.SPEAKING
    session.stop_speaking()
    assert session.state is SpeechState.INTERRUPTED
    session.idle()
    assert session.state is SpeechState.IDLE


def test_snapshot_reports_the_session():
    session = VoiceSession()
    session.start_speaking("hi")
    snapshot = session.snapshot()
    assert snapshot["speaking"] is True
    assert snapshot["stt"] == "offline"


# ------------------------------------------------------------- no fake audio
def test_stt_refuses_rather_than_inventing_a_transcript():
    session = VoiceSession()
    with pytest.raises(RuntimeError) as excinfo:
        session.listen(b"\x00\x01audio")
    assert "will not guess" in str(excinfo.value)


def test_tts_refuses_rather_than_producing_silence():
    with pytest.raises(RuntimeError):
        NullTextToSpeech().synthesise("hello")


def test_a_real_backend_can_be_plugged_in():
    session = VoiceSession(stt=ScriptedSTT("Open my project"))
    utterance = session.listen(b"audio")
    assert utterance.text == "Open my project"
    assert utterance.language == "en"


def test_history_is_kept_for_context():
    session = VoiceSession(stt=ScriptedSTT("hi"))
    session.listen(b"a")
    session.listen(b"b")
    assert len(session.history) == 2
