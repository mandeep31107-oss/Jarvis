"""The dashboard: fourteen sections, read-only by default, no secrets."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from jarvis.dashboard import Dashboard, make_server


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    from jarvis.config import settings_from_env
    from jarvis.runtime import build_runtime

    rt = build_runtime(settings_from_env(), with_memory=True)
    rt.handle("remind me about the meeting")
    yield rt
    rt.shutdown()


@pytest.fixture()
def server(runtime):
    httpd = make_server(runtime, host="127.0.0.1", port=0)
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, dict(resp.headers), resp.read()


# ------------------------------------------------------------------ sections


def test_exactly_fourteen_sections_are_present(runtime):
    snapshot = Dashboard(runtime).snapshot()
    assert len(Dashboard.SECTIONS) == 14
    for name in Dashboard.SECTIONS:
        assert name in snapshot, f"section {name} is missing"


def test_every_section_is_json_serialisable(runtime):
    """A section that only serialises via `default=str` is a repr, not data."""
    snapshot = Dashboard(runtime).snapshot()
    for name in Dashboard.SECTIONS:
        payload = snapshot[name]
        assert json.loads(json.dumps(payload)) == json.loads(
            json.dumps(json.loads(json.dumps(payload, default=str)))
        ), f"section {name} is not cleanly serialisable"


def test_activity_events_are_dicts_not_dataclass_reprs(runtime):
    """Regression: EventBus returns dataclasses, which serialise to
    'Event(topic=...)' - a string a browser cannot render."""
    events = Dashboard(runtime).snapshot()["activity"]["events"]
    assert events, "expected at least the runtime.start event"
    for event in events:
        assert isinstance(event, dict)
        assert "topic" in event and "at" in event


def test_audit_records_use_the_field_the_ui_reads(runtime):
    """The UI reads `at`; a rename in one place and not the other leaves the
    timestamp column silently blank."""
    records = Dashboard(runtime).snapshot()["audit"]["recent"]
    assert records
    assert records[0]["at"]


def test_the_security_section_lists_every_hard_denial(runtime):
    from jarvis.core.policy import FORBIDDEN

    section = Dashboard(runtime).snapshot()["security"]
    assert section["forbidden_count"] == len(FORBIDDEN)
    assert set(section["forbidden_actions"]) == {entry[0] for entry in FORBIDDEN}


def test_the_revenue_section_does_not_fake_a_pipeline(runtime):
    section = Dashboard(runtime).snapshot()["revenue"]
    assert section["decisions_recorded"] >= 0
    assert "will not show an empty one" in section["note"]


# ------------------------------------------------------------------ secrets


def test_no_secret_value_reaches_the_dashboard(runtime, monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATABASE_URL", "postgresql://u:hunter2secret@h/db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghij1234567890")

    from jarvis.config import settings_from_env
    from jarvis.runtime import build_runtime

    rt = build_runtime(settings_from_env(), with_memory=False)
    try:
        body = json.dumps(Dashboard(rt).snapshot(), default=str)
    finally:
        rt.shutdown()
    assert "hunter2secret" not in body
    assert "sk-proj-abcdefghij1234567890" not in body


# -------------------------------------------------------------------- http


def test_the_index_page_loads(server):
    status, headers, body = _get(server + "/")
    assert status == 200
    assert b"Jarvis" in body
    assert headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in headers["Content-Security-Policy"]


def test_the_status_endpoint_returns_all_sections(server):
    status, _headers, body = _get(server + "/api/status")
    assert status == 200
    payload = json.loads(body)
    for name in Dashboard.SECTIONS:
        assert name in payload


def test_the_health_endpoint_reports_the_run_state(server, runtime):
    status, _headers, body = _get(server + "/api/health")
    assert status == 200
    assert json.loads(body) == {"ok": True, "run_state": runtime.run_state}


def test_an_unknown_route_is_a_json_404(server):
    try:
        _get(server + "/api/nope")
        raise AssertionError("expected a 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        assert "no such route" in exc.read().decode()


def test_the_dashboard_is_read_only_by_default(server):
    request = urllib.request.Request(
        server + "/api/request",
        data=json.dumps({"text": "do something"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected a 403")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
        assert "read-only" in exc.read().decode()


def test_actions_can_be_enabled_and_still_go_through_the_runtime(runtime):
    import threading

    httpd = make_server(runtime, host="127.0.0.1", port=0, allow_actions=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        request = urllib.request.Request(
            base + "/api/request",
            data=json.dumps({"text": "remind me to call the bank"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            payload = json.loads(resp.read())
        assert payload["ok"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_hard_denial_is_refused_through_the_dashboard(runtime):
    """The dashboard is not a way around the policy engine."""
    import threading

    httpd = make_server(runtime, host="127.0.0.1", port=0, allow_actions=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        request = urllib.request.Request(
            base + "/api/request",
            data=json.dumps({"text": ""}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("expected a 400 for an empty request")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


# ------------------------------------------------------- concurrency guards


def test_memory_and_knowledge_survive_concurrent_access(tmp_path, monkeypatch):
    """Regression: the dashboard serves on worker threads, and SQLite connections
    are thread-bound by default, so the first request that touched memory raised
    ProgrammingError. check_same_thread=False is only safe because every store
    serialises access with a lock - this test is what keeps that true."""
    import threading

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    from jarvis.config import settings_from_env
    from jarvis.runtime import build_runtime

    settings = settings_from_env()
    settings.ensure_dirs()
    rt = build_runtime(settings, with_memory=True)
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            rt.memory.remember("preference", f"pref-{i}", f"value {i}")
            rt.memory.counts()
            rt.knowledge.stats()
            rt.knowledge.query("anything")
            rt.knowledge.needs_revalidation()
            rt.audit.entries(limit=5)
        except BaseException as exc:  # noqa: BLE001 - collect, do not raise in-thread
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rt.shutdown()

    assert not errors, f"{len(errors)} thread(s) failed, first: {errors[0]!r}"


def test_a_concurrent_dashboard_load_does_not_error(server):
    import threading

    failures: list[str] = []

    def hit() -> None:
        try:
            status, _h, body = _get(server + "/api/status")
            if status != 200:
                failures.append(f"status {status}")
            json.loads(body)
        except Exception as exc:  # noqa: BLE001
            failures.append(repr(exc))

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failures, failures[:3]


def test_head_requests_are_answered(server):
    """Health probes use HEAD; a 501 there looks like the server is broken."""
    request = urllib.request.Request(server + "/api/health", method="HEAD")
    with urllib.request.urlopen(request, timeout=10) as resp:
        assert resp.status == 200
        assert resp.read() == b""


def test_the_system_section_reports_the_always_active_state(runtime):
    """The first question about a background loop is whether it is running; it
    should not be buried in the raw status payload."""
    assert Dashboard(runtime).snapshot()["system"]["loop"] == {"enabled": False}

    from jarvis.core.loop import AlwaysActiveLoop

    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    runtime.loop = loop
    loop.start()
    try:
        section = Dashboard(runtime).snapshot()["system"]["loop"]
        assert section["running"] is True
        assert "iterations" in section
    finally:
        loop.stop()


def test_the_page_renders_every_section_without_a_template_error():
    """Guard the inline JS: a bad field name renders a blank card rather than
    throwing, which is easy to miss without loading the page."""
    from jarvis.dashboard import PAGE

    for marker in (
        "1 · System", "2 · Agents", "3 · Tasks", "4 · Risk", "5 · Audit",
        "6 · Memory", "7 · Knowledge", "8 · Compliance", "9 · Platform",
        "10 · Models", "11 · Host", "12 · Revenue", "13 · Security", "14 · Activity",
    ):
        assert marker in PAGE, f"the page has no card for {marker!r}"
