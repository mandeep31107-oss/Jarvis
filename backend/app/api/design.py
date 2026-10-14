"""Design engine endpoints: generation, concepts, checks, budget, materials and
the natural-language command pipeline (interpret -> preview -> commit)."""

from __future__ import annotations

import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import current_owner
from ..schemas import ProjectData
from ..services import budget as budget_service
from ..services import materials as materials_service
from ..services import storage
from ..services.designer import (generate_design, missing_information, project_summary,
                                 switch_concept)
from ..services.events import done, failed, progress, project_changed
from ..services.nl import commands as nl_commands
from ..services.nl.parser import parse_command
from ..services.validation import validation_result

router = APIRouter(prefix="/api/projects/{project_id}", tags=["design"])

# server-side preview cache: id -> (project_id, candidate payload, created)
_PREVIEWS: Dict[str, Dict[str, Any]] = {}
_PREVIEW_TTL = 15 * 60


def _load(project_id: str, owner: str) -> ProjectData:
    record = storage.get_project(project_id, owner_id=owner)
    if record is None:
        raise HTTPException(404, "That project does not exist (or belongs to another account).")
    return record.data


def _save(project_id: str, owner: str, data: ProjectData, label: str, source: str,
          note: str = "") -> None:
    storage.save_project(project_id, data, owner_id=owner, version_label=label,
                         version_source=source, note=note)
    project_changed(project_id, source)


class GenerateBody(BaseModel):
    concepts: List[str] = Field(default_factory=list)
    active: Optional[str] = None


class ConceptBody(BaseModel):
    concept: str


class MaterialBody(BaseModel):
    field: str
    value: Any


class StyleBody(BaseModel):
    style: str


class RoomFinishBody(BaseModel):
    wall_color: Optional[str] = None
    floor_material: Optional[str] = None
    floor_color: Optional[str] = None


class CommandBody(BaseModel):
    text: str = Field(max_length=600)


class CommitBody(BaseModel):
    preview_id: str


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------

@router.post("/generate")
def regenerate(project_id: str, body: GenerateBody,
               owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    progress(project_id, "generate", 10, "Rebuilding the room programme")
    try:
        from ..services.designer import DEFAULT_CONCEPTS
        generate_design(data, concepts=body.concepts or list(DEFAULT_CONCEPTS),
                        active=body.active)
    except Exception as exc:
        failed(project_id, "generate", str(exc))
        raise HTTPException(500, f"The layout engine refused this brief: {exc}")
    _save(project_id, owner, data, "regenerate", "generate", "Concepts regenerated")
    done(project_id, "generate", "Concepts ready")
    return _design_payload(data)


@router.post("/concept")
def activate_concept(project_id: str, body: ConceptBody,
                     owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    try:
        data, notes = switch_concept(data, body.concept)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    _save(project_id, owner, data, f"concept:{body.concept}", "concept",
          f"Activated concept {body.concept}")
    payload = _design_payload(data)
    payload["notes"] = notes
    return payload


@router.get("/summary")
def summary(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    return project_summary(data)


@router.get("/missing-information")
def missing(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    return {"questions": missing_information(data.plot, data.requirements)}


@router.get("/validation")
def validate(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    result = validation_result(data)
    data.last_validated = result
    storage.autosave(project_id, data, owner_id=owner)
    return {"counts": result.counts, "issues": [i.model_dump() for i in result.issues]}


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------

@router.get("/budget")
def budget(project_id: str, finish: Optional[str] = None,
           owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    if finish:
        data.requirements.finish_level = finish  # type: ignore[assignment]
    estimate = budget_service.estimate_budget(data)
    return estimate.model_dump(mode="json")


@router.get("/budget/rates")
def rates(project_id: str, finish: str = "standard",
          owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    return {"rates": budget_service.rate_table(finish),
            "note": "Indicative parametric rates. They are not current local prices: confirm "
                    "with vendors or enter your own rates below."}


@router.post("/budget/rates")
def set_rates(project_id: str, body: Dict[str, Any],
              owner: str = Depends(current_owner)) -> Dict[str, Any]:
    """Accept manual rate overrides: {category: {low, high}} stored on the project."""
    data = _load(project_id, owner)
    overrides = body.get("overrides") or {}
    cleaned = {}
    for key, value in overrides.items():
        try:
            cleaned[key] = float(value)
        except (TypeError, ValueError):
            continue
    data.budget.custom_rates = cleaned
    data.budget.verified = bool(cleaned) or bool(body.get("verified"))
    data.budget.rate_basis = str(body.get("basis") or "user-entered local rates")
    _save(project_id, owner, data, "manual rates", "budget", "Manual rate overrides saved")
    estimate = budget_service.estimate_budget(data)
    return {"saved": True, "estimate": estimate.model_dump(mode="json")}


@router.get("/budget/compare")
def compare(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    variants: Dict[str, ProjectData] = {}
    for key in [c.key for c in data.concepts]:
        candidate = data.model_copy(deep=True)
        try:
            switch_concept(candidate, key)
            variants[key] = candidate
        except Exception:
            continue
    rows = budget_service.compare_alternatives(data, variants)
    return {"comparison": [r.model_dump(mode="json") for r in rows],
            "note": "Costs are parametric estimates for comparing options, not quotations. "
                    "Space utilisation uses the engine's own room areas."}


# ---------------------------------------------------------------------------
# materials and interiors
# ---------------------------------------------------------------------------

@router.get("/materials/catalogue")
def catalogue(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    return {"styles": materials_service.style_catalogue(),
            "options": materials_service.option_catalogue(),
            "furniture_styles": materials_service.FURNITURE_STYLES}


@router.post("/materials/style")
def apply_style(project_id: str, body: StyleBody,
                owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    material_set, notes = materials_service.apply_style(data, body.style)
    data.materials = material_set
    _save(project_id, owner, data, f"style:{body.style}", "materials", notes[0] if notes else "")
    return {"materials": material_set.model_dump(mode="json"), "notes": notes,
            "data": data.model_dump(mode="json")}


@router.post("/materials")
def apply_material(project_id: str, body: MaterialBody,
                   owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    try:
        material_set, notes = materials_service.apply_material_change(data, body.field, body.value)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    data.materials = material_set
    _save(project_id, owner, data, f"material:{body.field}", "materials",
          f"{body.field} -> {body.value}")
    return {"materials": material_set.model_dump(mode="json"), "notes": notes}


@router.post("/rooms/{room_id}/finish")
def room_finish(project_id: str, room_id: str, body: RoomFinishBody,
                owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    _floor, room = data.find_room(room_id)
    if room is None:
        raise HTTPException(404, "Room not found.")
    materials_service.apply_room_finish(data, room, wall_color=body.wall_color,
                                        floor_material=body.floor_material,
                                        floor_color=body.floor_color)
    _save(project_id, owner, data, f"finish:{room.name}", "materials",
          f"Room finish updated for {room.name}")
    return {"room": room.model_dump(mode="json")}


@router.post("/materials/furniture-style")
def furniture_style(project_id: str, body: StyleBody,
                    owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    data, notes = materials_service.apply_furniture_style(data, body.style)
    _save(project_id, owner, data, f"furniture-style:{body.style}", "materials",
          notes[0] if notes else "")
    return {"notes": notes, "data": data.model_dump(mode="json")}


@router.get("/alternatives")
def alternatives(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    return {"alternatives": materials_service.design_alternatives(data)}


@router.get("/climate-advice")
def climate(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    return {"advice": materials_service.climate_advice(data)}


# ---------------------------------------------------------------------------
# natural language commands
# ---------------------------------------------------------------------------

@router.post("/command")
async def command(project_id: str, body: CommandBody,
                  owner: str = Depends(current_owner)) -> Dict[str, Any]:
    data = _load(project_id, owner)
    progress(project_id, "command", 20, f"Reading: {body.text[:60]}")
    parsed = await nl_commands.interpret(data, body.text)
    if parsed.clarification and not parsed.operations:
        return {"status": "clarification", "clarification": parsed.clarification,
                "parsed": parsed.as_dict(), "help": nl_commands.help_text()}
    preview_payload = nl_commands.preview(data, parsed)
    candidate: ProjectData = preview_payload.pop("preview")
    if not preview_payload.get("applied"):
        return {"status": "rejected", "errors": preview_payload.get("rejected", []),
                "parsed": parsed.as_dict(), "notes": preview_payload.get("notes", [])}
    preview_id = secrets.token_hex(8)
    _PREVIEWS[preview_id] = {"project_id": project_id, "owner": owner,
                             "candidate": candidate.model_dump(mode="json"),
                             "created": time.time(), "text": body.text}
    _prune_previews()
    done(project_id, "command", "Preview ready")
    payload = {k: v for k, v in preview_payload.items() if k != "preview"}
    payload["candidate"] = candidate.model_dump(mode="json")
    return {"status": "preview", "preview_id": preview_id, "parsed": parsed.as_dict(),
            **payload}


@router.post("/command/commit")
def commit(project_id: str, body: CommitBody,
           owner: str = Depends(current_owner)) -> Dict[str, Any]:
    entry = _PREVIEWS.get(body.preview_id)
    if not entry or entry["project_id"] != project_id or entry["owner"] != owner:
        raise HTTPException(404, "That preview expired. Run the command again.")
    if time.time() - entry["created"] > _PREVIEW_TTL:
        _PREVIEWS.pop(body.preview_id, None)
        raise HTTPException(410, "That preview expired. Run the command again.")
    current = _load(project_id, owner)
    candidate = ProjectData.model_validate(entry["candidate"])
    merged = nl_commands.apply_preview(candidate, current, entry["text"])
    _save(project_id, owner, merged, f"command: {entry['text'][:60]}", "command",
          entry["text"])
    _PREVIEWS.pop(body.preview_id, None)
    project_changed(project_id, "command")
    return {"applied": True, "data": merged.model_dump(mode="json"),
            "validation": validation_result(merged).model_dump(mode="json")}


@router.post("/command/reject")
def reject(project_id: str, body: CommitBody,
           owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _PREVIEWS.pop(body.preview_id, None)
    return {"rejected": True}


@router.get("/command/help")
def help_ops(project_id: str, owner: str = Depends(current_owner)) -> Dict[str, Any]:
    _load(project_id, owner)
    return {"help": nl_commands.help_text(), "operations": nl_commands.operation_catalogue()}


def _prune_previews() -> None:
    now = time.time()
    for key in [k for k, v in _PREVIEWS.items() if now - v["created"] > _PREVIEW_TTL]:
        _PREVIEWS.pop(key, None)


def _design_payload(data: ProjectData) -> Dict[str, Any]:
    result = validation_result(data)
    data.last_validated = result
    return {
        "data": data.model_dump(mode="json"),
        "summary": project_summary(data),
        "validation": {"counts": result.counts,
                       "issues": [i.model_dump() for i in result.issues]},
        "concepts": data.concepts,
        "active_concept": data.active_concept,
    }
