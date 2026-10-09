"""Text interface (Phase 1 of spec section 38).

A REPL plus non-interactive subcommands, so the same code path serves a human at
a terminal and a script in CI. Approvals for MEDIUM+ risk actions are asked here
through the policy engine -- the CLI is not a separate trust boundary.

    jarvis                     # interactive REPL
    jarvis run "explain X"     # one-shot
    jarvis status              # system status
    jarvis audit 20            # last 20 audit records
    jarvis revenue "idea"      # opportunity analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jarvis.core.audit import summarize
from jarvis.core.options import Dilemma
from jarvis.core.policy import CallbackApprover, PolicyDecision
from jarvis.core.risk import ActionRequest
from jarvis.core.supervisor import Execution
from jarvis.interaction.intents import describe_intents
from jarvis.runtime import Runtime, build_runtime, quick_status
from jarvis.util import redaction
from jarvis.version import PHASE, PHASE_NAME, __version__

BANNER = rf"""
     __                     _
    / /__  _______ _______ (_)___
   / / _ \/ __/ _ `\ V(_-< / (_-<
  /_/_//_/_/  \_,_/_/___/_/ /___/   v{__version__} - phase {PHASE}: {PHASE_NAME}

  SAFE - LEGAL - TRANSPARENT - USER-CONTROLLED - AUDITABLE
  Type /help for commands. /stop halts everything, always.
"""


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
def _print(text: str = "") -> None:
    print(text, flush=True)


def _render_execution(execution: Execution) -> None:
    result = execution.result
    if execution.error:
        _print(f"\n[blocked] {execution.error}")
    if result is None:
        if not execution.error:
            _print("(no result)")
        return
    _print(f"\n[{result.agent}] {result.summary}")
    for claim in result.claims:
        _print(f"  - {claim.render()}")
    if result.follow_ups:
        _print("  next:")
        for line in result.follow_ups:
            _print(f"    * {line}")
    for dilemma in result.dilemmas:
        _print()
        _print(dilemma.render())
    if not execution.verified and execution.verification_notes:
        _print("  verification: " + "; ".join(execution.verification_notes))


def _print_dilemma(dilemma: Dilemma) -> None:
    _print()
    _print(dilemma.render())


# --------------------------------------------------------------------------- #
# REPL
# --------------------------------------------------------------------------- #
class Repl:
    """Interactive session. Every command maps onto the same runtime API."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        runtime.set_approver(CallbackApprover(self._ask_approval))

    def _ask_approval(self, decision: PolicyDecision) -> bool:
        _print()
        _print("=" * 72)
        _print("APPROVAL REQUIRED")
        _print(f"  action : {decision.action.describe()}")
        _print(f"  risk   : {decision.assessment.level.value.upper()} "
               f"({decision.assessment.category.value})")
        _print(f"  why    : {decision.reason}")
        for reason in decision.assessment.reasons():
            _print(f"           {reason}")
        _print("=" * 72)
        try:
            answer = input("  Allow this action? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}

    # -- entry ---------------------------------------------------------------
    def loop(self) -> int:
        _print(BANNER)
        _print(quick_status(self.runtime))
        _print()
        while True:
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                _print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    break
                continue
            execution = self.runtime.handle(line)
            _render_execution(execution)
        self.runtime.shutdown()
        return 0

    # -- commands ------------------------------------------------------------
    def command(self, line: str) -> bool:
        """Handle a slash command. Returns False to exit."""
        parts = line[1:].split(maxsplit=1)
        name = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "help": self.cmd_help, "status": self.cmd_status, "agents": self.cmd_agents,
            "audit": self.cmd_audit, "risk": self.cmd_risk, "revenue": self.cmd_revenue,
            "compliance": self.cmd_compliance, "terms": self.cmd_terms, "models": self.cmd_models,
            "lang": self.cmd_lang, "memory": self.cmd_memory, "tasks": self.cmd_tasks,
            "privacy": self.cmd_privacy, "camera": self.cmd_camera, "lock": self.cmd_lock,
            "pause": self.cmd_pause, "resume": self.cmd_resume, "stop": self.cmd_stop,
            "build": self.cmd_build, "doc": self.cmd_doc, "consent": self.cmd_consent,
            "autostart": self.cmd_autostart, "activity": self.cmd_activity,
            "config": self.cmd_config, "read": self.cmd_read,
            "quit": lambda _a: False, "exit": lambda _a: False,
        }
        handler = handlers.get(name)
        if handler is None:
            _print(f"Unknown command '/{name}'. Try /help.")
            return True
        try:
            result = handler(argument)
        except Exception as exc:  # noqa: BLE001 - the REPL must survive any command
            _print(f"[error] {type(exc).__name__}: {exc}")
            return True
        return True if result is None else bool(result)

    def cmd_help(self, _arg: str) -> None:
        _print("""
COMMANDS
  /status                     system status
  /agents                     registered agents and their availability
  /activity                   what the agents did most recently
  /audit [n]                  last n audit records (default 15)
  /risk <verb> [target]       risk-score an action without running it
  /revenue <idea>             full opportunity analysis + options
  /compliance <markets>       jurisdiction brief (e.g. "IN,US")
  /terms <platform> <acts>    platform policy check, e.g. "/terms whatsapp bulk_messaging"
  /build <category> <path>    scaffold a site, e.g. "/build ecommerce ./shop"
  /doc <format> <title>       create a document (xlsx|csv|docx|pdf|md)
  /memory search|add|forget|export|counts [args]
  /tasks                      task queue, priorities and blockers
  /models                     model registry and router health
  /lang <text>                show language detection for some text
  /consent <camera|microphone|screen>   grant a capture device (revocable)
  /privacy on|off             PRIVACY MODE - all capture off
  /camera on|off              camera indicator control
  /lock                       lock the device and pause the agent
  /pause  /resume             pause / resume background work
  /stop                       EMERGENCY STOP - always available
  /autostart                  how to start Jarvis with the OS
  /help                       this text

Anything without a slash is treated as a normal request.
""")
        _print("Recognised intents: " + ", ".join(sorted(describe_intents())))

    def cmd_status(self, _arg: str) -> None:
        _print(quick_status(self.runtime))

    def cmd_agents(self, _arg: str) -> None:
        for name, info in sorted(self.runtime.registry.describe().items()):
            flag = "ready" if info["available"] else f"unavailable ({info['reason']})"
            _print(f"  {name:<14} {flag}")
            _print(f"                 {info['description']}")

    def cmd_activity(self, _arg: str) -> None:
        events = self.runtime.events.recent(12, topic="activity")
        if not events:
            _print("(no activity yet)")
            return
        for event in events:
            _print(f"  {event.at[11:19]}  {event.payload.get('agent', '-'):<12} "
                   f"{event.payload.get('message', '')}")

    def cmd_audit(self, arg: str) -> None:
        limit = int(arg) if arg.strip() else 15
        _print(summarize(self.runtime.audit.entries(limit=limit)))
        _print(f"\n{self.runtime.audit.stats()}")

    def cmd_risk(self, arg: str) -> None:
        if not arg:
            _print("usage: /risk <verb> [target]")
            return
        bits = arg.split(maxsplit=1)
        action = ActionRequest(verb=bits[0], target=bits[1] if len(bits) > 1 else "")
        _print(self.runtime.risk.explain(action))
        decision = self.runtime.policy.decide(action)
        _print(f"\npolicy decision: {decision.decision} - {decision.reason}")
        if decision.safer_alternatives:
            _print("safer alternatives:")
            for alternative in decision.safer_alternatives:
                _print(f"  - {alternative}")

    def cmd_revenue(self, arg: str) -> None:
        if not arg:
            _print("usage: /revenue <short description of the idea>")
            return
        from jarvis.agents.revenue import Opportunity

        opportunity = Opportunity(title=arg[:120], description=arg)
        execution = self.runtime.handle(
            arg, intent="revenue", opportunity=opportunity
        )
        _render_execution(execution)

    def cmd_compliance(self, arg: str) -> None:
        markets = [m.strip().upper() for m in (arg or self.runtime.settings.jurisdiction).split(",") if m.strip()]
        agent = self.runtime.registry.get("compliance")
        if agent is None:
            _print("compliance agent unavailable")
            return
        _print(agent.brief_text("the described activity", markets))
        queue = agent.verification_queue()
        _print(f"\nNeeds verification: {len(queue['regulations'])} regulation(s), "
               f"{len(queue['platforms'])} platform polic(y/ies).")

    def cmd_terms(self, arg: str) -> None:
        bits = arg.split()
        if not bits:
            _print("usage: /terms <platform> [activity ...]")
            _print("known platforms: " + ", ".join(p.id for p in self.runtime.terms.platforms))
            return
        platform, activities = bits[0], bits[1:] or ["api_automation"]
        _print(self.runtime.terms.evaluate_plan(platform, activities).render())

    def cmd_models(self, _arg: str) -> None:
        _print(self.runtime.router.render())
        _print()
        _print(json.dumps(self.runtime.router.health(), indent=2, default=str))

    def cmd_lang(self, arg: str) -> None:
        from jarvis.interaction.lang import detect, respond_in

        if not arg:
            _print("usage: /lang <text>")
            return
        result = detect(arg)
        _print(f"detected : {result}")
        _print(f"evidence : {'; '.join(result.evidence)}")
        _print(f"reply in : {respond_in(arg, self.runtime.settings.language_policy)}")

    def cmd_memory(self, arg: str) -> None:
        bits = arg.split(maxsplit=1)
        sub = bits[0].lower() if bits else "counts"
        rest = bits[1] if len(bits) > 1 else ""
        memory = self.runtime.memory
        if memory is None:
            _print("memory is disabled")
            return
        if sub == "counts":
            _print(json.dumps(memory.counts(), indent=2))
        elif sub == "search":
            _print(memory.render(memory.search(rest, limit=15)))
        elif sub == "add":
            record = memory.remember("knowledge", rest[:120] or "note", rest, source="cli")
            _print(f"stored {record.id}")
        elif sub == "forget":
            _print(f"forgot {memory.forget_matching(rest)} record(s)")
        elif sub == "export":
            destination = Path(rest or (self.runtime.settings.home / "exports" / "memory.json"))
            _print(f"exported to {memory.export(destination)}")
        elif sub == "disable":
            memory.disable()
            _print("memory disabled - nothing will be read or written")
        elif sub == "enable":
            memory.enable()
            _print("memory enabled")
        else:
            _print("usage: /memory counts|search|add|forget|export|disable|enable [args]")

    def cmd_tasks(self, _arg: str) -> None:
        _print(self.runtime.tasks.render())

    def cmd_privacy(self, arg: str) -> None:
        outcome = (
            self.runtime.host.privacy_on()
            if arg.strip().lower() in {"on", "", "enable"}
            else self.runtime.host.privacy_off()
        )
        _print(outcome.message)

    def cmd_camera(self, arg: str) -> None:
        outcome = (
            self.runtime.host.camera_off()
            if arg.strip().lower() in {"off", "disable"}
            else self.runtime.host.camera_on()
        )
        _print(outcome.message)

    def cmd_lock(self, _arg: str) -> None:
        """Emergency stop: halts tasks, kills capture, and refuses new work.

        Also engages the host privacy lock. Both are persisted, so the lock
        survives restarting Jarvis - a stop that a new process forgets is not
        a stop. /resume clears both.
        """
        outcome = self.runtime.host.lock()
        _print(outcome.message)
        _print(self.runtime.emergency_stop())
        _print("The lock is persisted. Run /resume to clear it.")

    def cmd_pause(self, _arg: str) -> None:
        _print(self.runtime.pause())

    def cmd_resume(self, _arg: str) -> None:
        self.runtime.host.unlock_agent()
        _print(self.runtime.resume())

    def cmd_stop(self, _arg: str) -> None:
        _print(self.runtime.emergency_stop())

    def cmd_build(self, arg: str) -> None:
        bits = arg.split(maxsplit=1)
        if not bits:
            from jarvis.agents.webbuilder import supported_categories

            _print("usage: /build <category> <destination>")
            _print("categories: " + ", ".join(supported_categories()))
            return
        category = bits[0].lower()
        destination = bits[1] if len(bits) > 1 else "./output/site"
        execution = self.runtime.handle(
            f"build a {category} site",
            intent="build",
            category=category,
            path=destination,
        )
        _render_execution(execution)

    def cmd_doc(self, arg: str) -> None:
        bits = arg.split(maxsplit=1)
        if not bits:
            _print("usage: /doc <xlsx|csv|docx|pdf|md> <title>")
            return
        fmt = bits[0].lower().lstrip(".")
        title = bits[1] if len(bits) > 1 else "Jarvis document"
        execution = self.runtime.handle(
            title, intent="document", format=fmt, title=title,
            headers=["Item", "Detail"], rows=[["Created by", "Jarvis CLI"]],
        )
        _render_execution(execution)

    def cmd_read(self, arg: str) -> None:
        """Fetch a source, report honestly what came back, store what passes.

        The first token is the locator and is passed explicitly. Detecting it
        from the sentence instead would silently miss anything without a scheme
        - a bare filename handed to a FileFetcher, for instance - and the agent
        would then answer the question rather than read the source.
        """
        bits = arg.split(maxsplit=1)
        if not bits:
            _print("usage: /read <url-or-path> [what it supports]")
            return
        url = bits[0]
        statement = bits[1].strip() if len(bits) > 1 else ""
        execution = self.runtime.handle(
            statement or url, intent="research", url=url, statement=statement
        )
        _render_execution(execution)

    def cmd_config(self, arg: str) -> None:
        """Show the effective configuration with secret values masked.

        Reading configuration is not the same as reading secrets: variables whose
        *name* marks them as secret are replaced by '***' no matter what they hold,
        so this view is safe to paste into a bug report.
        """
        _print("effective configuration (secret-named values are masked)")
        for key, value in self.runtime.settings.redacted_view().items():
            _print(f"  {key:<28} {value}")
        _print()
        _print("data locations")
        for key in ("home", "memory_db", "audit_path", "documents_dir", "checkpoint_dir"):
            _print(f"  {key:<28} {getattr(self.runtime.settings, key)}")

    def cmd_consent(self, arg: str) -> None:
        device = arg.strip().lower()
        if device not in {"camera", "microphone", "screen"}:
            _print("usage: /consent <camera|microphone|screen>")
            return
        _print(f"Granting {device} access. This is recorded, revocable, and the OS "
               "indicator stays visible.")
        if device in self.runtime.host.consented_devices:
            _print(f"{device} was already granted.")
            return
        self.runtime.host.consented_devices.add(device)
        self.runtime.audit.log(
            "consent.granted", agent="computer", result=device, risk="medium",
            approval="granted by user in session",
        )
        _print(f"{device} access granted for this session.")

    def cmd_autostart(self, _arg: str) -> None:
        _print(self.runtime.host.autostart_instructions()
               if self.runtime.host.has("autostart")
               else "Autostart is not available: computer use is disabled in settings.\n"
                    "Set JARVIS_COMPUTER_USE=on to see the instructions for this OS.")

    # -- misc ----------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return self.runtime.status()


# --------------------------------------------------------------------------- #
# Non-interactive commands
# --------------------------------------------------------------------------- #
def _cmd_run(runtime: Runtime, args: argparse.Namespace) -> int:
    """Handle one request.

    The exit code answers "could Jarvis fulfil this?", not "how sure is Jarvis?".
    A request that produced an answer exits 0 even when that answer is reported as
    unverified - honest uncertainty is not a failure. A request Jarvis could not
    fulfil, refused, or crashed on exits 1.
    """
    execution = runtime.handle(args.text, intent=args.intent)
    _render_execution(execution)
    if args.json and execution.result is not None:
        _print(json.dumps(execution.as_dict(), indent=2, default=str))
    return 0 if execution.ok else 1


def _cmd_status(runtime: Runtime, args: argparse.Namespace) -> int:
    if args.json:
        _print(json.dumps(runtime.status(), indent=2, default=str))
    else:
        _print(quick_status(runtime))
    return 0


def _cmd_audit(runtime: Runtime, args: argparse.Namespace) -> int:
    _print(summarize(runtime.audit.entries(limit=args.limit)))
    return 0


def _cmd_revenue(runtime: Runtime, args: argparse.Namespace) -> int:
    from jarvis.agents.revenue import Opportunity

    execution = runtime.handle(
        args.idea, intent="revenue", opportunity=Opportunity(title=args.idea, description=args.idea)
    )
    _render_execution(execution)
    if args.json and execution.result is not None:
        _print(json.dumps(execution.result.data, indent=2, default=str))
    return 0


def _cmd_check_secret(_runtime: Runtime, args: argparse.Namespace) -> int:
    text = args.text if args.text != "-" else sys.stdin.read()
    findings = redaction.scan(text)
    if not findings:
        _print("no secrets detected")
        return 0
    for finding in findings:
        _print(f"{finding.label} at offset {finding.start} (preview {finding.preview})")
    return 1


def _cmd_read(runtime: Runtime, args: argparse.Namespace) -> int:
    statement = " ".join(args.statement).strip()
    execution = runtime.handle(
        statement or args.url, intent="research", url=args.url, statement=statement
    )
    _render_execution(execution)
    if args.json and execution.result is not None:
        _print(json.dumps(execution.result.data, indent=2, default=str))
    return 0 if execution.ok else 1


def _cmd_lock(runtime: Runtime, _args: argparse.Namespace) -> int:
    """Emergency stop from the command line. Always available, never gated."""
    _print(runtime.emergency_stop())
    return 0


def _cmd_resume(runtime: Runtime, _args: argparse.Namespace) -> int:
    _print(runtime.resume())
    return 0


def _cmd_config(runtime: Runtime, args: argparse.Namespace) -> int:
    """Print the effective configuration with secret values masked."""
    view = runtime.settings.redacted_view()
    if args.json:
        _print(json.dumps(view, indent=2, default=str))
        return 0
    _print("effective configuration (secret-named values are masked)")
    for key, value in view.items():
        _print(f"  {key:<28} {value}")
    return 0


def _cmd_repl(runtime: Runtime, _args: argparse.Namespace) -> int:
    return Repl(runtime).loop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis - autonomous personal, research and development agent.",
    )
    parser.add_argument("--home", help="override JARVIS_HOME")
    parser.add_argument("--autonomy", choices=["auto", "ask", "review"])
    parser.add_argument("--jurisdiction", help="default jurisdiction, e.g. IN or US")
    parser.add_argument("--no-memory", action="store_true", help="run without persistent memory")
    parser.add_argument("--computer-use", action="store_true", help="enable OS control")
    parser.add_argument("--json", action="store_true", help="emit JSON where supported")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="handle one request")
    p.add_argument("text")
    p.add_argument("--intent")

    sub.add_parser("status", help="system status")

    p = sub.add_parser("audit", help="show the audit log")
    p.add_argument("limit", nargs="?", type=int, default=15)

    p = sub.add_parser("revenue", help="analyse a revenue opportunity")
    p.add_argument("idea")

    p = sub.add_parser("config", help="show the effective configuration (secrets masked)")
    p.add_argument(
        "--show", action="store_true",
        help="print configuration (the default for this subcommand)",
    )

    p = sub.add_parser("read", help="fetch a URL and record it with its source")
    p.add_argument("url")
    p.add_argument("statement", nargs="*", help="what the page supports")

    sub.add_parser("resume", help="clear an emergency stop and accept work again")
    sub.add_parser("lock", help="emergency stop: halt tasks, kill capture, refuse new work")

    p = sub.add_parser("check-secret", help="scan text for credential-shaped content")
    p.add_argument("text", nargs="?", default="-", help="text to scan, or '-' for stdin")

    sub.add_parser("repl", help="interactive session (the default)")
    return parser


#: Subcommands the parser knows about. Anything else on the command line is
#: treated as a one-shot request, so `jarvis "explain X"` works the way people
#: expect a CLI agent to work.
_SUBCOMMANDS = frozenset(
    {
        "run", "status", "audit", "revenue", "config", "check-secret",
        "resume", "lock", "read", "repl", "help",
    }
)


def _normalise_argv(argv: Sequence[str]) -> list[str]:
    """Rewrite a bare text argument into `run <text>`."""
    out: list[str] = []
    for token in argv:
        if not out and not token.startswith("-") and token not in _SUBCOMMANDS:
            out.append("run")
        out.append(token)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(
        _normalise_argv(sys.argv[1:] if argv is None else argv)
    )

    import os

    if args.home:
        os.environ["JARVIS_HOME"] = args.home
    if args.autonomy:
        os.environ["JARVIS_AUTONOMY"] = args.autonomy
    if args.jurisdiction:
        os.environ["JARVIS_JURISDICTION"] = args.jurisdiction
    if args.computer_use:
        os.environ["JARVIS_COMPUTER_USE"] = "on"

    from jarvis.config import settings_from_env

    settings = settings_from_env(dotenv=Path.cwd() / ".env")
    runtime = build_runtime(settings, with_memory=not args.no_memory)

    handlers = {
        "run": _cmd_run,
        "status": _cmd_status,
        "audit": _cmd_audit,
        "revenue": _cmd_revenue,
        "check-secret": _cmd_check_secret,
        "config": _cmd_config,
        "resume": _cmd_resume,
        "read": _cmd_read,
        "lock": _cmd_lock,
        "repl": _cmd_repl,
    }
    handler = handlers.get(args.command or "")
    try:
        if handler is None:
            if sys.stdin.isatty():
                return Repl(runtime).loop()
            _print(quick_status(runtime))
            return 0
        return handler(runtime, args)
    finally:
        runtime.shutdown()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
