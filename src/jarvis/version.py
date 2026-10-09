"""Single source of truth for the Jarvis version."""

__version__ = "0.4.0"

#: The highest phase completed *end to end*. Phases 7 and 8 shipped early because
#: they are independent of 3-6, and phases 3-6 each have a working core with one
#: boundary that is environmental rather than a gap in the code - see
#: PARTIAL_PHASES. ROADMAP.md is the accurate picture of what is and is not
#: built, and is deliberately not reduced to this one number.
PHASE = 2
PHASE_NAME = "Core runtime + live research"

#: Shipped ahead of their phase number because they depend on nothing in 3-6.
SHIPPED_EARLY = ("7 - dashboard", "8 - rush mode and the always-active loop")

#: Phases with a real, tested core and one boundary that this codebase cannot
#: cross on its own. Each entry names the boundary precisely, because "partially
#: built" without saying which part is the same as saying nothing.
PARTIAL_PHASES: dict[int, tuple[str, str]] = {
    3: (
        "voice: WAV I/O, DSP, voice-activity detection and barge-in are real",
        "transcription and synthesis need a model; no STT/TTS backend is bundled",
    ),
    4: (
        "vision: dependency-free PNG codec and frame analysis are real",
        "camera capture needs hardware this host does not have, and "
        "interpreting a scene needs a vision model",
    ),
    5: (
        "computer use: the process table is read for real from /proc",
        "clicking, typing and screenshots need a display server",
    ),
    6: (
        "revenue: approved plans execute through the policy engine and are monitored",
        "moving money, opening accounts and accepting terms always need a person",
    ),
}
