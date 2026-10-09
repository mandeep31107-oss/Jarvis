"""Coding agent (spec section 24, backed by section 10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.agents.coding import CodeAgentSupport, PythonSandbox, detect_language
from jarvis.core.confidence import Claim, Confidence


class CodingAgent(Agent):
    name = "coding"
    description = "Reviews, checks, tests and sandbox-runs code. Never runs untrusted code unsandboxed."
    capabilities = ("code", "review", "debug", "test", "sandbox", "explain")

    def __init__(self, runtime: Any = None, *, support: CodeAgentSupport | None = None) -> None:
        super().__init__(runtime)
        self.support = support or CodeAgentSupport()

    def run(self, request: AgentRequest) -> AgentResult:
        mode = (request.param("mode") or "review").lower()
        source = request.param("source") or ""
        target = request.param("path")

        if mode == "test" and target:
            return self._run_tests(Path(target))
        if not source and target:
            path = Path(target)
            if not path.is_file():
                return AgentResult(agent=self.name, ok=False, summary=f"No such file: {path}")
            source = path.read_text(encoding="utf-8", errors="replace")
        if not source:
            return AgentResult(
                agent=self.name, ok=False, summary="No source supplied to review."
            )

        language = request.param("language") or detect_language(target or source)
        report = self.support.review_and_run(source, execute=mode in ("review", "run"))
        review = report["review"]
        result = AgentResult(
            agent=self.name,
            ok=review["syntax_ok"],
            summary=(
                f"{language}: syntax {'ok' if review['syntax_ok'] else 'FAILED'}, "
                f"{len(review['findings'])} finding(s)"
            ),
            data={"language": language, "report": report},
        )
        result.add_claim(
            Claim.certain(
                "The source parses without syntax errors."
                if review["syntax_ok"]
                else "The source has a syntax error: " + "; ".join(review["issues"]),
                reasoning="ast.parse on the submitted source",
            )
        )
        if review["risky"]:
            result.add_claim(
                Claim(
                    statement=(
                        "The code reaches for capabilities (network, processes, native calls) "
                        "that need explicit approval before execution."
                    ),
                    confidence=Confidence.HIGH,
                    caveats=["heuristic scan of imports and calls"],
                )
            )
            result.follow_ups.append(
                "Approve execution explicitly if these capabilities are intended and the code is yours."
            )
        execution = report.get("execution")
        if execution:
            result.add_claim(
                Claim.certain(
                    f"Sandboxed run finished with exit code {execution['returncode']} "
                    f"in {execution['duration_s']}s."
                    + (" Timed out." if execution["timed_out"] else ""),
                    reasoning="subprocess run with rlimits, scrubbed env and a timeout",
                )
            )
            if not execution["ok"] and execution["stderr"]:
                result.follow_ups.append("stderr: " + execution["stderr"].strip().splitlines()[-1][:200])
        elif report.get("note"):
            result.data["note"] = report["note"]
        return result

    def _run_tests(self, root: Path) -> AgentResult:
        if not root.exists():
            return AgentResult(agent=self.name, ok=False, summary=f"No such path: {root}")
        outcome = self.support.run_tests(root)
        result = AgentResult(
            agent=self.name,
            ok=outcome.ok,
            summary=(
                f"tests {'passed' if outcome.ok else 'FAILED'} (exit {outcome.returncode}) "
                f"in {outcome.duration_s:.1f}s"
            ),
            data={"execution": outcome.as_dict()},
        )
        result.add_claim(
            Claim.certain(
                f"The project's own test runner exited with code {outcome.returncode}.",
                reasoning="subprocess run of the repository's configured runner",
            )
        )
        if not outcome.ok:
            tail = (outcome.stdout or outcome.stderr or "").strip().splitlines()[-15:]
            result.follow_ups.append("last output lines:\n" + "\n".join(tail))
        return result

    def sandbox_limits(self) -> dict[str, Any]:
        sbx = self.support.sandbox
        return {
            "timeout_s": sbx.timeout_s,
            "cpu_seconds": sbx.cpu_seconds,
            "memory_mb": sbx.memory_mb,
            "max_file_mb": sbx.max_file_mb,
            "max_output_chars": sbx.max_output_chars,
            "rlimits_available": sbx.supports_rlimits,
            "note": (
                "Network is not blocked at kernel level in-process; run inside a container "
                "for a hard guarantee."
            ),
        }

    def new_sandbox(self, **kwargs: Any) -> PythonSandbox:
        return PythonSandbox(**kwargs)
