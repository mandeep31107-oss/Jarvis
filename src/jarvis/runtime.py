"""The Jarvis runtime: everything wired together (spec section 23).

    USER -> INPUT -> PERCEPTION -> CONTEXT -> MEMORY -> PLANNER ->
    POLICY -> RISK -> MODEL ROUTER -> AGENTS -> TOOLS -> VALIDATION ->
    RESULT -> MEMORY UPDATE

:func:`build_runtime` is the single entry point. It works with zero
configuration and zero API keys: anything that needs an external provider
degrades and says so, rather than failing to start.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from jarvis.agents.base import AgentRequest
from jarvis.agents.coding_agent import CodingAgent
from jarvis.agents.compliance_agent import ComplianceAgent
from jarvis.agents.documents import DocumentAgent
from jarvis.agents.perception import ComputerAgent, VisionAgent
from jarvis.agents.research import ResearchAgent
from jarvis.agents.revenue import RevenueAgent
from jarvis.agents.support import LessonLog, MemoryAgent, ProductivityAgent, SecurityAgent
from jarvis.agents.webbuilder import WebBuilderAgent
from jarvis.compliance.jurisdiction import JurisdictionKnowledge
from jarvis.compliance.terms import TermsRegistry
from jarvis.config import Settings, settings_from_env
from jarvis.core.audit import AuditLog
from jarvis.core.events import EventBus
from jarvis.core.knowledge import KnowledgeBase
from jarvis.core.memory import MemoryStore
from jarvis.core.policy import Approver, CallbackApprover, PolicyEngine
from jarvis.core.risk import ActionRequest, RiskEngine
from jarvis.core.router import ModelRouter
from jarvis.core.supervisor import AgentRegistry, Execution, Supervisor
from jarvis.core.tasks import TaskManager
from jarvis.host import HostAdapter, detect_host
from jarvis.interaction.intents import parse_intent
from jarvis.version import PHASE, PHASE_NAME, __version__

log = logging.getLogger("jarvis.runtime")


@dataclass
class Runtime:
    """Holds every subsystem and exposes the two things callers need: handle() and status()."""

    settings: Settings
    events: EventBus
    audit: AuditLog
    memory: MemoryStore | None
    knowledge: KnowledgeBase
    risk: RiskEngine
    policy: PolicyEngine
    tasks: TaskManager
    router: ModelRouter
    host: HostAdapter
    registry: AgentRegistry
    supervisor: Supervisor
    lessons: LessonLog
    jurisdictions: JurisdictionKnowledge = field(default_factory=JurisdictionKnowledge)
    terms: TermsRegistry = field(default_factory=TermsRegistry)
    #: Set by emergency_stop(), cleared by resume(). New work is refused while it
    #: is set, so "stop" means stop and not merely "finish what is running".
    emergency_stopped: bool = False

    # --- main entry ----------------------------------------------------------
    def handle(
        self,
        text: str,
        *,
        intent: str | None = None,
        language: str | None = None,
        **params: Any,
    ) -> Execution:
        """Take a user utterance all the way through the pipeline."""
        request = parse_intent(text, intent=intent, language=language, **params)
        if self.emergency_stopped:
            self.audit.log(
                "request.blocked.emergency_stop",
                user_request=request.text[:200],
                interpretation=request.intent,
                result="refused",
                agent="runtime",
                meta={"reason": "emergency stop is active"},
            )
            return Execution(
                request=request,
                error=(
                    "EMERGENCY STOP is active. Jarvis is not doing any work. "
                    "Run /resume (or `jarvis run --resume`) when you want it to continue."
                ),
            )
        execution = self.supervisor.handle(request)

        # MEMORY UPDATE (spec section 23, last stage). Decisions worth keeping are
        # stored; nothing sensitive is, and secrets are refused by the store.
        if execution.result is not None and self.memory is not None and self.memory.enabled:
            self._maybe_remember(request, execution)
        return execution

    def _maybe_remember(self, request: AgentRequest, execution: Execution) -> None:
        if execution.request.intent not in {"revenue", "compliance", "build", "remember"}:
            return
        if execution.result is None or not execution.result.summary:
            return
        category = "decision" if execution.request.intent in {"revenue", "build"} else "knowledge"
        try:
            self.memory.remember(
                category,
                (request.text or request.intent)[:120],
                execution.result.summary,
                tags=[execution.request.intent, execution.agent],
                source="supervisor",
                confidence=execution.result.confidence,
                importance=5,
            )
        except Exception as exc:  # noqa: BLE001 - memory must never break a request
            log.warning("could not store memory: %s", exc)

    # --- actions -------------------------------------------------------------
    def act(self, verb: str, target: str = "", **context: Any) -> dict[str, Any]:
        """The only way to perform a side-effecting action. Always policy-gated."""
        action = ActionRequest(verb=verb, target=target, context=context)
        return self.supervisor.propose(action)

    # --- lifecycle -----------------------------------------------------------
    def shutdown(self) -> None:
        self.tasks.stop()
        if self.memory is not None:
            self.memory.close()
        self.events.publish("runtime.shutdown")

    # --- introspection -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "phase": PHASE,
            "phase_name": PHASE_NAME,
            "run_state": self.run_state,
            "settings": self.settings.redacted_view(),
            "agents": self.registry.describe(),
            "policy": self.policy.describe(),
            "models": self.router.health(),
            "host": self.host.describe(),
            "memory": self.memory.counts() if self.memory is not None else {"enabled": False},
            "knowledge": self.knowledge.stats(),
            "tasks": {
                "pending": self.tasks.pending_count(),
                "paused": self.paused,
            },
            "audit": self.audit.stats(),
            "compliance": {
                "jurisdictions": len(self.jurisdictions.entries),
                "regulations_needing_verification": len(self.jurisdictions.verification_queue()),
                "platforms": len(self.terms.platforms),
                "platform_policies_needing_verification": len(self.terms.verification_queue()),
            },
            "recent_activity": [
                {"agent": e.payload.get("agent"), "message": e.payload.get("message"), "at": e.at}
                for e in self.events.recent(8, topic="activity")
            ],
        }

    @property
    def paused(self) -> bool:
        return self.tasks.paused or self.host.privacy_mode.value == "locked"

    @property
    def run_state(self) -> str:
        """One of: emergency_stopped, paused, running. Reported by status().

        The safety state is deliberately the loudest thing in the status view: a
        user who cannot *see* that the agent is stopped has no way to trust it.
        """
        if self.emergency_stopped:
            return "emergency_stopped"
        return "paused" if self.paused else "running"

    def pause(self) -> str:
        self.tasks.pause()
        return "PAUSED - background work stopped. Say 'resume' to continue."

    def resume(self) -> str:
        was_stopped = self.emergency_stopped
        self.emergency_stopped = False
        self.tasks.resume()
        self.audit.log(
            "runtime.resumed", result="ok", agent="runtime",
            meta={"from": "emergency_stop" if was_stopped else "pause"},
        )
        return "RESUMED." + (
            " Emergency stop cleared - Jarvis may accept work again." if was_stopped else ""
        )

    def emergency_stop(self) -> str:
        """The user can always stop the agent (spec sections 20, 21).

        This must work from every state, including mid-task, and it must be
        observable afterwards - a stop the user cannot confirm is not a stop.
        """
        self.emergency_stopped = True
        self.tasks.stop()
        with contextlib.suppress(Exception):
            self.host.camera_off()
            self.host.mic_off()
        self.events.publish("runtime.emergency_stop")
        self.audit.log(
            "runtime.emergency_stop",
            result="stopped",
            agent="runtime",
            meta={"capture_devices_off": True, "tasks_halted": True},
        )
        return (
            "EMERGENCY STOP - all tasks halted, capture devices off. "
            "No new work will be accepted until you run /resume."
        )

    def set_approver(self, approver: Approver) -> None:
        self.policy.approver = approver


def build_runtime(
    settings: Settings | None = None,
    *,
    approver: Approver | None = None,
    with_memory: bool = True,
    search_provider: Any = None,
    model_router: ModelRouter | None = None,
    host: HostAdapter | None = None,
) -> Runtime:
    """Construct a fully wired runtime.

    Every dependency can be injected, which is what makes the model, the search
    backend and the OS layer replaceable without touching the rest of the system
    (spec section 25).
    """
    settings = settings or settings_from_env()
    settings.ensure_dirs()

    events = EventBus()
    audit = AuditLog(settings.audit_path)
    memory = MemoryStore(settings.memory_db) if with_memory else None
    knowledge = KnowledgeBase(settings.home / "knowledge.sqlite3")
    risk = RiskEngine()
    policy = PolicyEngine(
        risk,
        autonomy=settings.autonomy,
        approver=approver or CallbackApprover(lambda decision: False),
    )
    tasks = TaskManager(events=events, checkpoint_dir=settings.checkpoint_dir)
    router = model_router or ModelRouter(preferred_provider=settings.model_provider)
    host = host or detect_host(
        computer_use_enabled=settings.computer_use,
        consented_devices=settings.capture_devices,
    )
    jurisdictions = JurisdictionKnowledge()
    terms = TermsRegistry()

    registry = AgentRegistry()
    registry.register(ResearchAgent(knowledge=knowledge, provider=search_provider))
    registry.register(
        RevenueAgent(jurisdictions=jurisdictions, terms=terms)
    )
    registry.register(
        ComplianceAgent(
            jurisdictions=jurisdictions, terms=terms, default_jurisdiction=settings.jurisdiction
        )
    )
    registry.register(CodingAgent())
    registry.register(WebBuilderAgent())
    registry.register(DocumentAgent(output_dir=settings.documents_dir))
    registry.register(ComputerAgent(host=host))
    registry.register(VisionAgent(host=host))
    registry.register(MemoryAgent(memory=memory))
    registry.register(ProductivityAgent(memory=memory))
    registry.register(SecurityAgent())

    supervisor = Supervisor(registry=registry, policy=policy, audit=audit, events=events)
    runtime = Runtime(
        settings=settings,
        events=events,
        audit=audit,
        memory=memory,
        knowledge=knowledge,
        risk=risk,
        policy=policy,
        tasks=tasks,
        router=router,
        host=host,
        registry=registry,
        supervisor=supervisor,
        lessons=LessonLog(memory),
        jurisdictions=jurisdictions,
        terms=terms,
    )
    audit.log(
        "runtime.start",
        agent="supervisor",
        result=f"Jarvis {__version__} phase {PHASE} ({PHASE_NAME})",
        risk="low",
        meta={"autonomy": settings.autonomy, "jurisdiction": settings.jurisdiction},
    )
    events.publish("runtime.start", version=__version__, phase=PHASE)
    return runtime


def quick_status(runtime: Runtime) -> str:
    """A compact text status block for the CLI."""
    status = runtime.status()
    agents = status["agents"]
    ready = [n for n, a in agents.items() if a["available"]]
    lines = [
        f"Jarvis {status['version']} - phase {status['phase']} ({status['phase_name']})",
        f"state       : {status['run_state'].upper()}",
        f"agents      : {len(ready)}/{len(agents)} ready ({', '.join(sorted(ready))})",
        f"autonomy    : {status['policy']['autonomy']} (auto ceiling: {status['policy']['auto_ceiling']})",
        f"jurisdiction: {status['settings']['jurisdiction']}",
        f"memory      : {status['memory'].get('total', 0)} record(s)",
        f"knowledge   : {status['knowledge']}",
        f"models      : {len(status['models']['available'])} available, last choice {status['models']['last_choice']}",
        f"host        : {status['host']['platform']} privacy={status['host']['privacy_mode']}",
        f"audit       : {status['audit']['total']} record(s) at {status['audit']['path']}",
        f"compliance  : {status['compliance']['regulations_needing_verification']} regulation(s) "
        f"and {status['compliance']['platform_policies_needing_verification']} platform polic(y/ies) need verification",
    ]
    return "\n".join(lines)
