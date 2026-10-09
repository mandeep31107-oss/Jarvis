"""Risk assessment and the permission/decision engine (spec sections 6, 33, 39)."""

from __future__ import annotations

import pytest

from jarvis.core.policy import (
    ALLOW,
    DENY,
    FORBIDDEN,
    HARD_DENY,
    NEEDS_APPROVAL,
    AutoApprover,
    PolicyEngine,
)
from jarvis.core.risk import ActionRequest, RiskCategory, RiskEngine, RiskLevel
from jarvis.errors import HardDenial


# ---------------------------------------------------------------- risk levels
def test_read_only_is_low(policy):
    decision = policy.decide(ActionRequest(verb="memory.search"))
    assert decision.assessment.level is RiskLevel.LOW
    assert decision.decision == ALLOW


def test_sending_a_message_is_medium(policy):
    decision = policy.decide(ActionRequest(verb="message.send", target="user@example.com"))
    assert decision.assessment.level is RiskLevel.MEDIUM
    assert decision.decision == NEEDS_APPROVAL


def test_spending_money_is_high_or_worse(policy):
    decision = policy.decide(
        ActionRequest(verb="purchase.subscription", context={"amount": 49, "currency": "USD"})
    )
    assert decision.assessment.level.rank >= RiskLevel.HIGH.rank


def test_moving_money_is_critical(policy):
    decision = policy.decide(
        ActionRequest(verb="payment.transfer", context={"amount": 5000, "currency": "USD"})
    )
    assert decision.assessment.level is RiskLevel.CRITICAL


def test_irreversible_flag_forces_critical(policy):
    decision = policy.decide(ActionRequest(verb="file.read", irreversible=True))
    assert decision.assessment.level is RiskLevel.CRITICAL


def test_unclassified_action_defaults_to_high(risk):
    """An unknown action shape must never be assumed safe."""
    assessment = risk.assess(ActionRequest(verb="something.nobody.modelled"))
    assert assessment.level is RiskLevel.HIGH
    assert any("No rule matched" in r for r in assessment.reasons())


def test_third_party_private_data_is_critical(policy):
    decision = policy.decide(
        ActionRequest(verb="message.send", context={"persons_data": "contacts of 500 people"})
    )
    assert decision.assessment.level is RiskLevel.CRITICAL


# --------------------------------------------------------------- hard denials
@pytest.mark.parametrize(
    "verb,context",
    [
        ("captcha.solve", {}),
        ("auth.bypass", {}),
        ("ratelimit.bypass", {}),
        ("paywall.bypass", {}),
        ("spam.send", {}),
        ("fraud.transaction", {}),
        ("identity.impersonate", {}),
        ("secret.export", {}),
        ("camera.record_covert", {}),
        ("safety.disable", {}),
    ],
)
def test_hard_denials_raise_even_with_approval(permissive_policy, verb, context):
    action = ActionRequest(verb=verb, context=context)
    decision = permissive_policy.decide(action)
    assert decision.decision == HARD_DENY
    assert decision.permanent is True
    assert decision.safer_alternatives, "a refusal must always offer a safer route"
    with pytest.raises(HardDenial):
        permissive_policy.gate(action)


def test_hard_denial_message_names_the_rule(policy):
    with pytest.raises(HardDenial) as excinfo:
        policy.gate(ActionRequest(verb="captcha.solve"))
    assert excinfo.value.rule == "captcha.bypass"


def test_do_whatever_it_takes_does_not_unlock_a_denial(permissive_policy):
    """Spec section 39: profitability is never permission to break a rule."""
    action = ActionRequest(
        verb="ratelimit.bypass",
        context={"reason": "do whatever it takes to make money", "amount": 100000},
    )
    with pytest.raises(HardDenial):
        permissive_policy.gate(action)


# ------------------------------------------------------------------- autonomy
def test_review_mode_allows_medium_without_asking(risk):
    from jarvis.core.policy import PolicyEngine

    engine = PolicyEngine(risk, autonomy="review")
    assert engine.decide(ActionRequest(verb="message.send")).decision == ALLOW


def test_ask_mode_stops_even_low_risk(risk):
    from jarvis.core.policy import PolicyEngine

    engine = PolicyEngine(risk, autonomy="ask")
    assert engine.decide(ActionRequest(verb="memory.search")).decision == NEEDS_APPROVAL


def test_unknown_autonomy_mode_is_rejected(risk):
    from jarvis.core.policy import PolicyEngine

    with pytest.raises(ValueError):
        PolicyEngine(risk, autonomy="yolo")


# ------------------------------------------------------------------ approvals
def test_approval_granted_allows_the_action(risk):
    from jarvis.core.policy import Approval, PolicyEngine

    engine = PolicyEngine(risk, autonomy="auto")
    action = ActionRequest(verb="message.send", target="someone")
    decision = engine.gate(action, preapproved=Approval(granted=True, approver="boss"))
    assert decision.allowed
    assert "Approved by boss" in decision.reason


def test_approval_denied_blocks_the_action(risk):
    from jarvis.core.policy import Approval, PolicyEngine

    engine = PolicyEngine(risk, autonomy="auto")
    decision = engine.gate(
        ActionRequest(verb="message.send"), preapproved=Approval(granted=False, note="no")
    )
    assert decision.decision == DENY
    assert not decision.allowed


def test_no_approver_means_no_action(policy):
    """The safe default: headless operation queues, it does not act."""
    decision = policy.gate(ActionRequest(verb="message.send"))
    assert decision.decision == DENY


def test_category_override_can_disable_a_capability(risk):
    from jarvis.core.policy import PolicyEngine

    engine = PolicyEngine(risk, autonomy="review")
    engine.override_category(RiskCategory.COMMUNICATION, DENY)
    assert engine.decide(ActionRequest(verb="message.send")).decision == DENY


def test_category_override_rejects_unknown_values(policy):
    with pytest.raises(ValueError):
        policy.override_category(RiskCategory.COMMUNICATION, "maybe")


def test_user_rules_cannot_lower_a_builtin_severity(risk):
    from jarvis.core.risk import RiskRule

    risk.add_rule(
        RiskRule("downgrade", RiskLevel.LOW, RiskCategory.FINANCIAL, "user says it is fine",
                 verbs=("payment.*",))
    )
    # The built-in CRITICAL rule still fires; the level is the max of the hits.
    assert risk.assess(ActionRequest(verb="payment.transfer")).level is RiskLevel.CRITICAL


def test_explain_lists_the_reasons(risk):
    text = risk.explain(ActionRequest(verb="file.delete", target="report.xlsx"))
    assert "HIGH" in text
    assert "file.delete" in text


def test_describe_exposes_the_configuration(policy):
    described = policy.describe()
    assert described["autonomy"] == "auto"
    assert "captcha.bypass" in described["hard_denials"]


def test_risk_matrix_summary_covers_every_level(risk):
    from jarvis.core.risk import risk_matrix_summary

    summary = risk_matrix_summary(risk)
    assert set(summary) == {"low", "medium", "high", "critical"}
    assert summary["critical"], "no critical rules registered"


# --------------------------------------------------------------------------
# The most important invariant in the system: a forbidden action cannot be
# approved by anyone, including an approver that says yes to everything.
# --------------------------------------------------------------------------


@pytest.fixture()
def rubber_stamp() -> PolicyEngine:
    """A policy engine whose approver approves absolutely everything."""
    return PolicyEngine(RiskEngine(), approver=AutoApprover(RiskLevel.CRITICAL))


@pytest.mark.parametrize("rule_name,verbs", [(f[0], f[1]) for f in FORBIDDEN])
def test_every_forbidden_rule_denies_every_verb_it_names(rubber_stamp, rule_name, verbs):
    """Walk all 12 forbidden rules under an all-approving approver.

    This is the test that would catch a regression where a hard denial quietly
    became approvable - exactly the bug that existed while the rule engine
    required both the verb *and* the context regex to match, which meant the
    hard denials never fired at all.
    """
    for verb in verbs:
        with pytest.raises(HardDenial) as excinfo:
            rubber_stamp.gate(ActionRequest(verb=verb))
        assert excinfo.value.rule == rule_name, f"{verb} matched the wrong rule"


@pytest.mark.parametrize("actor", ["jarvis", "owner", "boss", "admin", "root"])
def test_forbidden_rules_are_denied_even_for_the_owner(rubber_stamp, actor):
    """Owner/boss mode raises the ceiling; it does not remove the floor.

    The brief is explicit that no user may be given a way to make Jarvis do
    something forbidden, and equally explicit that the authorised user must
    always be able to *stop* it. Stopping is always available; forbidden
    actions never are.
    """
    for _rule_name, verbs, *_rest in FORBIDDEN:
        for verb in verbs:
            with pytest.raises(HardDenial):
                rubber_stamp.gate(
                    ActionRequest(verb=verb, actor=actor, context={"approved": True})
                )


def test_the_forbidden_set_covers_every_category_the_brief_names():
    """Cross-check the 12 rules against the categories named in the brief."""
    covered = {f[0] for f in FORBIDDEN}
    required = {
        "auth.bypass",          # never bypass authentication
        "captcha.bypass",       # never solve or bypass CAPTCHA
        "ratelimit.bypass",     # never bypass rate limits
        "paywall.bypass",       # never bypass paywalls
        "spam",                 # never spam
        "fraud",                # never make fraudulent transactions
        "impersonation",        # never impersonate a human
        "ip.theft",             # never steal intellectual property
        "secret.exfiltration",  # never expose secrets
        "covert.surveillance",  # never record covertly
        "safety.override",      # never disable a safety control
        "unauthorised.access",  # never access what the user is not authorised for
    }
    assert required <= covered, f"missing hard denials: {sorted(required - covered)}"


def test_the_rubber_stamp_fixture_is_not_simply_denying_everything(rubber_stamp):
    """Control for the parametrised tests above.

    If the fixture denied everything, every "still raises HardDenial" assertion
    would pass vacuously. A normal high-risk action must go through, which
    proves the engine really is approving and that the forbidden set is what
    stops those specific verbs.
    """
    high_risk = ActionRequest(verb="app.install", target="libreoffice")
    assert rubber_stamp.decide(high_risk).decision != HARD_DENY
    assert rubber_stamp.gate(high_risk)  # approved, not raised
