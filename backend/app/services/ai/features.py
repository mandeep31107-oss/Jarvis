"""High level AI features used by the API.

Each function builds a compact, structured snapshot of the project and hands it to
whichever adapter is configured. The snapshot is facts only -- the model never
returns geometry, and nothing it says is applied to the plan without the user
approving a preview.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...schemas import ProjectData
from ..budget import estimate_budget
from ..designer import project_summary
from ..materials import STYLES, climate_advice, design_alternatives, style_catalogue
from ..units import m2_to_sqft
from ..validation import validation_result
from .base import DISCLAIMER
from .registry import get_image_provider, get_llm, get_vision

MAX_FACTS_ROOMS = 90


def build_facts(data: ProjectData, *, include_validation: bool = True,
                include_budget: bool = True, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    facts = project_summary(data)
    facts["rooms"] = facts.get("rooms", [])[:MAX_FACTS_ROOMS]
    facts["unit_note"] = ("All dimensions are metres, +x is East, +y is North. Room x/y is the "
                          "south-west corner.")
    facts["assumptions"] = [a.model_dump() for a in data.assumptions][:20]
    facts["history"] = data.history[-15:]
    facts["materials"] = data.materials.model_dump()
    facts["images"] = [{"id": i.id, "filename": i.filename, "room_type": i.room_type,
                        "goals": i.goals, "status": i.status} for i in data.images]
    facts["climate_advice"] = climate_advice(data)
    facts["concept_trade_offs"] = {c.key: list(c.trade_offs) for c in data.concepts}
    if include_validation:
        result = validation_result(data)
        facts["validation"] = {"ok": result.ok, "counts": result.counts,
                               "issues": [{"severity": i.severity, "code": i.code,
                                           "message": i.message, "fix": i.suggested_fix,
                                           "source": i.source} for i in result.issues[:40]]}
    if include_budget:
        try:
            est = estimate_budget(data)
            facts["budget_detail"] = {
                "lines": [{"category": l.category, "label": l.label, "quantity": l.quantity,
                           "unit": l.unit, "rate_low": l.rate_low, "rate_high": l.rate_high,
                           "amount_low": l.amount_low, "amount_high": l.amount_high,
                           "verified": l.verified} for l in est.lines],
                "assumptions": est.assumptions, "warnings": est.warnings,
                "disclaimer": est.disclaimer,
            }
        except Exception as exc:  # keep the assistant usable if budgeting fails
            facts["budget_detail"] = {"error": str(exc)}
    if extra:
        facts.update(extra)
    return facts


async def explain_design(data: ProjectData) -> Dict[str, Any]:
    llm, note = get_llm()
    text = await llm.complete(task="explain_design", facts=build_facts(data))
    return {"text": text, "provider": llm.name, "note": note, "disclaimer": DISCLAIMER}


async def explain_concepts(data: ProjectData) -> Dict[str, Any]:
    llm, note = get_llm()
    text = await llm.complete(task="explain_concept", facts=build_facts(data, include_budget=False))
    return {"text": text, "provider": llm.name, "note": note}


async def answer_question(data: ProjectData, question: str,
                          history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    llm, note = get_llm()
    facts = build_facts(data)
    if history:
        facts["recent_conversation"] = history[-6:]
    text = await llm.complete(task="answer", facts=facts, question=question)
    return {"text": text, "provider": llm.name, "note": note, "question": question,
            "sources": ["project snapshot", "validation results", "budget estimate"]}


async def style_advice(data: ProjectData) -> Dict[str, Any]:
    llm, note = get_llm()
    text = await llm.complete(task="style_advice",
                              facts=build_facts(data, include_validation=False, include_budget=False))
    return {"text": text, "provider": llm.name, "note": note,
            "palettes": style_catalogue(), "alternatives": design_alternatives(data)}


async def budget_commentary(data: ProjectData) -> Dict[str, Any]:
    llm, note = get_llm()
    facts = build_facts(data, include_validation=False)
    text = await llm.complete(task="budget_commentary", facts=facts)
    return {"text": text, "provider": llm.name, "note": note,
            "estimate": facts.get("budget_detail", {})}


async def compare_alternatives_text(data: ProjectData,
                                    alternatives: List[Dict[str, Any]]) -> Dict[str, Any]:
    llm, note = get_llm()
    facts = build_facts(data, include_validation=False,
                        extra={"alternatives": [a if isinstance(a, dict) else a.model_dump()
                                                for a in alternatives]})
    text = await llm.complete(task="compare", facts=facts)
    return {"text": text, "provider": llm.name, "note": note}


async def analyse_image(path: str, *, room_type: str = "other", goals: Optional[List[str]] = None,
                        note: str = "", mime_type: str = "image/jpeg") -> Dict[str, Any]:
    vision, fallback_note = get_vision()
    analysis = await vision.analyse(path=path, room_type=room_type, goals=goals or [], note=note,
                                    mime_type=mime_type)
    analysis["fallback_note"] = fallback_note
    return analysis


async def generate_image(prompt: str, style: str,
                         reference_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    provider, note = get_image_provider()
    result = await provider.generate(prompt=prompt, style=style, reference_paths=reference_paths)
    result["provider_note"] = note
    return result


# ---------------------------------------------------------------------------
# structured suggestions (computed locally, never invented by a model)
# ---------------------------------------------------------------------------

def design_suggestions(data: ProjectData) -> List[Dict[str, Any]]:
    """Concrete, actionable improvements derived from the project's own numbers."""
    out: List[Dict[str, Any]] = []
    result = validation_result(data)
    for issue in result.issues:
        if issue.severity == "info":
            continue
        out.append({
            "id": f"{issue.code}:{issue.room_id or ''}",
            "severity": issue.severity, "code": issue.code, "title": issue.code.replace("_", " ").title(),
            "message": issue.message, "fix": issue.suggested_fix, "source": issue.source,
            "room_ids": issue.room_ids, "floor_id": issue.floor_id,
            "auto_fixable": issue.code in ("opening_collision", "furniture_outside",
                                           "opening_outside_wall"),
        })
    metrics = data.metrics
    if metrics.open_space_pct < 20:
        out.append({"id": "open_space", "severity": "warning", "code": "open_space_low",
                    "title": "Very little open space",
                    "message": f"Only {metrics.open_space_pct:.0f}% of the plot is left open. Most "
                               f"Indian byelaws expect a meaningful open area for drainage, light "
                               f"and access.",
                    "fix": "Reduce the ground coverage or move a room upstairs.",
                    "source": "regulation", "room_ids": [], "floor_id": None,
                    "auto_fixable": False})
    if metrics.circulation_pct > 28:
        out.append({"id": "circulation", "severity": "info", "code": "circulation_high",
                    "title": "High circulation share",
                    "message": f"{metrics.circulation_pct:.0f}% of the built-up area is corridors, "
                               f"lobby and stairs.",
                    "fix": "Group rooms around a central lobby instead of a long corridor.",
                    "source": "comfort", "room_ids": [], "floor_id": None,
                    "auto_fixable": False})
    if data.requirements.budget_target:
        try:
            est = estimate_budget(data)
            if est.total_mid > data.requirements.budget_target:
                overshoot = est.total_mid / data.requirements.budget_target - 1
                out.append({
                    "id": "budget", "severity": "warning", "code": "over_budget",
                    "title": "Above your budget target",
                    "message": f"The estimate is about {overshoot * 100:.0f}% above your target of "
                               f"{data.requirements.currency} {data.requirements.budget_target:,.0f}.",
                    "fix": "Reduce built-up area, drop to an economical finish, or phase the build "
                           "(shell first, finishes later).",
                    "source": "budget", "room_ids": [], "floor_id": None, "auto_fixable": False})
        except Exception:
            pass
    for tip in climate_advice(data)[:2]:
        out.append({"id": f"climate:{tip[:24]}", "severity": "info", "code": "climate_tip",
                    "title": f"{data.plot.climate.replace('_', ' ').title()} climate tip",
                    "message": tip, "fix": None, "source": "comfort", "room_ids": [],
                    "floor_id": None, "auto_fixable": False})
    if not out:
        out.append({"id": "clean", "severity": "info", "code": "no_issues",
                    "title": "No blocking issues found",
                    "message": "The automated checks found nothing that needs immediate attention. "
                               "That is not a building approval.",
                    "fix": None, "source": "geometry", "room_ids": [], "floor_id": None,
                    "auto_fixable": False})
    return out


def before_after(data: ProjectData, analysis: Dict[str, Any],
                 proposed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Structured before/after comparison for a photo-based redesign request."""
    before = {
        "source": "uploaded image",
        "observations": [f.get("value", "") for f in analysis.get("findings", [])][:8],
        "palette": analysis.get("palette", []),
        "brightness": analysis.get("brightness"),
        "structural_elements": analysis.get("structural_elements", []),
        "unknowns": [analysis.get("dimension_warning") or
                     "Exact dimensions cannot be recovered from a photograph."],
    }
    after = {
        "source": "your model" if data.floors else "proposal",
        "style": data.requirements.style,
        "materials": data.materials.model_dump(),
        "rooms": [{"name": r.name, "area_m2": round(r.area, 2),
                   "area_sqft": round(m2_to_sqft(r.area), 1)}
                  for r in data.all_rooms()[:20]],
        "changes": (proposed or {}).get("changes", []),
    }
    alternatives = []
    for alt in design_alternatives(data, count=3):
        alternatives.append({"key": alt["key"], "label": alt["label"],
                             "description": alt["description"], "cost_impact": alt["cost_impact"],
                             "materials": alt["preview"]})
    return {"before": before, "after": after, "alternatives": alternatives,
            "notice": ("The 'after' column describes your live model and material choices -- it is "
                       "not a rendering of the uploaded photo. Generated illustrations, where the "
                       "provider supports them, are marked clearly and are never a substitute for "
                       "the 3D model.")}
