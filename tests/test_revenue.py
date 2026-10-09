"""Revenue Intelligence (spec section 3): modelling, screening, and options."""

from __future__ import annotations

import pytest

from jarvis.agents.revenue import Opportunity, RevenueAgent
from jarvis.core.options import Dilemma
from jarvis.core.risk import RiskLevel


def _opportunity(**overrides) -> Opportunity:
    base = dict(
        title="Freelance web development",
        category="services",
        description="Build sites for small local businesses.",
        markets=["IN"],
        upfront_cost=100.0,
        monthly_fixed_cost=50.0,
        variable_cost_per_unit=20.0,
        price_per_unit=1000.0,
        clients_per_month_at_maturity=5.0,
        ramp_months=3,
        gross_margin=0.85,
        hours_to_launch=40.0,
        hours_per_week=20.0,
        churn_per_month=0.05,
        demand_signal=0.6,
        competition=5.0,
        skill_fit=0.75,
        time_to_first_revenue_days=30.0,
    )
    base.update(overrides)
    return Opportunity(**base)


@pytest.fixture()
def agent() -> RevenueAgent:
    return RevenueAgent()


# ------------------------------------------------------------------- modelling
def test_active_clients_converge_on_the_target(agent):
    """clients_per_month_at_maturity is the ACTIVE base, not an acquisition rate.

    An earlier version treated it as new clients every month, so with 5% churn
    the model ran away to 20x the intended size.
    """
    assessment = agent.analyze(_opportunity(clients_per_month_at_maturity=5.0))
    final = assessment.projection[-1].active_clients
    assert final == pytest.approx(5.0, rel=0.02)
    assert max(r.active_clients for r in assessment.projection) <= 5.01


def test_ramp_is_monotonic_then_flat(agent):
    rows = agent.analyze(_opportunity(ramp_months=4)).projection
    ramps = [r.ramp for r in rows]
    assert ramps == sorted(ramps)
    assert ramps[-1] == 1.0
    assert rows[3].ramp == 1.0


def test_revenue_equals_clients_times_price(agent):
    rows = agent.analyze(_opportunity(price_per_unit=2000.0)).projection
    for row in rows:
        assert row.revenue == pytest.approx(row.active_clients * 2000.0)


def test_break_even_is_found_when_it_happens(agent):
    assessment = agent.analyze(_opportunity())
    assert assessment.break_even_month is not None
    assert assessment.projection[assessment.break_even_month - 1].cumulative_profit >= 0


def test_break_even_is_none_when_the_model_loses_money(agent):
    assessment = agent.analyze(
        _opportunity(price_per_unit=10.0, monthly_fixed_cost=5000.0, upfront_cost=20000.0)
    )
    assert assessment.break_even_month is None
    assert assessment.expected_value < 0


def test_invalid_assumptions_are_rejected(agent):
    with pytest.raises(ValueError):
        agent.analyze(_opportunity(price_per_unit=0))
    with pytest.raises(ValueError):
        agent.analyze(_opportunity(gross_margin=1.5))
    with pytest.raises(ValueError):
        agent.analyze(_opportunity(churn_per_month=1.0))
    with pytest.raises(ValueError):
        agent.analyze(_opportunity(upfront_cost=-5))


# --------------------------------------------------------------- scam screening
@pytest.mark.parametrize(
    "description",
    [
        "guaranteed returns every month",
        "get rich quick with no effort",
        "pay a registration fee to start earning",
        "recruit 5 people and earn from their sales",
        "no skills needed, 10x returns in a month",
        "MLM downline commissions",
        "hack the algorithm to grow fast",
    ],
)
def test_scheme_language_is_flagged(agent, description):
    assessment = agent.analyze(_opportunity(description=description))
    assert assessment.scam_flags, f"expected a flag for: {description}"
    assert not assessment.viable
    assert "REJECT" in assessment.recommendation


def test_implausible_hourly_rate_is_flagged(agent):
    assessment = agent.analyze(
        _opportunity(price_per_unit=50000.0, clients_per_month_at_maturity=20.0, hours_per_week=1.0)
    )
    assert any("hourly rate" in f.lower() for f in assessment.scam_flags)


def test_a_normal_business_is_not_flagged(agent):
    assert agent.analyze(_opportunity()).scam_flags == []


# ------------------------------------------------------------- platform terms
def test_a_policy_violating_channel_blocks_the_opportunity(agent):
    assessment = agent.analyze(
        _opportunity(
            title="Bulk WhatsApp lead generation",
            description="Message scraped numbers at scale.",
            platforms=["whatsapp"],
            platform_activities=["ui_automation"],
        )
    )
    assert assessment.terms_blocked
    assert not assessment.viable
    assert "REJECT AS DESCRIBED" in assessment.recommendation
    assert assessment.expected_value <= 0


def test_a_sanctioned_channel_is_noted_but_allowed(agent):
    assessment = agent.analyze(
        _opportunity(platforms=["telegram"], platform_activities=["api_automation"])
    )
    assert not assessment.terms_blocked
    assert any("sanctioned channel" in t for t in assessment.terms)


def test_an_unknown_platform_is_treated_as_do_not_automate(agent):
    assessment = agent.analyze(
        _opportunity(platforms=["totally-made-up-platform"], platform_activities=["api_automation"])
    )
    assert assessment.terms_blocked


# ------------------------------------------------------------------ compliance
def test_legal_review_reports_unverified_entries(agent):
    assessment = agent.analyze(_opportunity(markets=["IN", "US"]))
    assert any("UNVERIFIED" in line for line in assessment.legal)
    assert any("not a substitute" in line for line in assessment.legal)


def test_coverage_gaps_are_reported_as_gaps_not_clearance(agent):
    assessment = agent.analyze(_opportunity(markets=["ZZ"]))
    assert any("GAP" in line for line in assessment.legal)


# -------------------------------------------------------------------- options
def test_every_assessment_ends_with_a_valid_set_of_options(agent):
    dilemma = agent.analyze(_opportunity()).options
    assert isinstance(dilemma, Dilemma)
    assert dilemma.validate() == [], dilemma.validate()
    labels = {o.label for o in dilemma.options}
    assert {"A", "B", "C", "D", "E"} <= labels
    assert dilemma.recommendation in labels


def test_a_rejected_idea_recommends_cancel(agent):
    dilemma = agent.analyze(_opportunity(description="guaranteed returns")).options
    assert dilemma.recommendation == "E"


def test_every_option_carries_risk_cost_time_and_compliance(agent):
    for option in agent.analyze(_opportunity()).options.options:
        assert isinstance(option.risk, RiskLevel)
        assert option.cost
        assert option.time
        assert option.compliance


def test_options_render_without_error(agent):
    text = agent.analyze(_opportunity()).options.render()
    assert "SITUATION" in text and "PROBLEM" in text and "OPTIONS" in text


# ------------------------------------------------------------------- reporting
def test_ranking_puts_viable_ideas_first(agent):
    good = _opportunity(title="Good idea")
    bad = _opportunity(title="Bad idea", description="guaranteed returns")
    rows = agent.rank([bad, good])
    assert rows[0][0].title == "Good idea"
    assert "rank" in agent.render_ranking(rows)


def test_assessment_renders_the_whole_pipeline(agent):
    text = agent.analyze(_opportunity()).render()
    for section in ("REQUIRED", "LEGAL", "PLATFORM TERMS", "COSTS", "REVENUE MODEL", "RISKS",
                    "RECOMMENDATION"):
        assert section in text


def test_agent_run_wraps_the_assessment(agent):
    from jarvis.agents.base import AgentRequest

    result = agent.run(AgentRequest(intent="revenue", params={"opportunity": _opportunity()}))
    assert result.ok
    assert result.confidence.value in {"low", "medium", "high"}
    # The deterministic maths is HIGH; the demand judgement must not be.
    deterministic = [c for c in result.claims if c.deterministic]
    judgemental = [c for c in result.claims if not c.deterministic]
    assert deterministic and judgemental
    assert all(c.confidence.value == "high" for c in deterministic)
    assert all(c.confidence.value != "high" for c in judgemental)


def test_agent_run_without_an_opportunity_is_honest(agent):
    from jarvis.agents.base import AgentRequest

    result = agent.run(AgentRequest(intent="revenue"))
    assert not result.ok
    assert "No opportunity supplied" in result.summary
