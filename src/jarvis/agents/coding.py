"""Coding intelligence (spec section 10) with sandboxed execution.

Two guarantees matter here:

* **Untrusted code never runs with the user's privileges or their environment.**
  :class:`PythonSandbox` runs code in a subprocess with a scrubbed environment,
  a private working directory, a wall-clock timeout, a CPU-time limit, an
  address-space limit, an output-size limit and no child processes.
* **Nothing is claimed about code that was not actually checked.** Syntax errors
  come from ``ast.parse``, not from pattern matching, and execution results come
  from a real interpreter run.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Modules a submitted snippet should not be reaching for in a sandbox.
RISKY_MODULES = {
    "socket": "network access",
    "http": "network access",
    "urllib": "network access",
    "requests": "network access",
    "subprocess": "spawning processes",
    "ctypes": "native code execution",
    "shutil": "bulk filesystem operations",
    "multiprocessing": "spawning processes",
    "telnetlib": "network access",
    "ftplib": "network access",
    "smtplib": "network access",
}

RISKY_CALLS = {
    "eval": "arbitrary expression evaluation",
    "exec": "arbitrary code execution",
    "compile": "arbitrary code execution",
    "__import__": "dynamic import",
    "input": "blocks on stdin",
    "breakpoint": "interactive debugger",
    "exit": "process control",
    "quit": "process control",
}


@dataclass
class SyntaxIssue:
    line: int
    column: int
    message: str
    severity: str = "error"

    def render(self) -> str:
        return f"{self.severity} at line {self.line}, column {self.column}: {self.message}"


@dataclass
class LintFinding:
    line: int
    rule: str
    message: str
    severity: str = "warning"

    def as_dict(self) -> dict[str, Any]:
        return {"line": self.line, "rule": self.rule, "message": self.message, "severity": self.severity}


@dataclass
class ExecutionResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    truncated: bool = False
    killed_by: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": round(self.duration_s, 3),
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "killed_by": self.killed_by,
        }


@dataclass
class CodeReview:
    language: str
    syntax_ok: bool
    issues: list[SyntaxIssue] = field(default_factory=list)
    findings: list[LintFinding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def risky(self) -> bool:
        return any(f.rule.startswith("risky") for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "syntax_ok": self.syntax_ok,
            "issues": [i.render() for i in self.issues],
            "findings": [f.as_dict() for f in self.findings],
            "metrics": self.metrics,
            "risky": self.risky,
        }

    def render(self) -> str:
        lines = [f"language: {self.language}   syntax: {'ok' if self.syntax_ok else 'FAILED'}"]
        lines.extend("  " + i.render() for i in self.issues)
        for f in self.findings:
            lines.append(f"  {f.severity} line {f.line} [{f.rule}] {f.message}")
        if self.metrics:
            lines.append("  metrics: " + ", ".join(f"{k}={v}" for k, v in sorted(self.metrics.items())))
        return "\n".join(lines)


def detect_language(filename_or_code: str) -> str:
    """Best-effort language detection from a filename extension, then from content."""
    name = filename_or_code.lower()
    ext_map = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".html": "html", ".htm": "html",
        ".css": "css", ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
        ".java": "java", ".php": "php", ".rb": "ruby", ".json": "json", ".md": "markdown",
        ".sh": "shell", ".sql": "sql",
    }
    for ext, lang in ext_map.items():
        if name.endswith(ext):
            return lang
    code = filename_or_code
    if code.lstrip().lower().startswith("<!doctype") or code.lstrip().lower().startswith("<html"):
        return "html"
    if re.search(r"(?m)^\s*(def\s+\w+\s*\(|class\s+\w+\s*[:(]|import\s+\w+|from\s+\S+\s+import\s)", code) \
            or re.search(r"(?m)^\s*print\s*\(", code):
        return "python"
    if "public static void main" in code:
        return "java"
    if "<?php" in code:
        return "php"
    if "function " in code or "const " in code:
        return "javascript"
    return "unknown"


class PythonSandbox:
    """Runs Python source in an isolated subprocess.

    Isolation applied (POSIX):

    * a fresh temporary working directory, deleted afterwards
    * a scrubbed environment: no inherited variables, so no API key can be read
      by the snippet
    * ``RLIMIT_CPU`` (CPU seconds), ``RLIMIT_AS`` (address space),
      ``RLIMIT_FSIZE`` (bytes per file) and ``RLIMIT_NPROC`` (no children)
    * a wall-clock timeout on the parent side
    * capped captured output

    Network is not blocked at the kernel level here - that needs a container or
    a network namespace. When a real guarantee is required, run this inside one;
    see ``jarvis.host`` for the container adapter hooks.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        cpu_seconds: int = 10,
        memory_mb: int = 512,
        max_file_mb: int = 8,
        max_output_chars: int = 20_000,
        allow_network: bool = False,
        python: str | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.cpu_seconds = cpu_seconds
        self.memory_mb = memory_mb
        self.max_file_mb = max_file_mb
        self.max_output_chars = max_output_chars
        self.allow_network = allow_network
        self.python = python or sys.executable
        self.supports_rlimits = hasattr(os, "setrlimit")

    def _preexec(self) -> None:  # pragma: no cover - runs in the child
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds + 1))
        resource.setrlimit(
            resource.RLIMIT_AS, (self.memory_mb * 1024 * 1024, self.memory_mb * 1024 * 1024)
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (self.max_file_mb * 1024 * 1024, self.max_file_mb * 1024 * 1024)
        )
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
        if hasattr(os, "setsid"):
            os.setsid()

    def run(self, source: str, *, args: Sequence[str] = ()) -> ExecutionResult:
        """Execute ``source`` and return everything the caller needs to judge it."""
        import time

        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="jarvis-sbx-") as tmp:
            script = Path(tmp) / "snippet.py"
            script.write_text(source, encoding="utf-8")
            env = {
                # Deliberately minimal: PATH so the interpreter works, and nothing else.
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "HOME": tmp,
                "TMPDIR": tmp,
            }
            kwargs: dict[str, Any] = {"cwd": tmp, "env": env, "stdin": subprocess.DEVNULL}
            if self.supports_rlimits:
                kwargs["preexec_fn"] = self._preexec
            try:
                proc = subprocess.run(
                    [self.python, "-I", "-S", "-E", str(script), *args],
                    capture_output=True,
                    timeout=self.timeout_s,
                    text=True,
                    **kwargs,
                )
            except subprocess.TimeoutExpired as exc:
                return ExecutionResult(
                    ok=False,
                    returncode=-1,
                    stdout=_cap(exc.stdout or "", self.max_output_chars),
                    stderr=_cap(exc.stderr or "", self.max_output_chars),
                    duration_s=time.monotonic() - start,
                    timed_out=True,
                    killed_by=f"wall-clock timeout after {self.timeout_s}s",
                )
            except OSError as exc:
                return ExecutionResult(
                    ok=False,
                    returncode=-1,
                    stdout="",
                    stderr=str(exc),
                    duration_s=time.monotonic() - start,
                    killed_by=f"could not start the interpreter: {exc}",
                )

        duration = time.monotonic() - start
        stdout = _cap(proc.stdout or "", self.max_output_chars)
        stderr = _cap(proc.stderr or "", self.max_output_chars)
        return ExecutionResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration,
            truncated=len(proc.stdout or "") > self.max_output_chars,
        )


def _cap(text: str, limit: int) -> str:
    if isinstance(text, bytes):  # pragma: no cover - defensive
        text = text.decode("utf-8", "replace")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [output truncated at {limit} characters]"


class CodeReviewer:
    """Static review for Python source: syntax via the real parser, plus heuristics."""

    def review(self, source: str, *, language: str | None = None) -> CodeReview:
        lang = language or detect_language(source)
        if lang == "unknown":
            # Content heuristics are unreliable for tiny snippets. If the text
            # parses as Python, treat it as Python - that is evidence, not a guess.
            try:
                ast.parse(source)
            except SyntaxError:
                pass
            else:
                lang = "python"
        review = CodeReview(language=lang, syntax_ok=True)
        review.metrics = self._metrics(source)
        if lang != "python":
            review.findings.append(
                LintFinding(0, "unsupported", f"Deep review is only implemented for Python (got {lang}).",
                            severity="info")
            )
            return review
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            review.syntax_ok = False
            review.issues.append(
                SyntaxIssue(exc.lineno or 0, (exc.offset or 0), exc.msg or "syntax error")
            )
            return review
        review.findings.extend(self._walk(tree, source))
        return review

    def _walk(self, tree: ast.AST, source: str) -> list[LintFinding]:
        findings: list[LintFinding] = []
        lines = source.splitlines()
        for node in ast.walk(tree):
            line = getattr(node, "lineno", 0)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in RISKY_MODULES:
                        findings.append(
                            LintFinding(line, "risky_import",
                                        f"imports '{alias.name}' ({RISKY_MODULES[root]})")
                        )
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in RISKY_MODULES:
                    findings.append(
                        LintFinding(line, "risky_import",
                                    f"imports from '{node.module}' ({RISKY_MODULES[root]})")
                    )
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in RISKY_CALLS:
                    findings.append(
                        LintFinding(line, "risky_call", f"calls '{name}' ({RISKY_CALLS[name]})")
                    )
                if name in {"open", "Path.open"} and len(node.args) >= 2:
                    mode = node.args[1]
                    if (
                        isinstance(mode, ast.Constant)
                        and isinstance(mode.value, str)
                        and any(c in mode.value for c in "wax+")
                    ):
                        findings.append(
                            LintFinding(line, "filesystem_write",
                                        f"opens a file in '{mode.value}' mode")
                        )
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                findings.append(LintFinding(line, "bare_except", "bare 'except:' swallows every error"))
            elif isinstance(node, ast.Compare) and any(
                isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(other, ast.Constant) and other.value is None
                for op, other in zip(node.ops, node.comparators, strict=False)
            ):
                findings.append(LintFinding(line, "none_compare", "compare with None using 'is' / 'is not'"))

        for i, text in enumerate(lines, start=1):
            stripped = text.rstrip()
            if len(stripped) > 120:
                findings.append(LintFinding(i, "long_line", f"line is {len(stripped)} characters"))
            if "TODO" in text or "FIXME" in text:
                findings.append(LintFinding(i, "todo", "unfinished work marked in the source", severity="info"))
            if "\t" in text and "    " in text:
                findings.append(LintFinding(i, "mixed_indent", "tabs and spaces mixed on one line"))
        return findings

    @staticmethod
    def _metrics(source: str) -> dict[str, Any]:
        lines = source.splitlines()
        code_lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
        try:
            tree = ast.parse(source)
            functions = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
            classes = sum(isinstance(n, ast.ClassDef) for n in ast.walk(tree))
        except SyntaxError:
            functions = classes = 0
        return {
            "lines": len(lines),
            "code_lines": len(code_lines),
            "blank_lines": len(lines) - len(code_lines),
            "functions": functions,
            "classes": classes,
            "chars": len(source),
        }


def _module_available(name: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        value = _call_name(func.value)
        return f"{value}.{func.attr}" if value else func.attr
    return ""


class CodeAgentSupport:
    """Small helpers the CodingAgent exposes to the rest of the system."""

    def __init__(self, sandbox: PythonSandbox | None = None, reviewer: CodeReviewer | None = None) -> None:
        self.sandbox = sandbox or PythonSandbox()
        self.reviewer = reviewer or CodeReviewer()

    def review_and_run(self, source: str, *, execute: bool = True) -> dict[str, Any]:
        review = self.reviewer.review(source)
        out: dict[str, Any] = {"review": review.as_dict()}
        if not execute:
            return out
        if not review.syntax_ok:
            out["execution"] = None
            out["note"] = "not executed - the code does not parse"
            return out
        if review.risky:
            out["execution"] = None
            out["note"] = (
                "not executed - the code reaches for capabilities that need explicit approval "
                f"({', '.join(sorted({f.rule for f in review.findings if f.rule.startswith('risky')}))})"
            )
            return out
        out["execution"] = self.sandbox.run(source).as_dict()
        return out

    #: Output that means "nothing actually ran", even though the exit code was 0.
    #: A green exit code is not a pass when no test executed.
    EMPTY_RUN_MARKERS = (
        "no tests ran",
        "collected 0 items",
        "ran 0 tests",
        "no tests found",
    )

    def run_tests(self, path: str | Path, *, timeout_s: float = 120.0) -> ExecutionResult:
        """Run the project's own test runner. Never a re-implementation of it.

        Prefers the runner the project configured, and treats a zero-test run as
        a failure: ``unittest discover`` exits 0 when it finds no TestCase
        classes, which would otherwise report "tests passed" for a project whose
        tests never ran.
        """
        import time

        root = Path(path).resolve()
        start = time.monotonic()
        configured = (
            (root / "pyproject.toml").is_file()
            or (root / "pytest.ini").is_file()
            or (root / "setup.cfg").is_file()
            or (root / "tests").is_dir()
        )
        has_test_files = bool(list(root.glob("test_*.py")) or list(root.glob("*_test.py")))
        pytest_available = _module_available("pytest")

        if configured and pytest_available:
            cmd = [sys.executable, "-m", "pytest", "-q"]
        elif configured or has_test_files:
            cmd = (
                [sys.executable, "-m", "pytest", "-q"]
                if pytest_available
                else [sys.executable, "-m", "unittest", "discover", "-s", str(root), "-p", "test_*.py"]
            )
        else:
            return ExecutionResult(
                ok=False,
                returncode=2,
                stdout="",
                stderr=(
                    "no tests found (expected pyproject.toml, pytest.ini, a tests/ "
                    "directory, or test_*.py files)"
                ),
                duration_s=0.0,
                killed_by="no test configuration",
            )
        try:
            proc = subprocess.run(
                cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                ok=False, returncode=-1, stdout="", stderr=f"tests exceeded {timeout_s}s",
                duration_s=time.monotonic() - start, timed_out=True,
                killed_by=f"timeout after {timeout_s}s",
            )

        stdout = _cap(proc.stdout, 20_000)
        stderr = _cap(proc.stderr, 20_000)
        combined = f"{stdout}\n{stderr}".lower()
        empty_run = any(marker in combined for marker in self.EMPTY_RUN_MARKERS)
        ok = proc.returncode == 0 and not empty_run
        return ExecutionResult(
            ok=ok,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr
            + ("\n[no tests were collected - a zero-test run is not a pass]" if empty_run else ""),
            duration_s=time.monotonic() - start,
            killed_by="no tests collected" if empty_run else "",
        )


def summarise_json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def dedent(source: str) -> str:
    return textwrap.dedent(source)
