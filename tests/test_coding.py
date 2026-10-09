"""Coding intelligence and sandboxed execution (spec sections 10, 32)."""

from __future__ import annotations

import pytest

from jarvis.agents.base import AgentRequest
from jarvis.agents.coding import (
    CodeAgentSupport,
    CodeReviewer,
    PythonSandbox,
    detect_language,
)
from jarvis.agents.coding_agent import CodingAgent

CLEAN = """
def add(a, b):
    return a + b


print(add(2, 3))
"""

BROKEN = """
def add(a, b
    return a + b
"""

RISKY = """
import socket
import subprocess

subprocess.run(["ls"])
socket.create_connection(("example.com", 80))
"""


# ------------------------------------------------------------------ detection
@pytest.mark.parametrize(
    "text,expected",
    [
        ("main.py", "python"), ("app.tsx", "typescript"), ("index.html", "html"),
        ("style.css", "css"), ("main.cpp", "cpp"), ("App.java", "java"),
        ("run.rb", "ruby"), ("x.php", "php"), ("def f():\n    import os", "python"),
        ("public static void main(String[] a) {}", "java"),
        ("<?php echo 1;", "php"),
        ("function f() { const a = 1; }", "javascript"),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_unknown_input_is_reported_as_unknown():
    assert detect_language("!!!") == "unknown"


# -------------------------------------------------------------------- review
def test_clean_code_passes_syntax_check():
    review = CodeReviewer().review(CLEAN)
    assert review.syntax_ok
    assert review.issues == []
    assert review.metrics["functions"] == 1


def test_syntax_error_is_reported_with_line_and_column():
    review = CodeReviewer().review(BROKEN)
    assert not review.syntax_ok
    assert review.issues
    assert review.issues[0].line == 2
    assert "line 2" in review.issues[0].render()


def test_risky_imports_are_flagged():
    review = CodeReviewer().review(RISKY)
    assert review.risky
    rules = {f.rule for f in review.findings}
    assert "risky_import" in rules


def test_eval_and_exec_are_flagged():
    review = CodeReviewer().review("eval('1+1')\nexec('x=1')\n")
    assert {f.rule for f in review.findings} >= {"risky_call"}


def test_a_bare_except_is_flagged():
    review = CodeReviewer().review("try:\n    pass\nexcept:\n    pass\n")
    assert any(f.rule == "bare_except" for f in review.findings)


def test_none_comparison_is_flagged():
    review = CodeReviewer().review("x = None\nif x == None:\n    pass\n")
    assert any(f.rule == "none_compare" for f in review.findings)


def test_long_lines_and_todos_are_noted():
    long_line = "value = '" + "x" * 200 + "'"
    review = CodeReviewer().review(f"# TODO finish\n{long_line}\n")
    rules = {f.rule for f in review.findings}
    assert {"long_line", "todo"} <= rules


def test_non_python_code_is_not_pretended_to_be_reviewed():
    review = CodeReviewer().review("<html></html>", language="html")
    assert any(f.rule == "unsupported" for f in review.findings)


def test_render_summarises_the_review():
    text = CodeReviewer().review(BROKEN).render()
    assert "FAILED" in text


# ------------------------------------------------------------------- sandbox
def test_sandbox_runs_clean_code_and_captures_output():
    result = PythonSandbox(timeout_s=20).run("print('hello from the sandbox')")
    assert result.ok
    assert "hello from the sandbox" in result.stdout
    assert result.duration_s >= 0


def test_sandbox_reports_a_nonzero_exit():
    result = PythonSandbox(timeout_s=20).run("raise SystemExit(3)")
    assert not result.ok
    assert result.returncode == 3


def test_sandbox_captures_tracebacks():
    result = PythonSandbox(timeout_s=20).run("undefined_name()")
    assert not result.ok
    assert "NameError" in result.stderr


def test_sandbox_enforces_the_wall_clock_timeout():
    result = PythonSandbox(timeout_s=1).run("while True:\n    pass")
    assert result.timed_out is True
    assert "timeout" in result.killed_by.lower()


def test_sandbox_cannot_see_the_users_environment(tmp_path, monkeypatch):
    """Spec section 32: a snippet must not be able to read the user's secrets."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghijklmnopqrstuvwx")
    code = "import os, json; print(json.dumps(dict(os.environ)))"
    result = PythonSandbox(timeout_s=20).run(code)
    assert result.ok
    assert "OPENAI_API_KEY" not in result.stdout
    assert "sk-proj" not in result.stdout


def test_sandbox_runs_in_a_private_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marker.txt").write_text("outside", encoding="utf-8")
    result = PythonSandbox(timeout_s=20).run(
        "import os, pathlib;"
        "print(os.getcwd());"
        "print(pathlib.Path('marker.txt').exists())"
    )
    assert result.ok
    lines = result.stdout.strip().splitlines()
    assert str(tmp_path) not in lines[0]
    assert lines[1] == "False"


def test_sandbox_caps_output_size():
    result = PythonSandbox(timeout_s=20, max_output_chars=200).run("print('x' * 5000)")
    assert result.truncated is True
    assert "truncated" in result.stdout


def test_support_refuses_to_execute_risky_code():
    report = CodeAgentSupport().review_and_run(RISKY)
    assert report["execution"] is None
    assert "not executed" in report["note"]


def test_support_refuses_to_execute_code_that_does_not_parse():
    report = CodeAgentSupport().review_and_run(BROKEN)
    assert report["execution"] is None
    assert "does not parse" in report["note"]


def test_support_runs_clean_code():
    report = CodeAgentSupport().review_and_run(CLEAN)
    assert report["execution"]["ok"] is True
    assert report["execution"]["stdout"].strip() == "5"


# --------------------------------------------------------------------- agent
def test_coding_agent_reviews_source():
    result = CodingAgent().run(AgentRequest(intent="code", text="review", params={"source": CLEAN}))
    assert result.ok
    assert "syntax ok" in result.summary


def test_coding_agent_reports_a_syntax_error():
    result = CodingAgent().run(AgentRequest(intent="code", params={"source": BROKEN}))
    assert not result.ok
    assert any("syntax error" in c.statement.lower() for c in result.claims)


def test_coding_agent_reads_a_file(tmp_path):
    path = tmp_path / "m.py"
    path.write_text(CLEAN, encoding="utf-8")
    result = CodingAgent().run(AgentRequest(intent="code", params={"path": str(path)}))
    assert result.ok
    assert result.data["language"] == "python"


def test_coding_agent_reports_a_missing_file(tmp_path):
    result = CodingAgent().run(AgentRequest(intent="code", params={"path": str(tmp_path / "nope.py")}))
    assert not result.ok


def test_coding_agent_with_no_source_is_honest():
    result = CodingAgent().run(AgentRequest(intent="code"))
    assert not result.ok
    assert "No source supplied" in result.summary


def test_coding_agent_runs_the_projects_own_test_runner(tmp_path):
    """Spec: run the project's tests - never a re-implementation of them.

    Uses a throwaway project so the suite does not invoke itself.
    """
    (tmp_path / "test_sample.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    result = CodingAgent().run(
        AgentRequest(intent="code", params={"mode": "test", "path": str(tmp_path)})
    )
    assert "tests passed" in result.summary, result.summary
    assert result.data["execution"]["returncode"] == 0


def test_coding_agent_reports_a_failing_test_run(tmp_path):
    (tmp_path / "test_bad.py").write_text(
        "def test_wrong():\n    assert 1 + 1 == 3\n", encoding="utf-8"
    )
    result = CodingAgent().run(
        AgentRequest(intent="code", params={"mode": "test", "path": str(tmp_path)})
    )
    assert not result.ok
    assert "tests FAILED" in result.summary
    assert "assert 1 + 1 == 3" in "\n".join(result.follow_ups)


def test_coding_agent_reports_the_sandbox_limits():
    limits = CodingAgent().sandbox_limits()
    assert limits["timeout_s"] > 0
    assert limits["memory_mb"] > 0
    assert limits["rlimits_available"] is (hasattr(__import__("os"), "setrlimit"))


def test_run_tests_rejects_a_directory_with_no_tests(tmp_path):
    outcome = CodeAgentSupport().run_tests(tmp_path)
    assert not outcome.ok
    assert "no tests found" in outcome.stderr


def test_a_zero_test_run_is_not_reported_as_a_pass(tmp_path):
    """A file that contains no runnable tests must not come back green.

    ``unittest discover`` exits 0 in that situation, which is exactly the kind of
    clean-exit-code-but-wrong-output result that must be caught.
    """
    (tmp_path / "test_nothing.py").write_text("# no tests in here\n", encoding="utf-8")
    outcome = CodeAgentSupport().run_tests(tmp_path)
    assert not outcome.ok
    assert outcome.killed_by == "no tests collected"


def test_run_tests_rejects_a_missing_path(tmp_path):
    result = CodingAgent().run(
        AgentRequest(intent="code", params={"mode": "test", "path": str(tmp_path / "nope")})
    )
    assert not result.ok
    assert "No such path" in result.summary
