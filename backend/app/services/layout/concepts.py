"""Design concepts: each concept is a different set of planning priorities."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    blurb: str
    coverage: float              # fraction of the buildable envelope used as footprint
    generosity: float            # multiplier applied to comfortable room areas
    open_plan: bool = False      # merge living + dining
    courtyard: bool = False      # reserve an open-to-sky courtyard
    verandah: bool = False       # add a front porch/verandah
    corridor: bool = True        # explicit circulation strip
    ground_bedroom_bias: float = 0.4
    glazing: str = "standard"    # standard | large | minimal
    trade_offs: List[str] = field(default_factory=list)


CONCEPTS: Dict[str, Concept] = {
    "space_efficient": Concept(
        key="space_efficient",
        label="Space Efficient",
        blurb="Squeezes the most usable rooms out of the plot with compact circulation "
              "and shared walls. Best for smaller plots where every square foot matters.",
        coverage=0.88,
        generosity=0.92,
        open_plan=True,
        corridor=False,
        glazing="standard",
        trade_offs=[
            "Rooms are at the smaller end of comfortable, so bulky furniture may not fit.",
            "Fewer separation walls means less acoustic privacy between spaces.",
        ],
    ),
    "budget_conscious": Concept(
        key="budget_conscious",
        label="Budget Conscious",
        blurb="Keeps the built-up area (and therefore the construction cost) low by building "
              "a compact footprint, a simple roof line and fewer wet areas.",
        coverage=0.70,
        generosity=0.85,
        open_plan=True,
        corridor=False,
        glazing="minimal",
        trade_offs=[
            "Leaves more open ground, which is great for heat but reduces indoor area.",
            "Bathrooms are grouped to shorten plumbing runs; expect fewer fixtures.",
            "Cheaper to build now, but adding rooms later costs more per square foot.",
        ],
    ),
    "family_comfort": Concept(
        key="family_comfort",
        label="Family Comfort",
        blurb="Generous living and dining areas, a bedroom on the ground floor for elders, "
              "and clear separation between private and guest spaces.",
        coverage=0.80,
        generosity=1.15,
        ground_bedroom_bias=0.9,
        trade_offs=[
            "Larger built-up area, so construction cost is higher.",
            "Needs a reasonably large plot to avoid cramped leftovers.",
        ],
    ),
    "modern_contemporary": Concept(
        key="modern_contemporary",
        label="Modern Contemporary",
        blurb="Open-plan living and dining, large glazed openings, flat roof lines and a "
              "clean rectangular massing with cantilevered balconies.",
        coverage=0.86,
        generosity=1.08,
        open_plan=True,
        glazing="large",
        trade_offs=[
            "Large glazing needs shading in hot climates or the house will overheat.",
            "Open plan gives less acoustic separation for guests and family.",
        ],
    ),
    "traditional_indian": Concept(
        key="traditional_indian",
        label="Traditional Indian",
        blurb="Front verandah, a central courtyard for light and cross ventilation, a "
              "separate kitchen and a prayer space -- the classic Indian family home.",
        coverage=0.76,
        generosity=1.02,
        courtyard=True,
        verandah=True,
        glazing="standard",
        trade_offs=[
            "The courtyard consumes built-up area that could otherwise be a room.",
            "More wall length means slightly higher masonry cost.",
        ],
    ),
}


def get_concept(key: str | None) -> Concept:
    return CONCEPTS.get(key or "family_comfort", CONCEPTS["family_comfort"])


def concept_summaries() -> List[dict]:
    return [
        {
            "key": c.key,
            "label": c.label,
            "blurb": c.blurb,
            "trade_offs": list(c.trade_offs),
        }
        for c in CONCEPTS.values()
    ]
