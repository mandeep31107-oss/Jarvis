"""Tests for the /proc process reader and the host adapter wired to it.

These run against the real process table of the machine executing them. That is
the point: the values are cross-checked against ``ps``, which reads ``/proc`` by
its own route, so agreement is evidence rather than my code agreeing with itself.

Processes appear and disappear while the table is walked, so nothing here asserts
an exact PID or a fixed count.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from jarvis.errors import CapabilityUnavailable
from jarvis.host import detect_host
from jarvis.host.base import _missing_display_reason
from jarvis.host.procfs import (
    FIELDS,
    STATES,
    ProcessInfo,
    _split_stat,
    find,
    list_processes,
    proc_available,
    read_process,
)

HAS_PS = shutil.which("ps") is not None


# --------------------------------------------------------------- stat parsing


def test_a_command_name_containing_spaces_is_parsed_correctly() -> None:
    """/proc/<pid>/stat puts comm in parens, and it may contain spaces.

    Splitting on whitespace alone shifts every later field, which would surface
    as nonsense states and parent PIDs rather than an error.
    """
    fields = _split_stat("4242 (Web Content) S 100 4242 4242 0 -1 4194560")

    assert fields[0] == "4242"
    assert fields[1] == "(Web Content)"
    assert fields[2] == "S"
    assert fields[3] == "100"


def test_a_command_name_containing_parentheses_is_parsed_correctly() -> None:
    """The last ')' closes the name, not the first."""
    fields = _split_stat("99 (weird ) ( name) R 1 99 99 0 -1 0")

    assert fields[1] == "(weird ) ( name)"
    assert fields[2] == "R"
    assert fields[3] == "1"


def test_a_malformed_stat_line_does_not_crash() -> None:
    assert _split_stat("") == []
    assert _split_stat("no parens here") == ["no", "parens", "here"]


# ----------------------------------------------------------------- read_process


def test_this_process_can_be_read_back() -> None:
    info = read_process(os.getpid())

    assert info is not None
    assert info.pid == os.getpid()
    assert info.name, "the process name must be populated"
    assert info.state in STATES
    assert info.parent > 0
    assert info.owner == os.getuid()


def test_cpu_time_is_reported_in_seconds_not_clock_ticks() -> None:
    """utime/stime are ticks; reporting them raw overstates CPU 100-fold."""
    info = read_process(os.getpid())

    assert info is not None
    assert 0.0 <= info.cpu_seconds < 3600.0, "seconds, not raw ticks"


def test_memory_is_marked_unavailable_when_it_cannot_be_read() -> None:
    """Zero can mean 'not resident' or 'unreadable'; the flag says which."""
    info = ProcessInfo(pid=1, name="x")

    assert info.memory_kb == 0
    assert info.memory_available is False
    assert info.as_dict()["memory_kb"] is None


def test_a_pid_that_does_not_exist_returns_none() -> None:
    """A vanished process is an expected outcome, not an exception."""
    assert read_process(4_000_000) is None


def test_a_racing_process_disappearing_mid_read_does_not_raise() -> None:
    """Processes exit while the table is walked; the reader must cope."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = child.pid
    child.wait()

    # It may still have a /proc entry as a zombie, or none at all.
    result = read_process(pid)
    assert result is None or result.pid == pid


def test_unknown_state_letters_are_passed_through_rather_than_invented() -> None:
    info = ProcessInfo(pid=1, state="Q")

    assert info.state_text == "Q"


def test_a_process_without_arguments_reports_its_name_as_the_executable() -> None:
    info = ProcessInfo(pid=1, name="kthreadd")

    assert info.arguments == []
    assert info.executable == "kthreadd"


# ------------------------------------------------------------- list_processes


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_the_process_table_is_non_empty_and_sorted() -> None:
    processes = list_processes()

    assert processes, "something must be running"
    pids = [p.pid for p in processes]
    assert pids == sorted(pids)
    assert len(set(pids)) == len(pids), "no duplicate PIDs"


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_kernel_threads_are_excluded_unless_asked_for() -> None:
    """They have no command line and would bury the applications users ask about."""
    userland = list_processes()
    everything = list_processes(include_kernel_threads=True)

    assert len(everything) > len(userland)
    assert all(p.arguments for p in userland)


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_the_owner_filter_selects_only_that_user() -> None:
    mine = list_processes(owner=os.getuid())

    assert mine, "this process at least must be owned by us"
    assert all(p.owner == os.getuid() for p in mine)


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_processes_serialise_to_json_safe_dicts() -> None:
    import json

    payload = [p.as_dict() for p in list_processes()[:5]]

    json.dumps(payload)
    assert set(FIELDS) <= set(payload[0]) | {"state_text"}


@pytest.mark.skipif(not HAS_PS or not proc_available(), reason="needs ps and /proc")
def test_names_and_parents_agree_with_ps() -> None:
    """The real cross-check: ps reads /proc by its own route."""
    output = subprocess.run(
        ["ps", "-eo", "pid=,comm=,ppid="], capture_output=True, text=True, check=True
    ).stdout
    expected: dict[int, tuple[str, int]] = {}
    for line in output.strip().splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[2].isdigit():
            expected[int(parts[0])] = (parts[1], int(parts[2]))

    mine = {p.pid: p for p in list_processes(include_kernel_threads=True)}
    shared = sorted(set(mine) & set(expected))
    assert len(shared) > 3, "the two views must overlap substantially"

    #: ps truncates comm to 15 characters in column layout, while
    #: /proc/<pid>/comm holds the full name (27 characters for
    #: "kworker/0:0H-events_highpri"). The reader is faithful to the source, so
    #: the comparison is prefix-based rather than exact.
    name_mismatches = [
        pid
        for pid in shared
        if not mine[pid].name.startswith(expected[pid][0][:15])
    ]
    parent_mismatches = [pid for pid in shared if mine[pid].parent != expected[pid][1]]

    assert name_mismatches == [], f"name mismatches: {name_mismatches[:5]}"
    assert parent_mismatches == [], f"parent mismatches: {parent_mismatches[:5]}"


# ----------------------------------------------------------------------- find


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_find_locates_this_interpreter() -> None:
    marker = f"jarvis-find-test-{os.getpid()}"
    child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"])
    try:
        time.sleep(0.4)

        matches = find(marker)

        assert any(p.pid == child.pid for p in matches)
    finally:
        child.terminate()
        child.wait(timeout=10)


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_find_is_case_insensitive() -> None:
    """Compared against one snapshot: two separate calls would race the live table."""
    needle = f"case-check-{os.getpid()}"
    child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {needle}"])
    try:
        time.sleep(0.4)

        lower = find(needle.lower())
        upper = find(needle.upper())

        assert any(p.pid == child.pid for p in lower)
        assert [p.pid for p in lower] == [p.pid for p in upper]
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_an_empty_needle_matches_nothing() -> None:
    """Matching every process would be a footgun, not a feature."""
    assert find("") == []


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_exact_name_matching_ignores_the_command_line() -> None:
    marker = f"exact-name-test-{os.getpid()}"
    child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"])
    try:
        time.sleep(0.4)

        assert find(marker) != []
        assert find(marker, exact_name=True) == []
    finally:
        child.terminate()
        child.wait(timeout=10)


# ------------------------------------------------------------- host adapter


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_the_host_adapter_exposes_the_process_table() -> None:
    host = detect_host(computer_use_enabled=True)

    processes = host.list_processes()

    assert any(p.pid == os.getpid() for p in processes)


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_listing_processes_works_even_with_computer_use_disabled() -> None:
    """Observing is not controlling; disabling control must not blind Jarvis."""
    host = detect_host(computer_use_enabled=False)

    assert host.list_processes(), "reading the process table needs no computer-use switch"


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_list_apps_reports_a_real_running_state() -> None:
    """The field used to be hardcoded False, which made it a lie."""
    host = detect_host(computer_use_enabled=True)
    marker = f"app-running-check-{os.getpid()}"
    child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"])
    try:
        time.sleep(0.4)

        apps = host.list_apps()
        running = {a.name for a in apps if a.running}

        assert child.pid in {a.pid for a in apps if a.pid is not None}
        assert any("python" in name.lower() for name in running)
    finally:
        child.terminate()
        child.wait(timeout=10)


# --------------------------------------------------------- honest capability


def test_a_headless_host_names_the_missing_display_server(monkeypatch) -> None:
    """'Not implemented' leaves the user unable to tell refusal from inability."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    reason = _missing_display_reason()

    assert "no display server" in reason
    assert "DISPLAY" in reason


def test_a_host_with_a_display_reports_no_gap(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", ":0")

    assert _missing_display_reason() == ""


@pytest.mark.skipif(not proc_available(), reason="/proc is not available on this host")
def test_the_screenshot_refusal_explains_why(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    host = detect_host(computer_use_enabled=True)

    with pytest.raises(CapabilityUnavailable) as excinfo:
        host.screenshot()

    message = str(excinfo.value)
    assert "display server" in message
    #: The separator must be there - a previous version produced "linuxneeds".
    assert "linuxneeds" not in message


def test_the_capability_list_and_the_exception_never_disagree(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    host = detect_host(computer_use_enabled=True)

    listed = {c.name: c.reason for c in host.capabilities()}
    with pytest.raises(CapabilityUnavailable) as excinfo:
        host.screenshot()

    assert listed["screenshot"] in str(excinfo.value)


def test_disabling_computer_use_still_explains_itself() -> None:
    host = detect_host(computer_use_enabled=False)

    with pytest.raises(CapabilityUnavailable, match="disabled in settings"):
        host.open_app("anything")
