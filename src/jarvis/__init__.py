"""Jarvis - an autonomous personal, research, revenue and development agent.

The public entry point is :func:`jarvis.runtime.build_runtime`, which wires the
core together from :class:`jarvis.config.Settings`.
"""

from jarvis.version import PHASE, PHASE_NAME, __version__

__all__ = ["__version__", "PHASE", "PHASE_NAME"]
