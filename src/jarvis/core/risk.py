"""Risk assessment (spec section 6).

Every action Jarvis considers is turned into an :class:`ActionRequest` and
scored into ``LOW`` / ``MEDIUM`` / ``HIGH`` / ``CRITICAL``. The score is
explainable: every rule that fires contributes a human-readable reason, and the
highest-severity rule wins.
"""

from __future__ import annotations

import enum
import fnmatch
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]

    def __lt__(self, other: RiskLevel) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: RiskLevel) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.rank <= other.rank


class RiskCategory(str, enum.Enum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    DESTRUCTIVE = "destructive"
    COMMUNICATION = "communication"
    PUBLISHING = "publishing"
    FINANCIAL = "financial"
    ACCOUNT = "account"
    SECURITY = "security"
    LEGAL = "legal"
    PRIVACY = "privacy"
    THIRD_PARTY_DATA = "third_party_data"
    SYSTEM = "system"


@dataclass
class ActionRequest:
    """A normalised description of something Jarvis might do.

    ``verb`` is a short canonical verb (``file.delete``, ``message.send``,
    ``payment.transfer`` ...). ``target`` is what it acts on. ``context``
    carries whatever the caller knows (amount, recipient, platform, ...).
    """

    verb: str
    target: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    actor: str = "jarvis"
    #: Id of the originating user request, used to tie the audit trail together.
    request_id: str = ""
    #: When True the action cannot be undone (money moved, file deleted, email sent).
    irreversible: bool = False

    @property
    def money_amount(self) -> float:
        try:
            return float(self.context.get("amount", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def matches(self, pattern: str) -> bool:
        """Glob match against ``verb`` or ``verb target``."""
        return fnmatch.fnmatch(self.verb, pattern) or fnmatch.fnmatch(
            f"{self.verb} {self.target}".strip(), pattern
        )

    def describe(self) -> str:
        extra = ", ".join(f"{k}={v}" for k, v in sorted(self.context.items()) if k in _SHOWN_CONTEXT)
        base = f"{self.verb}" + (f" -> {self.target}" if self.target else "")
        return f"{base} ({extra})" if extra else base


_SHOWN_CONTEXT = {"amount", "currency", "platform", "recipient", "path", "url", "device"}


@dataclass(frozen=True)
class RuleHit:
    rule: str
    level: RiskLevel
    reason: str
    category: RiskCategory


@dataclass
class RiskAssessment:
    action: ActionRequest
    level: RiskLevel
    hits: list[RuleHit] = field(default_factory=list)
    requires_approval: bool = False
    denied: bool = False
    denial_reason: str = ""
    denial_rule: str = ""

    @property
    def category(self) -> RiskCategory:
        return self.hits[0].category if self.hits else RiskCategory.READ_ONLY

    def reasons(self) -> list[str]:
        return [f"[{h.level.value}] {h.rule}: {h.reason}" for h in self.hits]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.describe(),
            "verb": self.action.verb,
            "level": self.level.value,
            "category": self.category.value,
            "irreversible": self.action.irreversible,
            "requires_approval": self.requires_approval,
            "denied": self.denied,
            "denial_reason": self.denial_reason,
            "reasons": self.reasons(),
        }


#: Matcher signature: returns a rule hit when it applies to the action.
RulePredicate = Callable[[ActionRequest], RuleHit | None]


class RiskRule:
    def __init__(
        self,
        name: str,
        level: RiskLevel,
        category: RiskCategory,
        reason: str,
        *,
        verbs: Sequence[str] = (),
        predicate: RulePredicate | None = None,
        context_regex: str | None = None,
        match: str = "all",
    ) -> None:
        self.name = name
        self.level = level
        self.category = category
        self.reason = reason
        self.verbs = tuple(verbs)
        self.predicate = predicate
        self.context_regex = re.compile(context_regex, re.I) if context_regex else None
        #: "all" = every supplied condition must hold (ordinary rules).
        #: "any" = one condition is enough (hard denials, where an unparameterised
        #: ``captcha.solve`` call must be caught just as firmly as one carrying
        #: incriminating context).
        self.match = match

    def evaluate(self, action: ActionRequest) -> RuleHit | None:
        if self.predicate is not None:
            return self.predicate(action)
        verb_match = any(action.matches(v) for v in self.verbs) if self.verbs else True
        context_match = False
        if self.context_regex is not None:
            blob = " ".join(f"{k}={v}" for k, v in action.context.items())
            context_match = bool(self.context_regex.search(f"{action.target} {blob}"))
        if self.match == "any":
            if not (verb_match and self.verbs or context_match):
                return None
        else:
            if not verb_match:
                return None
            if self.context_regex is not None and not context_match:
                return None
        return RuleHit(self.name, self.level, self.reason, self.category)


def _default_rules() -> list[RiskRule]:
    """Baseline risk model. Users may add rules; they may not weaken a CRITICAL one
    without going through the policy engine, which records that in the audit log."""
    return [
        # --- CRITICAL -------------------------------------------------------
        RiskRule(
            "financial.transfer",
            RiskLevel.CRITICAL,
            RiskCategory.FINANCIAL,
            "Moving money is irreversible and has legal/tax consequences.",
            verbs=("payment.*", "money.*", "bank.*", "crypto.*"),
        ),
        RiskRule(
            "irreversible.flag",
            RiskLevel.CRITICAL,
            RiskCategory.DESTRUCTIVE,
            "The caller declared this action irreversible.",
            predicate=lambda a: RuleHit(
                "irreversible.flag", RiskLevel.CRITICAL, RiskCategory.DESTRUCTIVE,
                "Caller declared the action irreversible.",
            )
            if a.irreversible
            else None,
        ),
        RiskRule(
            "security.change",
            RiskLevel.CRITICAL,
            RiskCategory.SECURITY,
            "Changing authentication, keys or security settings can lock the user out.",
            verbs=("security.*", "auth.*", "credential.*", "ssh.*"),
        ),
        RiskRule(
            "third_party.private_data",
            RiskLevel.CRITICAL,
            RiskCategory.THIRD_PARTY_DATA,
            "Involves another person's private information.",
            context_regex=r"person\w*=|third_party|someone.else|contacts\.all",
        ),
        # --- HIGH -----------------------------------------------------------
        RiskRule(
            "destructive.delete",
            RiskLevel.HIGH,
            RiskCategory.DESTRUCTIVE,
            "Deleting data is destructive; a mistake may be unrecoverable.",
            verbs=("file.delete", "file.purge", "db.drop*", "repo.force_push", "*.delete_all"),
        ),
        RiskRule(
            "system.change",
            RiskLevel.HIGH,
            RiskCategory.SYSTEM,
            "Modifying OS settings, services or startup affects the whole machine.",
            verbs=("system.*", "autostart.*", "service.*", "process.kill*"),
        ),
        RiskRule(
            "publishing.public",
            RiskLevel.HIGH,
            RiskCategory.PUBLISHING,
            "Public content carries reputational and IP consequences.",
            verbs=("publish.*", "post.public", "deploy.production", "release.*"),
        ),
        RiskRule(
            "legal.commitment",
            RiskLevel.HIGH,
            RiskCategory.LEGAL,
            "Contracts and legal filings bind the user.",
            verbs=("contract.*", "legal.file", "agreement.sign", "tax.file"),
        ),
        RiskRule(
            "account.create",
            RiskLevel.HIGH,
            RiskCategory.ACCOUNT,
            "Account creation may violate a platform's automation policy and creates a "
            "legal relationship with the provider.",
            verbs=("account.create", "account.link", "signup.*"),
        ),
        RiskRule(
            "financial.spend",
            RiskLevel.HIGH,
            RiskCategory.FINANCIAL,
            "Spending money (subscription, purchase, ad budget) costs the user real funds.",
            verbs=("purchase.*", "subscribe.*", "billing.*", "ads.*"),
        ),
        # --- MEDIUM ---------------------------------------------------------
        RiskRule(
            "communication.send",
            RiskLevel.MEDIUM,
            RiskCategory.COMMUNICATION,
            "Outbound messages reach a real person and cannot be un-sent.",
            verbs=("message.send", "email.send", "sms.send", "chat.reply"),
        ),
        RiskRule(
            "publishing.draft",
            RiskLevel.MEDIUM,
            RiskCategory.PUBLISHING,
            "Publishing to a limited/private audience.",
            verbs=("publish.draft", "post.private", "share.link"),
        ),
        RiskRule(
            "local.write",
            RiskLevel.MEDIUM,
            RiskCategory.LOCAL_WRITE,
            "Modifies files on the user's machine.",
            verbs=("file.write", "file.move", "file.rename", "file.edit", "config.set"),
        ),
        RiskRule(
            "app.control",
            RiskLevel.MEDIUM,
            RiskCategory.SYSTEM,
            "Drives another application through its UI.",
            verbs=("app.open", "app.close", "ui.click", "ui.type", "browser.navigate"),
        ),
        RiskRule(
            "network.fetch",
            RiskLevel.MEDIUM,
            RiskCategory.PRIVACY,
            "Outbound network call may disclose context to a third party.",
            verbs=("http.get", "http.post", "api.call", "search.web"),
        ),
        RiskRule(
            "code.execute",
            RiskLevel.MEDIUM,
            RiskCategory.SYSTEM,
            "Executing code, even sandboxed, consumes resources and can fail loudly.",
            verbs=("code.run", "shell.run", "test.run"),
        ),
        # --- LOW ------------------------------------------------------------
        RiskRule(
            "read_only",
            RiskLevel.LOW,
            RiskCategory.READ_ONLY,
            "Read-only: no external effect.",
            verbs=(
                "file.read", "file.list", "memory.search", "memory.read", "audit.read",
                "research.*", "knowledge.query", "report.generate", "draft.create",
                "code.generate", "doc.create", "task.*", "status.*", "explain.*",
            ),
        ),
    ]


class RiskEngine:
    """Scores actions and exposes the reasoning."""

    def __init__(self, extra_rules: Sequence[RiskRule] = ()) -> None:
        self._rules: list[RiskRule] = list(_default_rules())
        self._user_rules: list[RiskRule] = []
        for rule in extra_rules:
            self.add_rule(rule)
        #: Actions Jarvis refuses regardless of risk score or approval.
        self._hard_denials: list[RiskRule] = []

    # --- configuration -------------------------------------------------------
    def add_rule(self, rule: RiskRule) -> None:
        """User-defined rules are evaluated first and may raise, but never lower,
        a severity that a built-in rule already assigned."""
        self._user_rules.append(rule)

    def add_hard_denial(
        self, name: str, reason: str, *, verbs: Sequence[str] = (), context_regex: str | None = None
    ) -> None:
        self._hard_denials.append(
            RiskRule(name, RiskLevel.CRITICAL, RiskCategory.SECURITY, reason,
                     verbs=verbs, context_regex=context_regex, match="any")
        )

    @property
    def rules(self) -> list[RiskRule]:
        return self._user_rules + self._rules

    # --- evaluation ----------------------------------------------------------
    def assess(self, action: ActionRequest) -> RiskAssessment:
        hits: list[RuleHit] = []
        denial_reason = ""
        denial_rule = ""

        for rule in self._hard_denials:
            hit = rule.evaluate(action)
            if hit:
                denial_reason = hit.reason
                denial_rule = hit.rule
                hits.append(hit)
                break

        for rule in self.rules:
            hit = rule.evaluate(action)
            if hit:
                hits.append(hit)

        if not hits:
            # Unknown action shape is never assumed safe.
            hits.append(
                RuleHit(
                    "unclassified.default",
                    RiskLevel.HIGH,
                    "No rule matched this action; defaulting to HIGH until it is classified.",
                    RiskCategory.SYSTEM,
                )
            )

        hits.sort(key=lambda h: -h.level.rank)
        level = hits[0].level
        return RiskAssessment(
            action=action,
            level=level,
            hits=hits,
            denied=bool(denial_reason),
            denial_reason=denial_reason,
            denial_rule=denial_rule,
        )

    def explain(self, action: ActionRequest) -> str:
        assessment = self.assess(action)
        lines = [f"{action.describe()} -> {assessment.level.value.upper()}"]
        if assessment.denied:
            lines.append(f"BLOCKED ({assessment.denial_rule}): {assessment.denial_reason}")
        lines.extend("  - " + r for r in assessment.reasons())
        return "\n".join(lines)


def risk_matrix_summary(engine: RiskEngine) -> Mapping[str, list[str]]:
    """Group the built-in rules by level, for the dashboard's Security page."""
    out: dict[str, list[str]] = {level.value: [] for level in RiskLevel}
    for rule in engine.rules:
        out[rule.level.value].append(rule.name)
    return out
