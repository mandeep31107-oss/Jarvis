"""Single source of truth for the Jarvis version."""

__version__ = "0.3.0"

#: The highest phase completed *in order*. Phases 7 and 8 shipped early because
#: they are independent of 3-6; ROADMAP.md is the accurate picture of what is
#: and is not built, and it is deliberately not reduced to this one number.
PHASE = 2
PHASE_NAME = "Core runtime + live research (dashboard, rush mode and the always-active loop also shipped)"
