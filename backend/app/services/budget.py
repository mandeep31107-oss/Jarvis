"""Budget estimation.

The estimator is a parametric model: every category is priced from a quantity
(built-up area, plot area, number of openings) times an *indicative* rate range.
Rates are ranges, never point quotations, and every line carries its own source
note so the user can see where the number came from and replace it with a real
local rate.

Nothing here is a contractor quotation, and no current market price is invented:
if a user's local rate is unknown the line stays a range and is flagged
``verified=False`` until the user enters their own rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..schemas import (BudgetAssumptions, BudgetComparison, BudgetEstimate, BudgetLine,
                       ProjectData, utcnow)
from .units import m2_to_sqft, rate_m2_to_sqft

# ---------------------------------------------------------------------------
# rate table
# ---------------------------------------------------------------------------
# Amounts are Indian Rupees. "builtup" = per m² of built-up area,
# "plot" = per m² of plot, "count" = per door/window, "percent" = % of the
# construction subtotal. Ranges are indicative residential construction bands
# for India; they must be confirmed against local rates before use.

RateBand = Tuple[float, float]


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    basis: str                      # builtup | plot | count | percent
    rates: Dict[str, RateBand]      # finish level -> (low, high)
    labour_share: float             # 0..1 of the line that is labour
    source: str
    note: str = ""
    percent_of: str = "construction"


RATE_SOURCE = ("Indicative Indian residential construction band used by this tool. "
               "Not a quotation -- confirm with local contractors and current market rates.")

CATEGORIES: Tuple[Category, ...] = (
    Category("foundation", "Foundation & substructure", "builtup",
             {"economical": (800, 1200), "standard": (1100, 1600), "premium": (1500, 2400)},
             0.35, RATE_SOURCE,
             "Assumes isolated/strip footings on ordinary soil. Piling, raft foundations or "
             "basements cost far more and are not covered."),
    Category("structure", "Structure: walls, columns, beams, slabs", "builtup",
             {"economical": (3800, 5200), "standard": (5200, 7000), "premium": (6800, 9500)},
             0.32, RATE_SOURCE,
             "Load-bearing masonry for economical, RCC framed for standard and premium."),
    Category("masonry_plaster", "Masonry finishing & plastering", "builtup",
             {"economical": (900, 1300), "standard": (1500, 2100), "premium": (1900, 2800)},
             0.55, RATE_SOURCE),
    Category("roofing", "Roofing, insulation & waterproofing", "builtup",
             {"economical": (300, 550), "standard": (500, 800), "premium": (800, 1600)},
             0.30, RATE_SOURCE,
             "Flat RCC slab with waterproofing treatment. A sloped tiled roof adds cost."),
    Category("doors_windows", "Doors, windows & joinery", "count",
             {"economical": (7000, 12000), "standard": (12000, 22000), "premium": (22000, 55000)},
             0.20, RATE_SOURCE,
             "Priced per opening. Aluminium/UPVC and teak sit at the top of the band."),
    Category("flooring", "Flooring", "builtup",
             {"economical": (550, 900), "standard": (900, 1600), "premium": (1600, 3800)},
             0.28, RATE_SOURCE,
             "Vitrified tile at economical, tile/granite at standard, marble or engineered wood at premium."),
    Category("wall_finish", "Wall finishes: paint, tiles, texture", "builtup",
             {"economical": (300, 500), "standard": (500, 900), "premium": (900, 1900)},
             0.45, RATE_SOURCE),
    Category("ceiling_woodwork", "Ceiling & interior woodwork", "builtup",
             {"economical": (350, 700), "standard": (700, 1400), "premium": (1400, 3200)},
             0.30, RATE_SOURCE,
             "False ceiling, wardrobes, TV units, loft and door frames."),
    Category("electrical", "Electrical work", "builtup",
             {"economical": (450, 700), "standard": (700, 1100), "premium": (1100, 2200)},
             0.40, RATE_SOURCE,
             "Wiring, switches, distribution board and basic fixtures. Home automation and "
             "solar are extra."),
    Category("plumbing", "Plumbing & sanitary", "builtup",
             {"economical": (600, 950), "standard": (900, 1500), "premium": (1500, 3200)},
             0.35, RATE_SOURCE,
             "Pipes, fittings, water tank and sanitaryware. Sewage treatment, rainwater "
             "harvesting and solar water heating are extra."),
    Category("kitchen_wardrobe", "Kitchen & storage fit-out", "builtup",
             {"economical": (450, 900), "standard": (1000, 2200), "premium": (2200, 5200)},
             0.25, RATE_SOURCE,
             "Modular kitchen, countertop, sink and appliances are a wide band -- enter your "
             "own rate if you have a quote."),
    Category("external_dev", "External development & site works", "plot",
             {"economical": (150, 320), "standard": (250, 600), "premium": (600, 1400)},
             0.40, RATE_SOURCE,
             "Boundary wall, gate, paving, drainage, landscaping and external lighting."),
    Category("professional_fees", "Design fees, approvals & documentation", "percent",
             {"economical": (0.03, 0.05), "standard": (0.04, 0.08), "premium": (0.06, 0.12)},
             0.85, ("Typical Indian architect/engineer fee plus municipal approval charges, "
                    "expressed as a percentage of construction cost."),
             "Municipal sanction fees vary widely by city and plot size; confirm locally."),
    Category("contingency", "Contingency & miscellaneous", "percent",
             {"economical": (0.05, 0.08), "standard": (0.06, 0.10), "premium": (0.08, 0.15)},
             0.20, ("Standard construction-practice allowance for rate movement, wastage and "
                    "unforeseen site conditions."),
             "The percentage can be overridden in the budget assumptions."),
)

CATEGORY_BY_KEY: Dict[str, Category] = {c.key: c for c in CATEGORIES}

CITY_MULTIPLIER = {"metro": 1.18, "tier1": 1.18, "tier2": 1.0, "tier3": 0.90, "rural": 0.85}

# adjustments that make a design genuinely harder to build
FLOOR_PREMIUM = {1: 1.0, 2: 1.02, 3: 1.06, 4: 1.12, 5: 1.20}


@dataclass
class EstimateContext:
    built_up_m2: float
    plot_area_m2: float
    opening_count: int
    floors: int
    finish: str
    city_tier: str
    irregular: bool
    accessibility: bool
    courtyard: bool
    currency: str = "INR"
    opening_units: float = 0.0
    adjustments: List[str] = field(default_factory=list)

    def structural_multiplier(self) -> float:
        mult = FLOOR_PREMIUM.get(self.floors, 1.20)
        if self.irregular:
            mult *= 1.05
            self.adjustments.append("Irregular plot shape: +5% on foundation and structure "
                                    "(more complex setting out and formwork).")
        if self.accessibility:
            mult *= 1.03
            self.adjustments.append("Step-free / accessible design: +3% (ramps, wider doors, "
                                    "accessible sanitaryware).")
        if self.courtyard:
            mult *= 1.02
        return round(mult, 4)


def build_context(data: ProjectData) -> EstimateContext:
    built = sum(r.area for f in data.floors for r in f.rooms if not r.is_outdoor)
    if built <= 0:
        built = data.metrics.total_built_up_m2
    openings = sum(len(f.openings) for f in data.floors)
    # windows cost roughly half of a door leaf + frame set, so weight the count
    units = 0.0
    for f in data.floors:
        for op in f.openings:
            units += {"door": 1.0, "sliding": 1.6, "pass_through": 0.4, "window": 0.55,
                      "vent": 0.15}.get(op.kind, 0.5) * max(0.5, min(1.8, op.width / 0.9))
    units = max(units, openings * 0.5)
    ctx = EstimateContext(
        built_up_m2=round(built, 2),
        plot_area_m2=round(data.plot.area, 2),
        opening_count=openings, opening_units=round(units, 2),
        floors=max(1, data.requirements.floors),
        finish=data.budget.finish_level or data.requirements.finish_level,
        city_tier=data.budget.city_tier or data.plot.city_tier,
        irregular=data.plot.shape in ("l_shape", "irregular", "custom") and bool(data.plot.boundary),
        accessibility=bool(data.requirements.accessibility),
        courtyard=any(r.type == "courtyard" for f in data.floors for r in f.rooms),
        currency=data.budget.currency or data.requirements.currency or "INR",
    )
    return ctx


def _quantity(cat: Category, ctx: EstimateContext) -> Tuple[float, str]:
    if cat.basis == "builtup":
        return ctx.built_up_m2, "m\u00b2 built-up"
    if cat.basis == "plot":
        return ctx.plot_area_m2, "m\u00b2 plot"
    if cat.basis == "count":
        return ctx.opening_units or float(ctx.opening_count), "door/window units"
    return 1.0, "% of construction cost"


def estimate_budget(data: ProjectData, ctx: Optional[EstimateContext] = None) -> BudgetEstimate:
    ctx = ctx or build_context(data)
    assumptions: BudgetAssumptions = data.budget
    city_mult = CITY_MULTIPLIER.get(ctx.city_tier, 1.0)
    struct_mult = ctx.structural_multiplier()
    finish = ctx.finish if ctx.finish in ("economical", "standard", "premium") else "standard"

    lines: List[BudgetLine] = []
    construction_low = construction_high = 0.0
    labour_total = material_total = 0.0
    warnings: List[str] = list(ctx.adjustments)

    # first pass: everything except the percentage categories
    for cat in CATEGORIES:
        if cat.basis == "percent":
            continue
        qty, unit = _quantity(cat, ctx)
        lo, hi = cat.rates.get(finish, cat.rates["standard"])
        custom = assumptions.custom_rates.get(cat.key)
        verified = False
        note = cat.note
        if custom is not None and custom > 0:
            lo = hi = float(custom)
            verified = bool(assumptions.verified)
            note = ("Rate entered by you" + (" and marked as verified." if verified else
                                             " (not independently verified).")) + \
                   ((" " + cat.note) if cat.note else "")
            if not verified:
                warnings.append(f"'{cat.label}' uses your own rate of {ctx.currency} {custom:,.0f} per "
                                f"{unit}. Mark it verified once you have a written quotation.")
        mult = city_mult * (struct_mult if cat.key in ("foundation", "structure") else 1.0)
        if cat.key == "kitchen_wardrobe" and data.requirements.kitchen_type == "platform_only":
            mult *= 0.55
            note = (note + " Reduced because a platform-only kitchen was selected.").strip()
        if cat.key == "external_dev" and not (data.materials.boundary_wall or
                                              data.materials.landscaping):
            mult *= 0.7
            note = (note + " Reduced because no boundary wall / landscaping is planned.").strip()
        lo *= mult
        hi *= mult
        lines.append(BudgetLine(
            category=cat.key, label=cat.label, quantity=round(qty, 2), unit=unit,
            rate_low=round(lo, 2), rate_high=round(hi, 2),
            amount_low=round(qty * lo, 2), amount_high=round(qty * hi, 2),
            amount_mid=round(qty * (lo + hi) / 2, 2),
            labour_share_pct=round(cat.labour_share * 100, 1),
            source=cat.source, verified=verified, note=note))
        construction_low += qty * lo
        construction_high += qty * hi

    # second pass: percentage categories
    for cat in CATEGORIES:
        if cat.basis != "percent":
            continue
        lo_pct, hi_pct = cat.rates.get(finish, cat.rates["standard"])
        custom = assumptions.custom_rates.get(cat.key)
        verified = False
        note = cat.note
        if cat.key == "contingency" and assumptions.contingency_pct:
            lo_pct = hi_pct = assumptions.contingency_pct / 100.0
            note = f"Set to your {assumptions.contingency_pct:g}% contingency."
        if custom is not None and custom > 0:
            lo_pct = hi_pct = custom / 100.0
            verified = bool(assumptions.verified)
            note = f"Percentage entered by you ({custom:g}%)."
        amount_low = construction_low * lo_pct
        amount_high = construction_high * hi_pct
        if not assumptions.include_furniture and cat.key == "contingency":
            pass
        lines.append(BudgetLine(
            category=cat.key, label=cat.label, quantity=round((lo_pct + hi_pct) / 2 * 100, 2),
            unit="% of construction cost", rate_low=round(lo_pct * 100, 2),
            rate_high=round(hi_pct * 100, 2), amount_low=round(amount_low, 2),
            amount_high=round(amount_high, 2), amount_mid=round((amount_low + amount_high) / 2, 2),
            labour_share_pct=round(cat.labour_share * 100, 1), source=cat.source,
            verified=verified, note=note))
        construction_low += amount_low
        construction_high += amount_high

    # excluded scopes the user should know about
    excluded: List[str] = []
    if not assumptions.include_furniture:
        excluded.append("Loose furniture, appliances and furnishings")
    if not assumptions.include_landscaping:
        excluded.append("Landscaping beyond basic site works")
    excluded += ["Land cost", "Stamp duty, registration and legal fees",
                 "Compound wall beyond the plotted boundary",
                 "Solar, home automation, sewage treatment plant",
                 "Basement or piling work", "Fees for a structural engineer's drawings"]

    for line in lines:
        labour_total += line.amount_mid * line.labour_share_pct / 100.0
        material_total += line.amount_mid * (1 - line.labour_share_pct / 100.0)

    gst_pct = assumptions.gst_pct or 0.0
    gst = (construction_low + construction_high) / 2 * gst_pct / 100.0
    total_low = construction_low + gst
    total_high = construction_high + gst

    if ctx.built_up_m2 <= 0:
        warnings.append("No built-up area could be measured, so the estimate is empty. Generate a "
                        "design first.")
    if not assumptions.verified and not assumptions.custom_rates:
        warnings.append("All rates are indicative bands. Enter your local contractor rates in the "
                        "budget panel to make this estimate meaningful for your city.")
    if assumptions.rate_basis:
        warnings.append(f"Rate basis you recorded: {assumptions.rate_basis}")

    est = BudgetEstimate(
        currency=ctx.currency, finish_level=finish, city_tier=ctx.city_tier,
        built_up_m2=ctx.built_up_m2, plot_area_m2=ctx.plot_area_m2,
        lines=lines,
        subtotal_low=round(construction_low - gst * 0, 2),
        subtotal_high=round(construction_high, 2),
        subtotal_mid=round((construction_low + construction_high) / 2, 2),
        contingency_low=round(next((l.amount_low for l in lines if l.category == "contingency"), 0), 2),
        contingency_high=round(next((l.amount_high for l in lines if l.category == "contingency"), 0), 2),
        gst=round(gst, 2), total_low=round(total_low, 2), total_high=round(total_high, 2),
        total_mid=round((total_low + total_high) / 2, 2),
        rate_per_sqm_low=round(total_low / ctx.built_up_m2, 2) if ctx.built_up_m2 else 0.0,
        rate_per_sqm_high=round(total_high / ctx.built_up_m2, 2) if ctx.built_up_m2 else 0.0,
        rate_per_sqft_low=round(rate_m2_to_sqft(total_low / ctx.built_up_m2), 2) if ctx.built_up_m2 else 0.0,
        rate_per_sqft_high=round(rate_m2_to_sqft(total_high / ctx.built_up_m2), 2) if ctx.built_up_m2 else 0.0,
        labour_total=round(labour_total, 2), material_total=round(material_total, 2),
        assumptions=_assumption_lines(ctx, assumptions, excluded),
        warnings=warnings,
        disclaimer=("This is a parametric planning estimate, not a contractor quotation and not a "
                    "tender. Rates are indicative bands and may be out of date for your city. "
                    "Get written quotations from local contractors and a quantity surveyor before "
                    "committing money."),
        generated_at=utcnow(),
    )
    return est


def _assumption_lines(ctx: EstimateContext, assumptions: BudgetAssumptions,
                      excluded: List[str]) -> List[str]:
    lines = [
        f"Built-up area measured from the model: {ctx.built_up_m2:,.2f} m\u00b2 "
        f"({m2_to_sqft(ctx.built_up_m2):,.0f} sq ft). Balconies, terraces and open parking are "
        f"excluded from built-up area.",
        f"Finish level '{ctx.finish}' and city tier '{ctx.city_tier}' "
        f"(location multiplier {CITY_MULTIPLIER.get(ctx.city_tier, 1.0):.2f}\u00d7).",
        f"{ctx.floors} floor(s); floor-count and shape adjustments applied to foundation and "
        f"structure only.",
        f"Labour share assumed at {assumptions.labour_share_pct:g}% overall "
        f"(per-category shares are used in the breakdown).",
        "Excluded from this estimate: " + "; ".join(excluded) + ".",
        "Costs are in " + ctx.currency + " and exclude land, finance charges and inflation between "
        "now and the construction date.",
    ]
    return lines


def compare_alternatives(data: ProjectData, variants: Dict[str, ProjectData]) -> List[BudgetComparison]:
    """Compare the current design with named alternatives on cost and space use."""
    out: List[BudgetComparison] = []
    all_variants = dict(variants)
    all_variants.setdefault(data.active_concept or "current", data)
    for key, project in all_variants.items():
        est = estimate_budget(project)
        rooms = [r for f in project.floors for r in f.rooms if not r.is_outdoor]
        usable = sum(r.area for r in rooms if r.type not in ("corridor", "lobby", "stairs"))
        beds = sum(1 for r in rooms if r.type in ("bedroom", "master_bedroom"))
        baths = sum(1 for r in rooms if r.type in ("bathroom", "toilet"))
        circ = sum(r.area for r in rooms if r.type in ("corridor", "lobby", "stairs"))
        total_area = sum(r.area for r in rooms) or 1.0
        notes: List[str] = []
        if est.built_up_m2 > 0:
            notes.append(f"{est.rate_per_sqft_low:,.0f}-{est.rate_per_sqft_high:,.0f} "
                         f"{est.currency}/sq ft")
        notes.append(f"{usable / total_area * 100:.0f}% of built-up area is usable rooms")
        if project.requirements.budget_target and est.total_mid > project.requirements.budget_target:
            notes.append("Above your stated budget target")
        out.append(BudgetComparison(
            label=key.replace("_", " ").title(), key=key, built_up_m2=est.built_up_m2,
            total_low=est.total_low, total_high=est.total_high, total_mid=est.total_mid,
            rate_per_sqft_low=est.rate_per_sqft_low, rate_per_sqft_high=est.rate_per_sqft_high,
            bedrooms=beds, bathrooms=baths, circulation_pct=round(circ / total_area * 100, 1),
            open_space_pct=project.metrics.open_space_pct, usable_area_m2=round(usable, 2),
            notes=notes))
    out.sort(key=lambda c: c.total_mid)
    return out


def rate_table(finish: str = "standard") -> List[Dict[str, object]]:
    """Expose the rate table so the UI can show sources and let users override."""
    rows: List[Dict[str, object]] = []
    for cat in CATEGORIES:
        lo, hi = cat.rates.get(finish, cat.rates["standard"])
        rows.append({
            "category": cat.key, "label": cat.label, "basis": cat.basis,
            "unit": {"builtup": "INR per m\u00b2 built-up", "plot": "INR per m\u00b2 plot",
                     "count": "INR per opening", "percent": "% of construction cost"}[cat.basis],
            "rate_low": lo, "rate_high": hi, "labour_share_pct": round(cat.labour_share * 100, 1),
            "source": cat.source, "note": cat.note,
            "bands": {lvl: list(band) for lvl, band in cat.rates.items()},
        })
    return rows
