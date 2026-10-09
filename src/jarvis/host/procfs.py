"""Reading the process table straight from ``/proc``.

This is the part of "computer use" that is genuinely available on a Linux host
with no display server, no accessibility bus and no automation toolkit. Listing
what is running, who started it, and how much it is using is real information
that can be verified against ``ps``, and it is what makes "is this app running?"
an answer rather than a guess.

What this module does *not* do is click, type or capture the screen. Those need
a display server, and a host without one gets :class:`CapabilityUnavailable`
naming the missing piece rather than a silent no-op.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ProcessInfo",
    "available",
    "find",
    "list_processes",
    "read_process",
    "proc_available",
]

#: Fields this reader can report. Documented so a caller does not have to guess
#: whether an empty string means "zero" or "not obtainable".
FIELDS = ("pid", "name", "command_line", "state", "parent", "owner", "memory_kb", "cpu_seconds")

#: ``/proc`` state letters, decoded. Unknown letters are passed through rather
#: than invented.
STATES = {
    "R": "running",
    "S": "sleeping",
    "D": "uninterruptible sleep (usually I/O)",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing stop",
    "X": "dead",
    "I": "idle kernel thread",
}

PAGE_SIZE = 4096


@dataclass(slots=True)
class ProcessInfo:
    """One row of the process table.

    Attributes
    ----------
    memory_kb:
        Resident set size. Zero can mean either "not resident" or "not
        readable", so ``memory_available`` says which.
    command_line:
        Empty for kernel threads, which legitimately have none.
    """

    pid: int
    name: str = ""
    command_line: str = ""
    state: str = ""
    parent: int = 0
    owner: int = -1
    memory_kb: int = 0
    cpu_seconds: float = 0.0
    memory_available: bool = False
    arguments: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.arguments is None:
            self.arguments = []

    @property
    def state_text(self) -> str:
        return STATES.get(self.state, self.state or "unknown")

    @property
    def executable(self) -> str:
        """The first argument, i.e. the program being run."""

        return self.arguments[0] if self.arguments else self.name

    def as_dict(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "name": self.name,
            "state": self.state,
            "state_text": self.state_text,
            "parent": self.parent,
            "owner": self.owner,
            "memory_kb": self.memory_kb if self.memory_available else None,
            "cpu_seconds": round(self.cpu_seconds, 3),
            "command_line": self.command_line,
            "arguments": list(self.arguments),
        }


def proc_available() -> bool:
    """Whether ``/proc`` exposes a process table on this host."""

    return Path("/proc/self/stat").exists() and any(Path("/proc").glob("[0-9]*"))


def available() -> bool:
    """Alias for :func:`proc_available`, for symmetry with other backends."""

    return proc_available()


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _split_stat(text: str) -> list[str]:
    """Split ``/proc/<pid>/stat`` despite the name containing spaces and parens.

    The second field is ``(comm)`` and a process is allowed to be named almost
    anything, including ``) (``. Splitting on whitespace alone misreads every
    field after the name - a subtle wrongness that would show up as nonsense
    states and parent PIDs.
    """
    start = text.find("(")
    end = text.rfind(")")
    if start == -1 or end == -1 or end < start:
        return text.split()
    before = text[:start].split()
    name = text[start + 1 : end]
    after = text[end + 2 :].split()
    return [*before, f"({name})", *after]


def read_process(pid: int, *, clock_ticks: int = 100) -> ProcessInfo | None:
    """Read one process, or None if it has already gone.

    Processes come and go while the table is being walked, so a vanished PID is
    an expected outcome rather than an error.
    """
    root = Path(f"/proc/{pid}")
    if not root.is_dir():
        return None

    fields = _split_stat(_read(root / "stat"))
    if len(fields) < 3:
        return None

    info = ProcessInfo(pid=pid)
    info.name = fields[1].strip("()") if len(fields) > 1 else ""
    info.state = fields[2] if len(fields) > 2 else ""
    if len(fields) > 3:
        with contextlib.suppress(ValueError):
            info.parent = int(fields[3])
    #: utime (14) and stime (15) are in clock ticks, not seconds. Reporting them
    #: raw would overstate CPU use by a factor of 100 on a typical system.
    with contextlib.suppress(ValueError, IndexError):
        ticks = int(fields[13]) + int(fields[14])
        info.cpu_seconds = ticks / clock_ticks if clock_ticks else 0.0

    raw_cmd = _read(root / "cmdline")
    if raw_cmd:
        # Arguments are NUL-separated; kernel threads have an empty cmdline.
        parts = [part for part in raw_cmd.split("\0") if part]
        info.arguments = parts
        info.command_line = " ".join(parts)

    status = _read(root / "status")
    for line in status.splitlines():
        if line.startswith("Uid:"):
            with contextlib.suppress(ValueError, IndexError):
                info.owner = int(line.split()[1])
        elif line.startswith("VmRSS:"):
            with contextlib.suppress(ValueError, IndexError):
                info.memory_kb = int(line.split()[1])
                info.memory_available = True
    return info


def list_processes(
    *, include_kernel_threads: bool = False, owner: int | None = None
) -> list[ProcessInfo]:
    """The process table, sorted by PID.

    Kernel threads are excluded by default: they have no command line and no
    memory of their own, and including them buries the applications a user is
    actually asking about.
    """
    if not proc_available():
        return []
    ticks = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    out: list[ProcessInfo] = []
    for entry in _pid_dirs():
        info = read_process(entry, clock_ticks=ticks)
        if info is None:
            continue
        if owner is not None and info.owner != owner:
            continue
        if not include_kernel_threads and not info.arguments:
            continue
        out.append(info)
    out.sort(key=lambda p: p.pid)
    return out


def _pid_dirs() -> Iterator[int]:
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and entry.is_dir():
            yield int(entry.name)


def find(
    needle: str, *, match_command_line: bool = True, exact_name: bool = False
) -> list[ProcessInfo]:
    """Processes whose name or command line contains ``needle``.

    Case-insensitive by default, because a user asking to close "Chrome" does
    not care that the binary is ``chrome``.
    """
    if not needle:
        return []
    low = needle.lower()
    matches: list[ProcessInfo] = []
    for info in list_processes():
        if exact_name:
            if info.name.lower() == low or Path(info.executable).name.lower() == low:
                matches.append(info)
            continue
        if low in info.name.lower() or match_command_line and low in info.command_line.lower():
            matches.append(info)
    return matches
