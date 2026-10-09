"""Exception hierarchy.

Every exception carries enough context for the audit log and the failure
recovery engine (spec section 34) to classify the cause and decide whether a
retry is safe.
"""

from __future__ import annotations


class JarvisError(Exception):
    """Base class for all Jarvis errors."""

    #: True when re-running the same action is expected to be harmless.
    safe_to_retry: bool = False
    #: Machine readable cause bucket, used by the recovery engine.
    cause: str = "unknown"


class ConfigurationError(JarvisError):
    cause = "configuration"


class PolicyViolation(JarvisError):
    """Raised when an action is blocked by the policy/permission engine."""

    cause = "policy"
    safe_to_retry = False

    def __init__(self, message: str, *, rule: str = "", risk: str = "") -> None:
        super().__init__(message)
        self.rule = rule
        self.risk = risk


class ApprovalRequired(JarvisError):
    """Raised when an action needs human approval and none was supplied."""

    cause = "approval_required"
    safe_to_retry = False

    def __init__(self, message: str, *, action_id: str = "") -> None:
        super().__init__(message)
        self.action_id = action_id


class HardDenial(PolicyViolation):
    """An action that Jarvis will never perform, with or without approval.

    This is spec section 39: "the system must never interpret 'do whatever it
    takes' as permission to break the rules."
    """

    cause = "hard_denial"


class ProviderUnavailable(JarvisError):
    """A model/tool/provider could not be reached."""

    cause = "provider_unavailable"
    safe_to_retry = True


class TimeoutError_(JarvisError):  # noqa: N818 - deliberately distinct from builtin
    cause = "timeout"
    safe_to_retry = True


class TaskFailed(JarvisError):
    """A task exhausted its retries and alternatives."""

    cause = "task_failed"

    def __init__(self, message: str, *, task_id: str = "", attempts: int = 0) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.attempts = attempts


class CapabilityUnavailable(JarvisError):
    """A capability needs hardware, a permission or an extra that is not present."""

    cause = "capability_unavailable"
    safe_to_retry = False


class ConsentRequired(JarvisError):
    """A capability needs explicit, revocable user consent (camera, OS control...)."""

    cause = "consent_required"
    safe_to_retry = False
