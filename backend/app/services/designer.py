"""Design orchestration.

Turns the setup wizard's answers into a complete :class:`ProjectData`: plot,
requirements, one or more generated concepts, materials, metrics, assumptions and
an honest list of what is still unknown. The layout engine does the geometry; this
module owns the project-level decisions and the paperwork around them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import (Assumption, ConceptKey, ConceptSummary, DesignMetrics, Floor, Plot,
                       ProjectData, Requirements, Room, utcnow)
from .budget import estimate_budget
from .layout.concepts import CONCEPTS, concept_summaries, get_concept
from .layout.engine import GenerationResult, generate_house
from .layout.site import boundary_points_from_plot
from .materials import STYLES, apply_style
from .units import m2_to_sqft, to_metres
from .validation import validation_result

DEFAULT_CONCEPTS: Tuple[ConceptKey, ...] = ("space_efficient", "budget_conscious",
                                            "family_comfort", "modern_contemporary",
                                            "traditional_indian")

MISSING_INFO_QUESTIONS: Tuple[Dict[str, str], ...] = (
    {"field": "road_side", "question": "Which side does the road face?",
     "why": "The road decides where the entrance, parking and front setback go, and it drives the "
            "orientation advice (afternoon sun on a west-facing plot is a real comfort problem)."},
    {"field": "orientation", "question": "Is the plot's long axis roughly north-south or east-west?",
     "why": "It changes which rooms get morning light and which walls take the evening heat."},
    {"field": "setbacks", "question": "Do you know your required setbacks (front / rear / sides)?",
     "why": "Setbacks decide how much of the plot is actually buildable. Without them the tool has "
            "to assume values, and those assumptions may not match your municipality."},
    {"field": "floors", "question": "How many floors do you want to build (including any future "
                                 "floor)?",
     "why": "Floor count changes the stair position, the structural system and the budget "
            "significantly."},
    {"field": "road_width", "question": "How wide is the road in front of the plot?",
     "why": "Road width usually caps the permissible FAR and the number of floors in Indian "
            "byelaws -- but confirm with your local authority."},
    {"field": "soil", "question": "Do you know the soil type or water table on the site?",
     "why": "Black cotton soil, filled ground or a high water table all change the foundation "
            "cost. This tool does not design foundations."},
)


def make_plot(width: float, depth: float, unit: str = "ft", shape: str = "rectangular",
              road_side: str = "S", road_width_m: Optional[float] = None,
              setbacks: Optional[Dict[str, float]] = None, boundary: Optional[List[Dict[str, float]]] = None,
              country: str = "India", state: str = "", city: str = "", city_tier: str = "tier2",
              climate: str = "composite") -> Plot:
    w = to_metres(width, unit)
    d = to_metres(depth, unit)
    pts = None
    if boundary:
        from ..schemas import Point
        pts = [Point(x=float(p["x"]), y=float(p["y"])) for p in boundary]
    elif shape in ("l_shape", "irregular", "custom"):
        from ..schemas import Point
        pts = [Point(x=x, y=y) for x, y in boundary_points_from_plot(w, d, shape)]
    plot = Plot(width=round(w, 4), depth=round(d, 4), input_width=width, input_depth=depth,
                input_unit=unit, shape=shape, road_side=road_side, road_width_m=road_width_m,
                country=country, state=state, city=city, city_tier=city_tier, climate=climate,
                **({"boundary": pts} if pts else {}))
    if setbacks:
        for key, value in setbacks.items():
            if hasattr(plot.setbacks, key):
                setattr(plot.setbacks, key, max(0.0, float(value)))
    else:
        _default_setbacks(plot)
    return plot


def _default_setbacks(plot: Plot) -> None:
    """Reasonable starting setbacks, always reported to the user as assumptions."""
    area = plot.area
    front = 2.5 if area < 150 else (3.0 if area < 400 else 4.0)
    rear = 1.5 if area < 150 else 2.0
    side = 0.0 if area < 50 else (1.0 if area < 250 else 1.5)
    plot.apply_setbacks(front, rear, side)


def default_setback_note(plot: Plot) -> str:
    sb = plot.setbacks
    return (f"Assumed setbacks of {sb.south:.2f} m south, {sb.north:.2f} m north, "
            f"{sb.east:.2f} m east and {sb.west:.2f} m west, based on a {plot.area:,.0f} m\u00b2 plot. "
            f"Your municipal byelaws may require different values -- enter them in the setup "
            f"wizard if you know them.")


def missing_information(plot: Plot, req: Requirements) -> List[Dict[str, str]]:
    """Materially missing inputs the AI should ask about, with the reason why."""
    out: List[Dict[str, str]] = []
    known = {
        "road_side": bool(plot.road_side),
        "orientation": plot.width > 0 and plot.depth > 0,
        "setbacks": any(getattr(plot.setbacks, s) > 0 for s in ("north", "south", "east", "west")),
        "floors": req.floors >= 1,
        "road_width": plot.road_width_m is not None,
        "soil": False,
    }
    for item in MISSING_INFO_QUESTIONS:
        if not known.get(item["field"], True):
            out.append(dict(item))
    return out


def generate_design(data: ProjectData, concepts: Sequence[str] = DEFAULT_CONCEPTS,
                    active: Optional[str] = None, apply_materials: bool = True) -> ProjectData:
    """Generate every requested concept into the project and activate one."""
    req = data.requirements
    style_key = req.style if req.style in STYLES else "modern"
    summaries: List[ConceptSummary] = []
    results: Dict[str, GenerationResult] = {}

    for key in concepts:
        if key not in CONCEPTS:
            continue
        try:
            res = generate_house(data.plot, req, key)
        except Exception as exc:  # pragma: no cover - engine is matrix tested
            summaries.append(ConceptSummary(key=key, label=CONCEPTS[key].label,
                                            blurb=f"This concept could not be generated: {exc}",
                                            trade_offs=["Generation failed"]))
            continue
        results[key] = res
        summaries.append(ConceptSummary(key=key, label=CONCEPTS[key].label,
                                        blurb=CONCEPTS[key].blurb,
                                        trade_offs=list(CONCEPTS[key].trade_offs),
                                        metrics=res.metrics))

    active_key = active or req.concept
    if active_key not in results:
        active_key = next((k for k in ("family_comfort", "budget_conscious", "space_efficient",
                                       "modern_contemporary", "traditional_indian")
                           if k in results), None)
    data.concepts = summaries
    if active_key is None or active_key not in results:
        data.history.append({"at": utcnow().isoformat(), "kind": "generate",
                             "message": "No concept could be generated for this plot and programme."})
        return data

    res = results[active_key]
    data.active_concept = active_key
    data.floors = res.floors
    data.metrics = res.metrics
    data.assumptions = list(res.assumptions)
    data.assumptions.append(Assumption(topic="setbacks", statement=default_setback_note(data.plot),
                                       severity="warning"))
    for note in res.notes:
        data.history.append({"at": utcnow().isoformat(), "kind": "note", "message": note})
    for warn in res.warnings:
        data.history.append({"at": utcnow().isoformat(), "kind": "warning", "message": warn})
        data.assumptions.append(Assumption(topic="planning", statement=warn, severity="warning"))

    if apply_materials:
        materials, notes = apply_style(data, style_key)
        data.materials = materials
        data.requirements.style = style_key
        for note in notes:
            data.history.append({"at": utcnow().isoformat(), "kind": "note", "message": note})

    data.history.append({
        "at": utcnow().isoformat(), "kind": "generate",
        "message": f"Generated {len(results)} concept(s) and activated '{active_key}'.",
        "concept": active_key, "metrics": res.metrics.model_dump(),
    })
    data.last_validated = None
    return data


def switch_concept(data: ProjectData, concept_key: str) -> Tuple[ProjectData, List[str]]:
    """Regenerate the same brief with a different concept (full geometry rebuild)."""
    if concept_key not in CONCEPTS:
        return data, [f"Unknown concept '{concept_key}'."]
    previous = data.active_concept
    data = generate_design(data, concepts=[concept_key], active=concept_key)
    notes = [f"Switched to the '{CONCEPTS[concept_key].label}' concept"
             + (f" (was '{CONCEPTS.get(previous).label if previous in CONCEPTS else previous}')."
                if previous else ".")]
    notes += list(CONCEPTS[concept_key].trade_offs)
    return data, notes


def project_summary(data: ProjectData) -> Dict[str, Any]:
    """Compact facts for the dashboard, assistant context and reports."""
    rooms = [r for f in data.floors for r in f.rooms]
    indoor = [r for r in rooms if not r.is_outdoor]
    est = None
    try:
        est = estimate_budget(data)
    except Exception:
        est = None
    return {
        "name": data.name,
        "plot": {
            "width_m": data.plot.width, "depth_m": data.plot.depth,
            "area_m2": round(data.plot.area, 2), "area_sqft": round(m2_to_sqft(data.plot.area), 1),
            "shape": data.plot.shape, "road_side": data.plot.road_side,
            "city": data.plot.city, "state": data.plot.state, "climate": data.plot.climate,
            "setbacks": data.plot.setbacks.model_dump(),
        },
        "requirements": data.requirements.model_dump(),
        "floors": [{"id": f.id, "name": f.name, "index": f.index, "level": f.level,
                    "height": f.height, "is_terrace": f.is_terrace,
                    "built_up_m2": round(f.built_up_area, 2), "rooms": len(f.rooms)}
                   for f in data.floors],
        "metrics": data.metrics.model_dump(),
        "rooms": [{"id": r.id, "name": r.name, "type": r.type, "floor": r.floor_id,
                   "area_m2": round(r.area, 2), "w": r.w, "d": r.d, "outdoor": r.is_outdoor}
                  for r in indoor],
        "room_count": len(rooms),
        "indoor_area_m2": round(sum(r.area for r in indoor), 2),
        "style": data.requirements.style,
        "active_concept": data.active_concept,
        "concepts": [{"key": c.key, "label": c.label,
                      "built_up_m2": c.metrics.total_built_up_m2,
                      "coverage_pct": c.metrics.ground_coverage_pct,
                      "open_space_pct": c.metrics.open_space_pct} for c in data.concepts],
        "budget": ({"total_low": est.total_low, "total_high": est.total_high,
                    "total_mid": est.total_mid, "currency": est.currency,
                    "per_sqft_low": est.rate_per_sqft_low,
                    "per_sqft_high": est.rate_per_sqft_high} if est else None),
        "images": len(data.images),
        "assumption_count": len(data.assumptions),
    }


def new_project(name: str, plot: Plot, requirements: Requirements) -> ProjectData:
    return ProjectData(name=name or "Untitled House Project", plot=plot, requirements=requirements,
                       length_unit=plot.input_unit or "ft",
                       area_unit="sqft" if (plot.input_unit or "ft") != "m" else "sqm")


def quick_project(width: float = 30.0, depth: float = 50.0, unit: str = "ft",
                  floors: int = 2, bedrooms: int = 3, name: str = "Untitled House Project",
                  **kwargs: Any) -> ProjectData:
    """Convenience entry point used by the API and the sample project."""
    plot = make_plot(width, depth, unit, road_side=kwargs.pop("road_side", "S"),
                     shape=kwargs.pop("shape", "rectangular"),
                     city=kwargs.pop("city", ""), state=kwargs.pop("state", ""),
                     country=kwargs.pop("country", "India"),
                     city_tier=kwargs.pop("city_tier", "tier2"),
                     climate=kwargs.pop("climate", "composite"),
                     setbacks=kwargs.pop("setbacks", None))
    req = Requirements(floors=floors, bedrooms=bedrooms, **kwargs)
    data = new_project(name, plot, req)
    return generate_design(data)
