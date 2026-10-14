"""Command facade: parse -> preview -> apply.

The three steps are deliberately separate so the UI can always show what is about
to happen and let the user reject it. ``preview`` never mutates the saved project;
``commit`` mutates a working copy the caller then persists.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ...schemas import ProjectData
from ..ai.base import AIError, ParsedCommand
from ..ai.registry import get_llm
from .ops import OPERATIONS, apply_operations, preview_operations
from .parser import candidate_operations, parse_command

LLM_COMMAND_PROMPT = """You convert one user instruction about a house design into structured edit
operations. Reply with JSON only: {"operations":[{"op":NAME,"params":{...}}],"explanation":"...",
"clarification":"..."}.

Allowed operations and their params:
- rename_room: room (name), name
- resize_room: room, and one of scale | area | delta_width | delta_depth | width | depth, optional edge (N/S/E/W)
- move_room: room, direction (N/S/E/W) and distance (metres), or x and y
- delete_room: room, optional give_space_to (another room name)
- add_room: type (one of the room types listed below), optional near (room name), area (m2), width, depth, floor
- swap_rooms: room, other_room
- split_room: room, optional new_type, fraction (0.15-0.6)
- change_material: field, value
- change_style: style
- room_finish: room, wall_color | floor_material | floor_color
- add_opening: room, kind (door|window|sliding|vent), edge, width
- move_opening: opening_id, offset or delta
- delete_opening: opening_id
- add_furniture: room, kind, x, y
- move_furniture: item_id, x, y
- delete_furniture: item_id
- change_requirements: patch (object of requirement fields)
- adjust_setback: side (north|south|east|west|front|rear), value (metres)
- regenerate: optional concept

Rules: use only rooms and ids present in the snapshot; dimensions are metres; never invent rooms or
ids; if the instruction is ambiguous or destructive, set "clarification" and return no operations;
if it would change walls, add an operation list but keep the explanation short and factual."""


async def interpret(data: ProjectData, text: str, *, use_llm: bool = True,
                    confidence_threshold: float = 0.45) -> ParsedCommand:
    """Rule-based parse first; ask a configured model only when the rules are unsure."""
    parsed = parse_command(data, text)
    if parsed.confidence >= confidence_threshold or not use_llm:
        return parsed
    llm, note = get_llm()
    if llm.name == "local":
        parsed.preview_notes.append("The built-in assistant could not parse this instruction.")
        return parsed
    facts = _command_facts(data, text)
    try:
        raw = await llm.complete(task="command", facts=facts, question=text,
                                 system=LLM_COMMAND_PROMPT, json_mode=True, max_tokens=600)
    except AIError as exc:
        parsed.preview_notes.append(f"AI assist failed: {exc}")
        return parsed
    suggested = _validate_suggestion(data, raw)
    if suggested is None:
        parsed.preview_notes.append("AI assist returned nothing usable; the instruction was not "
                                    "changed.")
        return parsed
    suggested.preview_notes.append("Interpreted by the configured AI provider. Check the preview "
                                   "carefully before approving.")
    if note:
        suggested.preview_notes.append(note)
    if parsed.clarification:
        suggested.clarification = suggested.clarification or parsed.clarification
    return suggested


def _command_facts(data: ProjectData, text: str) -> Dict[str, Any]:
    return {
        "instruction": text,
        "rooms": [{"id": r.id, "name": r.name, "type": r.type, "floor": r.floor_id,
                   "x": r.x, "y": r.y, "w": r.w, "d": r.d, "area_m2": round(r.area, 2),
                   "outdoor": r.is_outdoor} for f in data.floors for r in f.rooms],
        "floors": [{"id": f.id, "name": f.name, "index": f.index} for f in data.floors],
        "openings": [{"id": o.id, "room_id": o.room_id, "kind": o.kind, "edge": o.edge,
                      "offset": o.offset, "width": o.width} for f in data.floors
                     for o in f.openings],
        "furniture": [{"id": i.id, "kind": i.kind, "room_id": i.room_id, "label": i.label,
                       "x": i.x, "y": i.y} for f in data.floors for i in f.furniture],
        "room_types": sorted(OPERATIONS.keys()),
        "materials": list(data.materials.model_dump().keys()),
        "requirements": data.requirements.model_dump(),
    }


def _validate_suggestion(data: ProjectData, raw: str) -> Optional[ParsedCommand]:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    operations: List[Dict[str, Any]] = []
    for item in payload.get("operations") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("op") or "")
        params = item.get("params") or {}
        if name not in OPERATIONS or not isinstance(params, dict):
            continue
        operations.append({"op": name, "params": _coerce_params(data, name, dict(params))})
    if not operations and not payload.get("clarification"):
        return None
    structural = any(op["op"] in ("resize_room", "add_room", "delete_room", "swap_rooms",
                                  "split_room", "move_room", "regenerate", "adjust_setback",
                                  "change_requirements") for op in operations)
    warnings: List[str] = []
    if structural:
        warnings.append("This changes walls or the layout. Which walls carry load must be confirmed "
                        "by a structural engineer before anything is built.")
    return ParsedCommand(
        intent=operations[0]["op"] if operations else "clarify",
        operations=operations,
        explanation=str(payload.get("explanation") or "Suggested change"),
        clarification=str(payload.get("clarification") or ""),
        confidence=0.6 if operations else 0.0, structural=structural, warnings=warnings)


def _coerce_params(data: ProjectData, op: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Map model output onto real ids, dropping anything that does not exist."""
    out = dict(params)
    room_ref = out.get("room") or out.get("room_id")
    if room_ref and op in ("rename_room", "resize_room", "move_room", "delete_room", "swap_rooms",
                           "split_room", "room_finish", "add_opening", "add_furniture"):
        from .ops import find_room
        _, room = find_room(data, str(room_ref) if str(room_ref).startswith("rm_") else None,
                            str(room_ref))
        if room is None:
            return {}
        out["room"] = room.name
        out["room_id"] = room.id
    other = out.get("other_room")
    if other and op == "swap_rooms":
        from .ops import find_room
        _, room = find_room(data, None, str(other))
        if room is None:
            return {}
        out["other_room"] = room.name
        out["other_room_id"] = room.id
    for key in ("scale", "area", "width", "depth", "delta_width", "delta_depth", "distance",
                "fraction", "x", "y", "value", "offset", "delta"):
        if key in out:
            try:
                out[key] = float(out[key])
            except (TypeError, ValueError):
                out.pop(key)
    return out


def preview(data: ProjectData, parsed: ParsedCommand) -> Dict[str, Any]:
    """Dry-run the operations and describe the effect."""
    if parsed.clarification and not parsed.operations:
        return {"ok": False, "needs_input": True, "question": parsed.clarification,
                "applied": [], "rejected": [], "notes": [], "changed_rooms": [],
                "new_issues": [], "fixed_issues": [], "structural": False,
                "area_before_m2": round(sum(r.area for f in data.floors for r in f.rooms
                                            if not r.is_outdoor), 2),
                "area_after_m2": round(sum(r.area for f in data.floors for r in f.rooms
                                           if not r.is_outdoor), 2),
                "area_delta_m2": 0.0,
                "warning": "Nothing was changed."}
    result = preview_operations(data, parsed.operations)
    result["needs_input"] = False
    result["question"] = parsed.clarification
    result["explanation"] = parsed.explanation
    result["intent"] = parsed.intent
    result["confidence"] = parsed.confidence
    result["warnings"] = list(parsed.warnings)
    result["preview"].model_dump  # touch to ensure the object is materialised
    return result


def commit(data: ProjectData, parsed: ParsedCommand, *, message: str = "") -> Tuple[ProjectData, List[str], bool]:
    """Apply the operations to ``data`` in place and log the change in its history."""
    data, notes, structural = apply_operations(data, parsed.operations)
    data.history.append({
        "kind": "command", "message": message or parsed.explanation or parsed.intent,
        "intent": parsed.intent, "operations": parsed.operations, "notes": notes,
        "structural": structural, "provider": "assistant",
    })
    return data, notes, structural


def apply_preview(candidate: ProjectData, original: ProjectData, message: str = "") -> ProjectData:
    """Promote a previewed candidate to the live document, keeping the history log."""
    candidate.history = list(original.history) + [{
        "kind": "command", "message": message or "Applied an approved AI command",
        "notes": ["Change approved from the preview."],
    }]
    return candidate


def help_text() -> str:
    from .parser import _help_text
    return _help_text()


def operation_catalogue() -> List[Dict[str, Any]]:
    """Machine readable list used by the UI to build the command help panel."""
    descriptions = {
        "rename_room": "Rename a room",
        "resize_room": "Resize a room (wall moves into the neighbour when possible)",
        "move_room": "Move a room to a new position",
        "delete_room": "Delete a room, optionally giving its space to a neighbour",
        "add_room": "Add a new room, optionally next to an existing one",
        "swap_rooms": "Swap two rooms on the same floor",
        "split_room": "Split one room into two",
        "change_material": "Change one material or colour",
        "change_style": "Apply a whole design style",
        "room_finish": "Change one room's wall colour or flooring",
        "add_opening": "Add a door, window, sliding door or vent",
        "move_opening": "Slide a door or window along its wall",
        "delete_opening": "Delete a door or window",
        "add_furniture": "Add a furniture item to a room",
        "move_furniture": "Move a furniture item",
        "delete_furniture": "Remove a furniture item",
        "change_requirements": "Change the brief (bedrooms, floors, balcony...) -- needs regeneration",
        "adjust_setback": "Change a setback -- needs regeneration",
        "regenerate": "Regenerate the whole layout",
    }
    out = [{"op": key, "description": descriptions.get(key, key.replace("_", " ").title()),
            "structural": key in ("resize_room", "add_room", "delete_room", "swap_rooms",
                                  "split_room", "move_room", "regenerate", "adjust_setback",
                                  "change_requirements")}
           for key in sorted(OPERATIONS)]
    return out
