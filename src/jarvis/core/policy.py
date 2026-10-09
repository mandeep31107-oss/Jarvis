"""Permission and decision engine (spec sections 6, 33, 39).

Three layers, evaluated in order:

1. **Hard denials** -- things Jarvis will never do, even if the user says
   "do whatever it takes". Section 39.
2. **Policy** -- autonomy mode + per-category overrides decide whether an action
   may proceed, needs approval, or is refused.
3. **Approval** -- a human-in-the-loop gate for everything the policy flags.

The engine never silently escalates its own privileges: raising autonomy above
the configured ceiling is refused and audited.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from jarvis.core.risk import ActionRequest, RiskAssessment, RiskCategory, RiskEngine, RiskLevel
from jarvis.errors import HardDenial


class Decision(str):
    pass


ALLOW = "allow"
NEEDS_APPROVAL = "needs_approval"
DENY = "deny"
HARD_DENY = "hard_deny"


@dataclass
class PolicyDecision:
    action: ActionRequest
    assessment: RiskAssessment
    decision: str
    reason: str
    requires_approval: bool = False
    #: True when no amount of user approval can unlock this action.
    permanent: bool = False
    safer_alternatives: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action.describe(),
            "risk": self.assessment.level.value,
            "decision": self.decision,
            "reason": self.reason,
            "requires_approval": self.requires_approval,
            "permanent": self.permanent,
            "safer_alternatives": list(self.safer_alternatives),
        }


@dataclass
class Approval:
    granted: bool
    approver: str = "user"
    note: str = ""
    #: Approving one specific action only; blanket approvals are never inferred.
    action_id: str = ""
    at: str = ""


class Approver(Protocol):
    """Anything that can answer 'may Jarvis do this?'."""

    def request(self, decision: PolicyDecision) -> Approval:  # pragma: no cover - protocol
        ...


class DenyingApprover:
    """Default approver: nobody is at the keyboard, so nothing risky proceeds.

    This is the safe default for headless/background operation. A background job
    that cannot reach a human must queue the action, not perform it.
    """

    name = "denying"

    def request(self, decision: PolicyDecision) -> Approval:
        return Approval(granted=False, approver=self.name, note="No approver available; action queued.")


class CallbackApprover:
    """Routes the approval question to a callable (CLI prompt, UI, webhook...)."""

    name = "callback"

    def __init__(self, callback: Callable[[PolicyDecision], bool], *, who: str = "user") -> None:
        self._callback = callback
        self._who = who

    def request(self, decision: PolicyDecision) -> Approval:
        granted = bool(self._callback(decision))
        return Approval(granted=granted, approver=self._who, note="interactive approval")


class AutoApprover:
    """Approves up to a ceiling. Used for tests and for explicitly configured
    unattended workflows; the ceiling is enforced here *and* in the policy."""

    name = "auto"

    def __init__(self, ceiling: RiskLevel = RiskLevel.LOW) -> None:
        self.ceiling = ceiling

    def request(self, decision: PolicyDecision) -> Approval:
        granted = decision.assessment.level.rank <= self.ceiling.rank
        return Approval(
            granted=granted,
            approver=self.name,
            note=f"auto ceiling={self.ceiling.value}",
        )


# ---------------------------------------------------------------------------
# Hard denials (spec sections 2 and 39)
# ---------------------------------------------------------------------------

#: Each entry: (name, verb globs, context regex, explanation, safer alternatives)
FORBIDDEN: list[tuple[str, tuple[str, ...], str, str, tuple[str, ...]]] = [
    (
        "auth.bypass",
        ("auth.bypass", "auth.crack", "login.bruteforce", "mfa.bypass"),
        r"bypass|circumvent|crack|brute",
        "Bypassing authentication, MFA or any access control is unauthorised access.",
        ("Ask the user to sign in themselves.", "Use an official OAuth/API token the user provides."),
    ),
    (
        "captcha.bypass",
        ("captcha.solve", "captcha.bypass"),
        r"captcha|recaptcha|hcaptcha|turnstile",
        "Solving or bypassing CAPTCHA defeats an explicit anti-automation control.",
        ("Have the user complete the challenge.", "Use the platform's official API if one exists."),
    ),
    (
        "ratelimit.bypass",
        ("ratelimit.bypass", "throttle.evade", "proxy.rotate"),
        r"rate.?limit|throttl|evade|rotat",
        "Evading rate limits or rotating proxies to dodge them breaches most platform ToS.",
        ("Respect the documented limit and queue the work.", "Request a higher tier from the provider."),
    ),
    (
        "paywall.bypass",
        ("paywall.bypass", "drm.bypass", "content.scrape_paid"),
        r"paywall|drm|premium.?content|pirat",
        "Circumventing paywalls or DRM infringes copyright and platform terms.",
        ("Summarise openly licensed material.", "Purchase or subscribe with the user's approval."),
    ),
    (
        "unauthorised.access",
        ("system.intrude", "network.scan_unauthorised", "wifi.crack"),
        r"intrud|unauthoris|unauthoriz|hack into|wifi.?crack",
        "Accessing systems the user does not own or control is illegal.",
        ("Limit work to systems the user owns and has authorised.", "Stop and ask the owner."),
    ),
    (
        "impersonation",
        ("identity.impersonate", "profile.fake", "review.fake"),
        r"impersonat|pretend.?to.?be.?human|fake.?(profile|review|account)",
        "Deceptively impersonating a human or another person is fraud in most jurisdictions.",
        ("Disclose that the message is from an AI assistant where required.", "Have the user send it."),
    ),
    (
        "spam",
        ("bulk.send_unsolicited", "spam.*"),
        r"\bspam\b|unsolicited|cold.?(blast|mass)|mass.?dm",
        "Unsolicited bulk messaging violates anti-spam law and platform rules.",
        ("Send to an opt-in list with an unsubscribe link.", "Draft one personalised message."),
    ),
    (
        "fraud",
        ("fraud.*", "chargeback.abuse", "launder.*"),
        r"fraud|launder|chargeback.?abuse|stolen.?(card|account)",
        "Fraudulent transactions and money laundering are serious crimes.",
        ("Stop. Use a legitimate payment flow with the user's own funds."),
    ),
    (
        "ip.theft",
        ("content.clone_proprietary", "license.violate"),
        r"steal|plagiaris|plagiariz|clone.?their|rip.?off|pirate",
        "Copying or misusing someone else's intellectual property is infringement.",
        ("Build an original implementation.", "Use properly licensed assets."),
    ),
    (
        "secret.exfiltration",
        ("secret.export", "credential.share", "memory.dump_secrets"),
        r"send.?(keys?|tokens?|passwords?)|exfiltrat|upload.?(env|credentials)",
        "Secrets must never leave the machine into a message, paste or third-party site.",
        ("Rotate the credential.", "Share a redacted reference instead."),
    ),
    (
        "covert.surveillance",
        ("camera.record_covert", "mic.record_covert", "track.person"),
        r"covert|secret(ly)?.?record|without.?them.?knowing|spy|stalk|track.?person",
        "Covert recording or tracking of people breaches privacy law.",
        ("Record only with visible indicator and consent.", "Disable capture."),
    ),
    (
        "safety.override",
        ("safety.disable", "policy.disable", "guardrail.remove"),
        r"disable.?(safety|guardrail|policy|audit)|remove.?restrictions",
        "Jarvis cannot switch off its own safety, audit or compliance layer.",
        ("Adjust autonomy settings, which stay audited.", "Stop the agent instead."),
    ),
]


class PolicyEngine:
    """Decides allow / needs_approval / deny, and enforces the never-do list."""

    #: Ceiling on how autonomous Jarvis may be configured to run.
    MAX_AUTONOMY: dict[str, RiskLevel] = {
        # LOW automatic, MEDIUM asks, HIGH/CRITICAL always ask
        "auto": RiskLevel.LOW,
        # everything asks (handled via auto_ceiling_rank = -1 in set_autonomy)
        "ask": RiskLevel.LOW,
        # LOW + MEDIUM automatic, HIGH/CRITICAL ask
        "review": RiskLevel.MEDIUM,
    }

    def __init__(
        self,
        risk: RiskEngine,
        *,
        autonomy: str = "auto",
        approver: Approver | None = None,
        category_overrides: dict[RiskCategory, str] | None = None,
    ) -> None:
        self.risk = risk
        self.approver: Approver = approver or DenyingApprover()
        self.category_overrides: dict[RiskCategory, str] = dict(category_overrides or {})
        self.set_autonomy(autonomy)
        self._alternatives: dict[str, list[str]] = {}
        for name, verbs, regex, reason, alternatives in FORBIDDEN:
            risk.add_hard_denial(name, reason, verbs=verbs, context_regex=regex)
            self._alternatives[name] = list(alternatives)

    # --- configuration -------------------------------------------------------
    def set_autonomy(self, mode: str) -> None:
        if mode not in self.MAX_AUTONOMY:
            raise ValueError(f"unknown autonomy mode: {mode!r}")
        self.autonomy = mode
        #: Highest risk level that may proceed without asking.
        self.auto_ceiling = RiskLevel.LOW if mode == "ask" else self.MAX_AUTONOMY[mode]
        if mode == "ask":
            # In 'ask' mode even LOW risk actions are confirmed first.
            self.auto_ceiling_rank = -1
        else:
            self.auto_ceiling_rank = self.auto_ceiling.rank

    def override_category(self, category: RiskCategory, decision: str) -> None:
        """Per-category override. ``decision`` is one of allow/needs_approval/deny."""
        if decision not in (ALLOW, NEEDS_APPROVAL, DENY):
            raise ValueError(f"unknown category override: {decision!r}")
        self.category_overrides[category] = decision

    # --- decision ------------------------------------------------------------
    def decide(self, action: ActionRequest, *, preapproved: Approval | None = None) -> PolicyDecision:
        assessment = self.risk.assess(action)

        if assessment.denied:
            return PolicyDecision(
                action=action,
                assessment=assessment,
                decision=HARD_DENY,
                reason=assessment.denial_reason,
                permanent=True,
                safer_alternatives=list(self._alternatives.get(assessment.denial_rule, ())),
            )

        override = self.category_overrides.get(assessment.category)
        if override == DENY:
            return PolicyDecision(
                action=action,
                assessment=assessment,
                decision=DENY,
                reason=f"Category '{assessment.category.value}' is disabled in policy.",
                safer_alternatives=["Re-enable the category in settings, or perform it manually."],
            )

        auto_rank = self.auto_ceiling_rank
        if assessment.level.rank <= auto_rank and override != NEEDS_APPROVAL:
            return PolicyDecision(
                action=action,
                assessment=assessment,
                decision=ALLOW,
                reason=f"Risk {assessment.level.value} is within the '{self.autonomy}' autonomy ceiling.",
            )

        return PolicyDecision(
            action=action,
            assessment=assessment,
            decision=NEEDS_APPROVAL,
            requires_approval=True,
            reason=(
                f"Risk {assessment.level.value} exceeds the '{self.autonomy}' autonomy ceiling "
                f"({self.auto_ceiling.value}); human approval required."
            ),
        )

    def gate(self, action: ActionRequest, *, preapproved: Approval | None = None) -> PolicyDecision:
        """Decide, and if approval is needed, actually ask. Raises :class:`HardDenial`
        for permanently forbidden actions so callers cannot ignore it."""
        decision = self.decide(action, preapproved=preapproved)

        if decision.decision == HARD_DENY:
            raise HardDenial(
                f"Refused: {decision.reason}",
                rule=decision.assessment.denial_rule,
                risk=decision.assessment.level.value,
            )

        if decision.decision == NEEDS_APPROVAL:
            approval = preapproved or self.approver.request(decision)
            decision.requires_approval = True
            if approval.granted:
                decision.decision = ALLOW
                decision.reason = f"Approved by {approval.approver}: {approval.note}"
            else:
                decision.decision = DENY
                decision.reason = f"Not approved by {approval.approver}: {approval.note}"
        return decision

    def describe(self) -> dict[str, object]:
        return {
            "autonomy": self.autonomy,
            "auto_ceiling": self.auto_ceiling.value,
            "approver": getattr(self.approver, "name", type(self.approver).__name__),
            "category_overrides": {c.value: d for c, d in self.category_overrides.items()},
            "hard_denials": [name for name, *_ in FORBIDDEN],
        }
