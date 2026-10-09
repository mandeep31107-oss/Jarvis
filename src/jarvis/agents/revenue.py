"""Revenue Intelligence (spec section 3).

The engine never "blindly executes a make-money strategy". It walks the full
pipeline -- discover, research, requirements, law, platform terms, geography,
tax, cost, revenue, risk, scam check, expected return -- and then hands the user
comparable options and asks which one to pursue.

All money maths is deterministic, so it is reported with
``Confidence.HIGH``/``deterministic=True``. Everything judgemental (will customers
buy, how strong is demand) is reported at ``LOW``/``MEDIUM`` with the assumption
stated, because guessing at revenue is exactly the kind of confident nonsense the
anti-hallucination rules exist to prevent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.compliance.jurisdiction import JurisdictionKnowledge, related_jurisdictions
from jarvis.compliance.terms import TermsRegistry
from jarvis.core.confidence import Claim, Confidence
from jarvis.core.options import Dilemma, Option
from jarvis.core.risk import RiskLevel

MONTHS = 12

#: Phrases that reliably indicate a bad opportunity rather than a good one.
SCAM_PATTERNS: list[tuple[str, str]] = [
    (r"guarantee\w*\s+(returns?|profit|income)", "Promises guaranteed returns. Legitimate businesses cannot guarantee demand."),
    (r"get rich quick|quick riches|easy money", "'Get rich quick' framing is the signature of a scheme, not a market."),
    (r"pay\s+(an?\s+)?(upfront|registration|training)\s+fee\s+to\s+(start|earn|work)", "Requiring payment to be allowed to earn is a classic advance-fee pattern."),
    (r"(recruit|refer)\s+(at least\s+)?\d+\s+(people|members)", "Income driven by recruiting rather than selling is a pyramid structure."),
    (r"no\s+(skills?|experience|work)\s+(needed|required)", "'No skill required, high pay' has no economic basis."),
    (r"\b(10x|100x|1000x)\b\s*(returns?|in\s+a\s+month)", "Implausible return multiples."),
    (r"passive\s+income\s+from\s+day\s+one", "Day-one passive income from active work is a marketing claim, not a projection."),
    (r"\b(mlm|network\s+marketing|downline|upline)\b", "Multi-level structures are heavily regulated and usually lose money for participants."),
    (r"(hack|exploit|bypass)\s+(the\s+)?(algorithm|system|platform)", "Depends on circumventing a platform's controls - not a durable or permitted business."),
]


@dataclass
class Opportunity:
    """A revenue idea plus the assumptions needed to model it.

    Every numeric field is an *estimate supplied by the user or by research*;
    the assessment says so rather than presenting it as fact.
    """

    title: str
    category: str = "services"
    description: str = ""
    markets: list[str] = field(default_factory=lambda: ["IN"])
    #: Platforms the plan intends to use, for the terms check.
    platforms: list[str] = field(default_factory=list)
    platform_activities: list[str] = field(default_factory=lambda: ["api_automation"])

    upfront_cost: float = 0.0
    monthly_fixed_cost: float = 0.0
    variable_cost_per_unit: float = 0.0
    price_per_unit: float = 0.0
    clients_per_month_at_maturity: float = 4.0
    ramp_months: int = 4
    gross_margin: float = 0.8
    hours_to_launch: float = 40.0
    hours_per_week: float = 20.0
    churn_per_month: float = 0.05

    # Judgement inputs, 0..1 or 1..10, with the source of the estimate.
    demand_signal: float = 0.5
    competition: float = 5.0  # 1 = wide open, 10 = saturated
    skill_fit: float = 0.6
    time_to_first_revenue_days: float = 30.0
    estimate_source: str = "user estimate, not verified market research"

    def validate(self) -> list[str]:
        problems = []
        if self.price_per_unit <= 0:
            problems.append("price_per_unit must be positive")
        if self.upfront_cost < 0 or self.monthly_fixed_cost < 0:
            problems.append("costs cannot be negative")
        if not 0 < self.gross_margin <= 1:
            problems.append("gross_margin must be in (0, 1]")
        if self.ramp_months < 1:
            problems.append("ramp_months must be at least 1")
        if self.churn_per_month < 0 or self.churn_per_month >= 1:
            problems.append("churn_per_month must be in [0, 1)")
        if self.hours_to_launch < 0 or self.hours_per_week <= 0:
            problems.append("hours must be positive")
        return problems


@dataclass
class ProjectionRow:
    month: int
    ramp: float
    active_clients: float
    revenue: float
    variable_cost: float
    fixed_cost: float
    profit: float
    cumulative_profit: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "ramp": round(self.ramp, 3),
            "active_clients": round(self.active_clients, 2),
            "revenue": round(self.revenue, 2),
            "variable_cost": round(self.variable_cost, 2),
            "fixed_cost": round(self.fixed_cost, 2),
            "profit": round(self.profit, 2),
            "cumulative_profit": round(self.cumulative_profit, 2),
        }


@dataclass
class OpportunityAssessment:
    opportunity: Opportunity
    requirements: list[str] = field(default_factory=list)
    legal: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    terms_blocked: list[str] = field(default_factory=list)
    geographic: list[str] = field(default_factory=list)
    tax: list[str] = field(default_factory=list)
    costs: dict[str, float] = field(default_factory=dict)
    projection: list[ProjectionRow] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    scam_flags: list[str] = field(default_factory=list)
    probability: float = 0.0
    expected_value: float = 0.0
    break_even_month: int | None = None
    recommendation: str = ""
    options: Dilemma | None = None
    disclaimer: str = (
        "This is information and risk analysis, not a substitute for qualified legal or tax "
        "advice. All revenue figures are modelled from the supplied assumptions, not from "
        "observed demand."
    )

    @property
    def viable(self) -> bool:
        return not self.terms_blocked and not self.scam_flags and self.expected_value > 0

    @property
    def monthly_profit_at_maturity(self) -> float:
        return self.projection[-1].profit if self.projection else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.opportunity.title,
            "viable": self.viable,
            "requirements": list(self.requirements),
            "legal": list(self.legal),
            "terms": list(self.terms),
            "terms_blocked": list(self.terms_blocked),
            "geographic": list(self.geographic),
            "tax": list(self.tax),
            "costs": self.costs,
            "projection": [r.as_dict() for r in self.projection],
            "risks": list(self.risks),
            "scam_flags": list(self.scam_flags),
            "probability": round(self.probability, 3),
            "expected_value": round(self.expected_value, 2),
            "break_even_month": self.break_even_month,
            "recommendation": self.recommendation,
            "options": self.options.as_dict() if self.options else None,
            "disclaimer": self.disclaimer,
        }

    def render(self) -> str:
        o = self.opportunity
        lines = [f"OPPORTUNITY: {o.title}", f"category: {o.category}", ""]

        lines.append("REQUIRED")
        lines.extend(f"  - {r}" for r in self.requirements)

        lines.append("")
        lines.append("LEGAL / REGULATORY")
        lines.extend(f"  - {r}" for r in self.legal or ["  no verified entries on file - this is a gap, not clearance"])

        lines.append("")
        lines.append("PLATFORM TERMS")
        lines.extend(f"  - {r}" for r in self.terms)
        if self.terms_blocked:
            lines.append("  BLOCKED:")
            lines.extend(f"  ! {r}" for r in self.terms_blocked)

        lines.append("")
        lines.append("GEOGRAPHIC / TAX")
        lines.extend(f"  - {r}" for r in self.geographic)
        lines.extend(f"  - {r}" for r in self.tax)

        lines.append("")
        lines.append("COSTS")
        for k, v in sorted(self.costs.items()):
            lines.append(f"  {k:<32} {v:,.2f}")

        lines.append("")
        lines.append(f"REVENUE MODEL (from supplied assumptions; source: {o.estimate_source})")
        lines.append("  mo   clients     revenue      costs      profit   cumulative")
        for r in self.projection:
            lines.append(
                f"  {r.month:>2}  {r.active_clients:>8.2f}  {r.revenue:>10,.2f} "
                f"{r.variable_cost + r.fixed_cost:>10,.2f}  {r.profit:>9,.2f}  {r.cumulative_profit:>11,.2f}"
            )
        lines.append(f"  break-even month: {self.break_even_month if self.break_even_month else 'not within 12 months'}")
        lines.append(f"  probability of any traction: {self.probability:.0%}")
        lines.append(f"  expected value over 12 months: {self.expected_value:,.2f}")

        if self.scam_flags:
            lines.append("")
            lines.append("SCAM / RED FLAGS")
            lines.extend(f"  ! {f}" for f in self.scam_flags)

        lines.append("")
        lines.append("RISKS")
        lines.extend(f"  - {r}" for r in self.risks)

        lines.append("")
        lines.append(f"RECOMMENDATION: {self.recommendation}")
        if self.options:
            lines.append("")
            lines.append(self.options.render())
        lines.append("")
        lines.append(self.disclaimer)
        return "\n".join(lines)


class RevenueAgent(Agent):
    """Discovers, models and screens revenue opportunities."""

    name = "revenue"
    description = "Finds and models legitimate revenue opportunities, screens them for legal, platform and scam risk."
    capabilities = ("revenue", "market_analysis", "roi")

    def __init__(
        self,
        runtime: Any = None,
        *,
        jurisdictions: JurisdictionKnowledge | None = None,
        terms: TermsRegistry | None = None,
        executor: Any = None,
        monitor: Any = None,
    ) -> None:
        super().__init__(runtime)
        self.jurisdictions = jurisdictions or JurisdictionKnowledge()
        self.terms = terms or TermsRegistry()
        #: Injected rather than constructed here: the executor holds no authority
        #: of its own and must share the runtime's policy engine and audit log.
        self.executor = executor
        self.monitor = monitor

    # --------------------------------------------------------------- execution
    def plan_from_assessment(self, assessment: OpportunityAssessment) -> Any:
        """Turn an assessment into a concrete, gated list of steps.

        Requirements become steps. Those involving money, accounts, identity or
        terms of service are marked as needing a person, because automating them
        would either fail or breach the platform's rules.
        """
        from jarvis.revenue.execution import ExecutionPlan, Step

        plan = ExecutionPlan(
            title=f"Launch: {assessment.opportunity.title}",
            opportunity=assessment.opportunity.title,
        )
        #: Verbs that need a human being, mapped from the requirement text.
        human_markers = {
            "payment": "payment.collect",
            "bank": "bank.connect",
            "account": "account.create",
            "identity": "identity.verify",
            "kyc": "identity.verify",
            "tax": "tax.file",
            "contract": "contract.sign",
            "terms": "terms.accept",
        }
        for requirement in self.requirements(assessment.opportunity):
            low = requirement.lower()
            verb = next((v for marker, v in human_markers.items() if marker in low), "task.prepare")
            plan.add(
                Step(
                    verb=verb,
                    title=requirement,
                    risk=RiskLevel.HIGH if verb != "task.prepare" else RiskLevel.MEDIUM,
                    irreversible=verb in {"payment.collect", "tax.file", "contract.sign"},
                )
            )
        return plan

    def monitor_rules(self, assessment: OpportunityAssessment) -> list[Any]:
        """Thresholds worth watching for this opportunity.

        Derived from the model's own numbers rather than invented defaults, so
        the alert means something: the break-even point is where the plan stops
        working, not an arbitrary figure.
        """
        from jarvis.revenue.execution import MonitorRule

        rules = [
            MonitorRule(
                name="Acquisition cost above the modelled break-even",
                metric="cac",
                threshold=max(1.0, assessment.opportunity.price_per_unit * 0.5),
                direction="above",
                advice="Pause paid acquisition; the model assumed a cheaper customer.",
            ),
        ]
        if assessment.break_even_month is not None:
            rules.append(
                MonitorRule(
                    name=f"No break-even by month {assessment.break_even_month}",
                    metric="months_elapsed",
                    threshold=float(assessment.break_even_month),
                    direction="above",
                    advice="Re-run the analysis; the projection has not held.",
                )
            )
        return rules

    # ------------------------------------------------------------------ entry
    def run(self, request: AgentRequest) -> AgentResult:
        opportunity: Opportunity | None = request.param("opportunity")
        if opportunity is None:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary="No opportunity supplied. Provide a title and rough numbers to model.",
                follow_ups=["Describe the idea and the assumptions; I will model it and screen it."],
            )
        try:
            assessment = self.analyze(opportunity)
        except ValueError as exc:
            return self._needs_input(opportunity, str(exc))
        result = AgentResult(
            agent=self.name,
            ok=assessment.viable,
            summary=(
                f"{opportunity.title}: expected value {assessment.expected_value:,.0f} over 12 months, "
                f"probability {assessment.probability:.0%}."
                + (" REJECTED - see flags." if not assessment.viable else "")
            ),
            data=assessment.as_dict(),
        )
        result.add_claim(
            Claim.certain(
                f"At the supplied assumptions, cumulative profit after {MONTHS} months is "
                f"{assessment.projection[-1].cumulative_profit:,.2f} and break-even is month "
                f"{assessment.break_even_month or '>12'}.",
                reasoning="arithmetic over the supplied assumptions",
            )
        )
        result.add_claim(
            Claim(
                statement=(
                    f"The probability of gaining any traction is about {assessment.probability:.0%}."
                ),
                confidence=Confidence.LOW,
                caveats=[
                    "derived from unverified judgement inputs (demand, competition, skill fit)",
                    f"assumption source: {opportunity.estimate_source}",
                ],
                reasoning="heuristic scoring, not observed market data",
            )
        )
        if assessment.terms_blocked:
            result.add_claim(
                Claim(
                    statement=(
                        "At least one step of this plan conflicts with a platform policy and must "
                        "not be automated as described."
                    ),
                    confidence=Confidence.HIGH,
                    caveats=["policy entries are unverified - re-read the current terms"],
                )
            )
        if assessment.options:
            result.dilemmas.append(assessment.options)
        result.follow_ups.append("Tell me A/B/C and I will start only the parts that need no further approval.")
        return result

    def _needs_input(self, opportunity: Opportunity, problem: str) -> AgentResult:
        """An idea without numbers is not an assessment - ask, do not invent."""
        result = AgentResult(
            agent=self.name,
            ok=False,
            summary=(
                f"I cannot model '{opportunity.title}' yet: {problem}. "
                "Jarvis will not invent a price or a customer count."
            ),
        )
        result.add_claim(
            Claim(
                statement=f"The idea needs more inputs before it can be modelled: {problem}",
                confidence=Confidence.HIGH,
            )
        )
        dilemma = Dilemma(
            situation=f"You asked me to evaluate '{opportunity.title}'.",
            problem=problem,
            risk="Modelling with invented numbers produces a confident-looking, meaningless answer.",
        )
        dilemma.add(
            Option(
                label="A",
                title="Give me your real numbers",
                kind="safe",
                expected_result="A model you can actually defend, based on your inputs.",
                risk=RiskLevel.LOW,
                cost="a few minutes",
                time="immediate",
                compliance="compliant",
                notes=[
                    "I need: price per customer, how many customers at maturity, monthly fixed "
                    "cost, upfront cost, hours per week.",
                ],
            )
        )
        dilemma.add(
            Option(
                label="B",
                title="Model it with clearly-labelled placeholder assumptions",
                kind="fast",
                expected_result="A rough shape of the economics, explicitly marked as invented.",
                risk=RiskLevel.LOW,
                cost="none",
                time="immediate",
                compliance="compliant",
                notes=["Every number will be labelled 'placeholder - not researched'."],
            )
        )
        dilemma.cancel_option()
        dilemma.recommendation = "A"
        result.dilemmas.append(dilemma)
        result.follow_ups.append("Option A is the only one that produces a number worth acting on.")
        return result

    # ------------------------------------------------------------------ pipeline
    def analyze(self, opportunity: Opportunity) -> OpportunityAssessment:
        problems = opportunity.validate()
        if problems:
            raise ValueError("invalid opportunity: " + "; ".join(problems))

        assessment = OpportunityAssessment(opportunity=opportunity)
        assessment.scam_flags = self.screen_scams(opportunity)
        assessment.requirements = self.requirements(opportunity)
        assessment.legal, assessment.tax, assessment.geographic = self.compliance_review(opportunity)
        assessment.terms, assessment.terms_blocked = self.terms_review(opportunity)
        assessment.costs = self.costs(opportunity)
        assessment.projection = self.project(opportunity)
        assessment.break_even_month = self.break_even(assessment.projection)
        assessment.probability = self.probability_of_traction(opportunity)
        assessment.expected_value = self.expected_value(opportunity, assessment)
        assessment.risks = self.risks(opportunity, assessment)
        assessment.recommendation = self.recommend(assessment)
        assessment.options = self.build_options(assessment)
        return assessment

    # --- step: discover / research (what it takes) ---------------------------
    def requirements(self, o: Opportunity) -> list[str]:
        base = {
            "services": [
                "A portfolio or 2-3 reference samples before pitching",
                "A written scope + contract template",
                "An invoicing and payment collection flow",
                "A delivery checklist so quality does not depend on mood",
            ],
            "saas": [
                "A deployed, monitored backend with auth",
                "A billing provider with test mode wired first",
                "Terms of service and privacy policy for the product",
                "A support channel and an SLA you can actually meet",
            ],
            "digital_product": [
                "A licence that states what buyers may do with it",
                "A delivery mechanism and an update path",
                "A refund policy",
            ],
            "ecommerce": [
                "Supplier terms and lead times in writing",
                "A returns and refund process",
                "Product liability and consumer-protection compliance for the target market",
                "Accurate product claims - no unsubstantiated health or performance claims",
            ],
            "content": [
                "A platform account in good standing",
                "Disclosure of sponsorship/affiliate relationships where applicable",
                "Only rights-cleared assets",
            ],
            "automation": [
                "Written authorisation from every client whose systems you touch",
                "An audit log of what the automation did",
                "A rollback path for every automated change",
            ],
        }.get(o.category, ["A defined deliverable", "A way to be paid", "A way to support it"])
        out = list(base)
        if o.platforms:
            out.append(f"An account in good standing on: {', '.join(o.platforms)}")
        out.append("Record-keeping for income and expenses from day one")
        return out

    # --- steps: law, geography, tax -----------------------------------------
    def compliance_review(self, o: Opportunity) -> tuple[list[str], list[str], list[str]]:
        jurisdictions = related_jurisdictions(o.markets)
        topics = ("data_privacy", "tax", "consumer_protection", "communication")
        brief = self.jurisdictions.brief(o.title, jurisdictions, topics)

        legal: list[str] = []
        tax: list[str] = []
        for entry in brief.entries:
            label = f"[{entry.jurisdiction}] {entry.title}"
            if entry.topic == "tax":
                tax.append(f"{label} - {'; '.join(entry.restrictions[:2]) or 'see source'}")
            else:
                legal.append(f"{label} - {'; '.join(entry.restrictions[:2]) or 'see source'}")

        if brief.missing_coverage:
            legal.append(
                "GAP: no verified entry on file for "
                + ", ".join(brief.missing_coverage)
                + f" in {', '.join(jurisdictions)}. Absence of an entry is not clearance."
            )
        if brief.unverified:
            legal.append(
                f"{len(brief.unverified)} of {len(brief.entries)} entries are UNVERIFIED - "
                "confirm against the primary source before acting."
            )
        legal.append(
            "This is information and risk analysis, not a substitute for qualified legal or tax advice."
        )

        geo = [
            f"Target market {m} may impose registration, invoicing or consumer-protection duties "
            "on a foreign seller - check before the first sale."
            for m in o.markets
        ]
        if not o.markets:
            geo = ["No target market specified; jurisdictional duties cannot be assessed."]
        return legal, tax, geo

    # --- step: platform terms ------------------------------------------------
    def terms_review(self, o: Opportunity) -> tuple[list[str], list[str]]:
        notes: list[str] = []
        blocked: list[str] = []
        for platform in o.platforms:
            verdict = self.terms.evaluate_plan(platform, o.platform_activities or ["api_automation"])
            if verdict.must_stop and verdict.blocking:
                b = verdict.blocking
                blocked.append(
                    f"{platform}/{b.activity} is {b.verdict.upper()}: {b.reason} "
                    f"(sanctioned channel: {b.sanctioned_channel or 'none known'})"
                )
            else:
                platform_record = self.terms.get(platform)
                channel = platform_record.sanctioned_channel if platform_record else ""
                conditionals = [c for c in verdict.checks if c.verdict == "conditional"]
                if conditionals:
                    notes.append(
                        f"{platform}: permitted only within the sanctioned channel - {channel or '?'}"
                    )
                elif channel:
                    notes.append(
                        f"{platform}: no conflict found; stay inside the sanctioned channel ({channel})."
                    )
                else:
                    notes.append(f"{platform}: no conflict found in the entries on file.")
        if not o.platforms:
            notes.append("No third-party platform required by this plan.")
        return notes, blocked

    # --- step: costs ---------------------------------------------------------
    def costs(self, o: Opportunity) -> dict[str, float]:
        hours_year = o.hours_per_week * 52
        maturity_clients = o.clients_per_month_at_maturity
        variable_month = maturity_clients * o.variable_cost_per_unit
        return {
            "upfront_cost": round(o.upfront_cost, 2),
            "monthly_fixed_cost": round(o.monthly_fixed_cost, 2),
            "variable_cost_per_unit": round(o.variable_cost_per_unit, 2),
            "variable_cost_at_maturity_per_month": round(variable_month, 2),
            "total_cost_year_1": round(o.upfront_cost + (o.monthly_fixed_cost + variable_month * 0.5) * MONTHS, 2),
            "hours_to_launch": round(o.hours_to_launch, 2),
            "hours_per_week": round(o.hours_per_week, 2),
            "your_time_per_year_hours": round(hours_year, 2),
        }

    # --- step: revenue projection -------------------------------------------
    def project(self, o: Opportunity) -> list[ProjectionRow]:
        """Model monthly revenue from the supplied assumptions.

        ``clients_per_month_at_maturity`` is the number of *active* clients the
        business reaches at maturity, not an acquisition rate. Acquisition each
        month is whatever is needed to move towards that target after churn, so
        the active base converges on the target instead of growing without bound.
        """
        rows: list[ProjectionRow] = []
        active = 0.0
        cumulative = -o.upfront_cost
        for month in range(1, MONTHS + 1):
            ramp = min(1.0, month / max(1, o.ramp_months))
            target = o.clients_per_month_at_maturity * ramp
            retained = active * (1.0 - o.churn_per_month)
            acquisitions = max(0.0, target - retained)
            active = retained + acquisitions
            revenue = active * o.price_per_unit
            variable = active * o.variable_cost_per_unit
            cogs = revenue * (1.0 - o.gross_margin)
            fixed = o.monthly_fixed_cost
            profit = revenue - cogs - variable - fixed
            cumulative += profit
            rows.append(
                ProjectionRow(
                    month=month,
                    ramp=ramp,
                    active_clients=active,
                    revenue=revenue,
                    variable_cost=variable + cogs,
                    fixed_cost=fixed,
                    profit=profit,
                    cumulative_profit=cumulative,
                )
            )
        return rows

    @staticmethod
    def break_even(projection: Sequence[ProjectionRow]) -> int | None:
        for row in projection:
            if row.cumulative_profit >= 0:
                return row.month
        return None

    # --- step: risk / probability -------------------------------------------
    def probability_of_traction(self, o: Opportunity) -> float:
        """A transparent heuristic, not a forecast.

        Kept deliberately conservative and capped below 1.0 - nothing here can
        honestly claim a business will succeed.
        """
        skill = max(0.0, min(1.0, o.skill_fit))
        demand = max(0.0, min(1.0, o.demand_signal))
        competition_penalty = max(0.0, 1.0 - (o.competition - 1) / 12.0)
        speed = max(0.0, min(1.0, 1.0 - o.time_to_first_revenue_days / 365.0))
        raw = 0.30 * skill + 0.30 * demand + 0.25 * competition_penalty + 0.15 * speed
        return round(max(0.02, min(0.75, raw)), 3)

    def expected_value(self, o: Opportunity, assessment: OpportunityAssessment) -> float:
        """Probability-weighted 12-month outcome.

        The downside case is "you spent the money and earned nothing", which is
        the honest model for a new venture, not a symmetric +/- swing.
        """
        upside = assessment.projection[-1].cumulative_profit if assessment.projection else 0.0
        downside = -(o.upfront_cost + o.monthly_fixed_cost * min(MONTHS, max(1, o.ramp_months + 2)))
        p = assessment.probability
        value = p * upside + (1 - p) * downside
        if assessment.terms_blocked:
            value = min(value, downside)  # a blocked plan is not an opportunity
        return round(value, 2)

    def risks(self, o: Opportunity, assessment: OpportunityAssessment) -> list[str]:
        risks = [
            f"All revenue figures depend on {o.estimate_source}; they are not observed demand.",
            f"Break-even is month {assessment.break_even_month or '>12'} at these assumptions - "
            "a slower ramp pushes it further out.",
            f"Concentration risk: at maturity the model assumes {o.clients_per_month_at_maturity:.1f} "
            "active clients, so losing one is a large percentage swing.",
        ]
        if o.competition >= 7:
            risks.append(
                f"Competition scored {o.competition}/10 - differentiation, not effort, is the binding constraint."
            )
        if o.time_to_first_revenue_days > 90:
            risks.append(
                f"{o.time_to_first_revenue_days:.0f} days to first revenue means months of unpaid work; "
                "confirm you can fund that."
            )
        if o.upfront_cost > 0:
            risks.append(
                f"{o.upfront_cost:,.0f} of upfront spend is at risk in the downside case; treat it as sunk."
            )
        if o.platforms:
            risks.append(
                "Platform dependence: a policy change or account action can end the channel overnight. "
                "Own an email list or a direct relationship wherever possible."
            )
        if assessment.terms_blocked:
            risks.append("The plan as described requires a policy-violating step. It must be redesigned.")
        risks.append("Tax and registration duties attach at the first sale, not at scale.")
        return risks

    # --- step: scam screening ------------------------------------------------
    def screen_scams(self, o: Opportunity) -> list[str]:
        blob = " ".join([o.title, o.description, o.category]).lower()
        flags = [msg for pattern, msg in SCAM_PATTERNS if re.search(pattern, blob)]

        # Implausible economics: high margin, no time, no skill, big money.
        if o.price_per_unit * o.clients_per_month_at_maturity > 0 and o.hours_per_week <= 2:
            annual = o.price_per_unit * o.clients_per_month_at_maturity * 12
            if annual / max(1.0, o.hours_per_week * 52) > 500:
                flags.append(
                    "Implied hourly rate is implausibly high for the stated effort - "
                    "either the numbers are wrong or the model is a scheme."
                )
        if o.gross_margin > 0.98 and o.category not in {"digital_product", "content"}:
            flags.append(
                "A near-100% margin outside pure digital goods usually hides a cost you have not counted."
            )
        return flags

    # --- step: recommend + options ------------------------------------------
    def recommend(self, a: OpportunityAssessment) -> str:
        if a.scam_flags:
            return (
                "REJECT. This has the shape of a scheme, not a business. "
                "I will not build or promote it. Ask me for legitimate alternatives in the same category."
            )
        if a.terms_blocked:
            return (
                "REJECT AS DESCRIBED. A required step conflicts with a platform policy. "
                "Redesign around the sanctioned channel or pick a different route to market."
            )
        if a.expected_value <= 0:
            return (
                "Not worth starting at these assumptions. The modelled downside exceeds the "
                "probability-weighted upside. Improve price, demand evidence or ramp before committing money."
            )
        if a.break_even_month is None:
            return (
                "Marginal. It does not break even inside 12 months. Worth a small, time-boxed "
                "experiment only, with a stop date agreed up front."
            )
        return (
            f"Proceed with a time-boxed pilot: smallest sellable version, no ad spend until the first "
            f"{max(2, int(a.opportunity.clients_per_month_at_maturity / 2))} paying customers exist."
        )

    def build_options(self, a: OpportunityAssessment) -> Dilemma:
        """Section 3/7: A safe, B fast, C profitable, D conservative, E cancel."""
        o = a.opportunity
        dilemma = Dilemma(
            situation=f"You asked me to evaluate '{o.title}'.",
            problem=(
                "I can model it, but I cannot verify real demand, and parts of it may need "
                "approval, credentials or platform accounts that only you can provide."
            ),
            risk=(
                "Starting at full size risks "
                f"{a.costs.get('upfront_cost', 0):,.0f} of upfront spend and "
                f"{a.costs.get('your_time_per_year_hours', 0):,.0f} hours/year for a "
                f"{a.probability:.0%} chance of traction."
            ),
        )
        dilemma.add(
            Option(
                label="A",
                title=f"Safe: time-boxed pilot of '{o.title}' with no paid spend",
                kind="safe",
                expected_result=(
                    f"Build the smallest sellable version in ~{max(4, o.hours_to_launch / 4):.0f} hours; "
                    "aim for 2-3 paying customers before spending anything."
                ),
                risk=RiskLevel.LOW,
                cost=f"~{max(4, o.hours_to_launch / 4):.0f} hours, {o.upfront_cost * 0.1:,.0f} or less",
                time=f"{max(1, int(o.ramp_months / 2))}-4 weeks",
                compliance="compliant",
                notes=["Stops automatically at the agreed stop date if there is no paying customer."],
            )
        )
        dilemma.add(
            Option(
                label="B",
                title="Fast: build the full version now and start selling immediately",
                kind="fast",
                expected_result=(
                    f"Modelled cumulative profit {a.projection[-1].cumulative_profit:,.0f} at month 12, "
                    f"break-even month {a.break_even_month or '>12'}."
                ),
                risk=RiskLevel.MEDIUM,
                cost=f"{a.costs.get('total_cost_year_1', 0):,.0f} over the year plus {a.costs.get('your_time_per_year_hours', 0):,.0f} hours",
                time=f"{int(o.hours_to_launch)}+ hours to launch",
                compliance="compliant with conditions" if not a.terms_blocked else "blocked",
                notes=(["One or more steps conflict with platform policy."] if a.terms_blocked else []),
            )
        )
        dilemma.add(
            Option(
                label="C",
                title="More profitable: raise price and narrow the offer before building anything",
                kind="profitable",
                expected_result=(
                    "Higher revenue per client and a clearer positioning, at the cost of a "
                    "smaller addressable market."
                ),
                risk=RiskLevel.MEDIUM,
                cost="research time",
                time="1-2 weeks",
                compliance="compliant",
                notes=[
                    f"Modelled at {o.price_per_unit:,.0f} per client; a 50% price rise roughly halves "
                    "the clients needed for the same revenue.",
                    "Requires real conversations with prospective customers, which I can draft but not fake.",
                ],
            )
        )
        dilemma.add(
            Option(
                label="D",
                title="Conservative: research only, build nothing yet",
                kind="conservative",
                expected_result="A written market and competitor brief, and the compliance gaps closed.",
                risk=RiskLevel.LOW,
                cost="research time",
                time="a few days",
                compliance="compliant",
                notes=[f"Unverified compliance entries to close: {len(a.legal)}"],
            )
        )
        dilemma.cancel_option("Keep evaluating other ideas; nothing is built or spent.")
        dilemma.recommendation = "A" if a.viable else "E"
        return dilemma

    # --- portfolio view ------------------------------------------------------
    def rank(self, opportunities: Sequence[Opportunity]) -> list[tuple[Opportunity, OpportunityAssessment]]:
        """Compare several ideas on expected value, flagging the ones to reject."""
        scored = [(o, self.analyze(o)) for o in opportunities]
        scored.sort(key=lambda pair: (-int(pair[1].viable), -pair[1].expected_value))
        return scored

    def render_ranking(self, rows: Sequence[tuple[Opportunity, OpportunityAssessment]]) -> str:
        lines = ["rank  idea                                   viable   EV(12mo)   prob  break-even"]
        for i, (o, a) in enumerate(rows, start=1):
            lines.append(
                f"{i:>4}  {o.title[:38]:<38}  {str(a.viable):<6} "
                f"{a.expected_value:>10,.0f} {a.probability:>6.0%}  "
                f"{a.break_even_month if a.break_even_month else '>12'}"
            )
        return "\n".join(lines)
