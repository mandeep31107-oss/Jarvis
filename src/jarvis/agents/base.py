"""The contract every specialized agent implements (spec section 24).

Agents are deliberately small and side-effect-free at this layer: they return an
:class:`AgentResult` describing what they found and what they want to do. The
supervisor -- not the agent -- is what actually performs actions, because that is
where the policy, risk and audit checks live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.core.confidence import Claim, Confidence
from jarvis.core.options import Dilemma


@dataclass
class AgentResult:
    """What an agent hands back to the supervisor."""

    agent: str
    ok: bool = True
    summary: str = ""
    #: Every important assertion, each with its own confidence.
    claims: list[Claim] = field(default_factory=list)
    #: Structured payload for the dashboard / next agent.
    data: dict[str, Any] = field(default_factory=dict)
    #: Decisions handed back to the user (spec section 7).
    dilemmas: list[Dilemma] = field(default_factory=list)
    #: Actions the agent wants the supervisor to run through the policy gate.
    proposed_actions: list[Any] = field(default_factory=list)
    #: Human-readable "what I am doing" lines (spec section 30).
    activity: list[str] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> Confidence:
        """The result is only as trustworthy as its weakest important claim."""
        if not self.claims:
            return Confidence.UNKNOWN
        return min((c.confidence for c in self.claims), key=lambda c: c.rank)

    def add_claim(self, claim: Claim) -> AgentResult:
        self.claims.append(claim)
        return self

    def say(self, message: str) -> None:
        self.activity.append(message)

    def render(self) -> str:
        head = f"[{self.agent}] {'ok' if self.ok else 'blocked'} - {self.summary}"
        lines = [head]
        lines.extend(f"  - {c.render()}" for c in self.claims)
        if self.follow_ups:
            lines.append("  next:")
            lines.extend(f"    * {f}" for f in self.follow_ups)
        for d in self.dilemmas:
            lines.append("")
            lines.append(d.render())
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "ok": self.ok,
            "summary": self.summary,
            "confidence": self.confidence.value,
            "claims": [c.as_dict() for c in self.claims],
            "data": self.data,
            "dilemmas": [d.as_dict() for d in self.dilemmas],
            "activity": list(self.activity),
            "follow_ups": list(self.follow_ups),
        }


class Agent:
    """Base class for specialized agents.

    Subclasses set ``name`` and implement :meth:`run`. ``capabilities`` is what
    the supervisor uses for routing, and ``requires`` lists the extras/hardware
    the agent needs so an unmet dependency is reported instead of crashing.
    """

    name: str = "agent"
    description: str = ""
    capabilities: tuple[str, ...] = ()
    #: Optional importable modules; missing ones degrade instead of breaking.
    requires: tuple[str, ...] = ()

    def __init__(self, runtime: Any = None) -> None:
        self.runtime = runtime

    # --- lifecycle -----------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """(is_usable, reason). Checked before the agent is dispatched to."""
        missing = [m for m in self.requires if _missing(m)]
        if missing:
            return False, f"missing optional dependency: {', '.join(missing)}"
        return True, "ready"

    def run(self, request: AgentRequest) -> AgentResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<Agent {self.name}>"


@dataclass
class AgentRequest:
    """A normalised ask, produced by the supervisor from the user's words."""

    intent: str
    text: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    user_request: str = ""
    request_id: str = ""
    language: str = "en"

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


def _missing(module: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(module) is None
    except (ImportError, ValueError):
        return True
