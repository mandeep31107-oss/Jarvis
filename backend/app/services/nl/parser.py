"""Natural language command parsing (rule based, deterministic).

The parser turns sentences like "make the kitchen more spacious" or "add a study
room near the bedroom" into the structured operations in :mod:`ops`. It never edits
anything itself -- it produces operations that are previewed, then approved or
rejected by the user.

A hosted model may be asked for help when the rules are not confident (see
``commands.interpret``), but its output is validated against the same operation
schema, so an unknown or malformed suggestion is rejected rather than applied.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ...schemas import ProjectData, Room
from ..ai.base import ParsedCommand
from ..room_catalog import ROOM_LABELS, ROOM_SPECS
from ..units import parse_dimension
from .ops import find_room

DIRECTIONS = {"north": "N", "south": "S", "east": "E", "west": "W", "up": "N", "down": "S",
              "left": "W", "right": "E", "n": "N", "s": "S", "e": "E", "w": "W"}

STYLE_WORDS = {
    "modern": "modern", "contemporary": "contemporary", "minimalist": "minimalist",
    "minimal": "minimalist", "traditional": "traditional_indian", "indian": "traditional_indian",
    "scandinavian": "scandinavian", "nordic": "scandinavian", "industrial": "industrial",
    "luxury": "luxury", "premium": "luxury", "rustic": "rustic", "mediterranean": "mediterranean",
    "japanese": "japanese", "zen": "japanese", "budget": "budget_modern",
}

CONCEPT_WORDS = {
    "space efficient": "space_efficient", "space-efficient": "space_efficient",
    "compact": "space_efficient", "budget": "budget_conscious", "cheap": "budget_conscious",
    "economical": "budget_conscious", "family": "family_comfort", "comfort": "family_comfort",
    "modern contemporary": "modern_contemporary", "contemporary": "modern_contemporary",
    "traditional": "traditional_indian", "indian": "traditional_indian", "vastu": "traditional_indian",
}

MATERIAL_WORDS = {
    "marble": ("floor_material", "marble"), "granite": ("floor_material", "granite"),
    "wooden floor": ("floor_material", "wood"), "wood flooring": ("floor_material", "wood"),
    "wood": ("floor_material", "wood"), "tile": ("floor_material", "tile"),
    "tiles": ("floor_material", "tile"), "kota": ("floor_material", "kota_stone"),
    "concrete floor": ("floor_material", "concrete"),
    "flat roof": ("roof_type", "flat"), "gable roof": ("roof_type", "gable"),
    "sloped roof": ("roof_type", "gable"), "hip roof": ("roof_type", "hip"),
    "shed roof": ("roof_type", "shed"),
    "false ceiling": ("ceiling_style", "cove"), "cove ceiling": ("ceiling_style", "cove"),
    "plain ceiling": ("ceiling_style", "plain"), "exposed beams": ("ceiling_style", "beam"),
    "glass doors": ("door_material", "glass"), "wooden doors": ("door_material", "wood"),
    "metal doors": ("door_material", "metal"),
    "boundary wall": ("boundary_wall", True), "no boundary wall": ("boundary_wall", False),
    "landscaping": ("landscaping", True), "no landscaping": ("landscaping", False),
    "exterior lighting": ("exterior_lighting", True),
}

COLOR_NAMES = {
    "white": "#f7f5f0", "off white": "#f0ece3", "cream": "#f2e8d5", "beige": "#e0d3bb",
    "grey": "#9a9a97", "gray": "#9a9a97", "light grey": "#d5d3cd", "light gray": "#d5d3cd",
    "dark grey": "#5c5f63", "charcoal": "#3b3f43", "black": "#22252a", "blue": "#4a6fa5",
    "navy": "#2f3f5c", "sky blue": "#a8c8e0", "green": "#6f8f6a", "olive": "#7d8450",
    "yellow": "#e0c46a", "mustard": "#c9a227", "orange": "#d98a3d", "terracotta": "#c46a4a",
    "red": "#9c3b32", "maroon": "#6d2b2b", "pink": "#d9a7b0", "brown": "#7d5a3c",
    "wood brown": "#8a5a34", "teal": "#3f7a78", "purple": "#6b4f7a", "lavender": "#b7a6c9",
    "sand": "#d9c9a8", "ivory": "#f6f1e4", "sage": "#a9b39a",
}

ROOM_ALIASES = {
    "master": "master_bedroom", "master bedroom": "master_bedroom", "bed": "bedroom",
    "beds": "bedroom", "bath": "bathroom", "bathroom": "bathroom", "toilet": "toilet",
    "wc": "toilet", "kitchen": "kitchen", "living": "living", "hall": "living",
    "living room": "living", "dining": "dining", "dining room": "dining", "study": "study",
    "office": "study", "pooja": "puja", "puja": "puja", "prayer": "puja", "store": "store",
    "storage": "store", "utility": "utility", "wash": "utility", "laundry": "utility",
    "corridor": "corridor", "passage": "corridor", "lobby": "lobby", "entrance": "lobby",
    "foyer": "lobby", "stairs": "stairs", "staircase": "stairs", "balcony": "balcony",
    "terrace": "terrace", "garden": "garden", "courtyard": "courtyard", "parking": "parking",
    "garage": "parking", "porch": "porch", "verandah": "porch", "servant": "servant_room",
    "helper": "servant_room", "shop": "shop",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _find_room_ref(data: ProjectData, text: str) -> Tuple[Optional[str], List[str]]:
    """Best room reference in the sentence. Returns (query, ambiguous_names).

    The *earliest* mention wins, because instructions read as
    "<verb> <subject> ... <other room>": "delete the store room and give the space
    to the kitchen" is about the store room, not the kitchen.
    """
    t = _norm(text)
    best: Optional[Tuple[int, int, str, List[str]]] = None   # (pos, -len, ref, ambiguous)

    def consider(pos: int, ref: str, ambiguous: List[str]) -> None:
        nonlocal best
        if pos < 0:
            return
        key = (pos, -len(ref))
        if best is None or key < (best[0], best[1]):
            best = (pos, -len(ref), ref, ambiguous)

    for name in {r.name.lower() for f in data.floors for r in f.rooms}:
        if not name:
            continue
        pos = t.find(name)
        if pos >= 0:
            matches = [r for f in data.floors for r in f.rooms if r.name.lower() == name]
            consider(pos, name, [m.name for m in matches])
    for alias, room_type in ROOM_ALIASES.items():
        match = re.search(rf"\b{re.escape(alias)}\b", t)
        if not match:
            continue
        matches = [r for f in data.floors for r in f.rooms if r.type == room_type]
        if len(matches) == 1:
            consider(match.start(), matches[0].name, [matches[0].name])
        else:
            consider(match.start(), alias, [m.name for m in matches])
    if best is None:
        return None, []
    return best[2], best[3]


def _room_from_ref(data: ProjectData, ref: Optional[str]) -> Tuple[Optional[Room], List[str]]:
    if not ref:
        return None, []
    _, room = find_room(data, None, ref)
    if room is not None:
        same = [r for f in data.floors for r in f.rooms
                if r.name.lower() == ref.lower() or r.type.replace("_", " ") == ref.lower()]
        return room, [r.name for r in same]
    return None, []


def _dimension(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(ft|feet|foot|m|metre|meters|meter|cm|in|inch|inches|"
                      r"sqft|sq ft|sqm|m2|sq m|gaj|sqyd|sq yd)\b", _norm(text))
    if match:
        value = parse_dimension(f"{match.group(1)} {match.group(2)}")
        if value is not None:
            return float(value)
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:m|meters?|metres?)\b", _norm(text))
    if match:
        return float(match.group(1))
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:ft|feet|foot)\b", _norm(text))
    if match:
        return float(match.group(1)) * 0.3048
    return None


def _area(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(sq\s?ft|sqft|square feet|square foot)", _norm(text))
    if match:
        return float(match.group(1)) * 0.092903
    match = re.search(r"(\d+(?:\.\d+)?)\s*(sq\s?m|sqm|m2|square met(?:re|er)s?)", _norm(text))
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*(gaj|sq\s?yd|sqyd|square yards?)", _norm(text))
    if match:
        return float(match.group(1)) * 0.836127
    return None


def _percent(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", _norm(text))
    return float(match.group(1)) / 100.0 if match else None


def _direction(text: str) -> Optional[str]:
    t = _norm(text)
    for word, code in DIRECTIONS.items():
        if re.search(rf"\b{re.escape(word)}\b", t):
            return code
    return None


def _color(text: str) -> Optional[str]:
    t = _norm(text)
    match = re.search(r"#[0-9a-fA-F]{6}\b", t)
    if match:
        return match.group(0)
    for name, hexcode in sorted(COLOR_NAMES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(name)}\b", t):
            return hexcode
    return None


def _room_type(text: str) -> Optional[str]:
    t = _norm(text)
    for alias, room_type in sorted(ROOM_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(alias)}\b", t):
            return room_type
    for key, label in ROOM_LABELS.items():
        if label.lower() in t:
            return key
    return None


def _second_room(data: ProjectData, text: str, first: Optional[str]) -> Optional[str]:
    """Find a second room reference (for 'swap A and B', 'near B')."""
    t = _norm(text)
    parts = re.split(r"\band\b|\bwith\b|\bnear\b|\bnext to\b|\bbeside\b|\bopposite\b", t)
    for part in reversed(parts[1:] if len(parts) > 1 else parts):
        ref, _ = _find_room_ref(data, part)
        if ref and ref != first:
            return ref
    return None


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

def _rule_style(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(style|theme|look|palette|interior style|design style)\b", t) and \
            not re.search(r"\b(use|apply|switch to|change to|make it)\b.*\b(style|look|theme)\b", t):
        return None
    for word, key in sorted(STYLE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(word)}\b", t):
            return [{"op": "change_style", "params": {"style": key}}], \
                f"Switch the whole design to the {key.replace('_', ' ')} style", 0.85
    return None


def _rule_material(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    ops: List[Dict[str, Any]] = []
    color = _color(text)
    if color and re.search(r"\b(wall|walls|paint|colour|color)\b", t) and \
            not re.search(r"\b(floor|ceiling|roof)\b", t):
        room_ref, _ = _find_room_ref(data, t)
        if room_ref and re.search(r"\b(in|of|for)\b", t):
            ops.append({"op": "room_finish", "params": {"room": room_ref, "wall_color": color}})
        else:
            target = "feature_wall" if re.search(r"\b(feature|accent)\b", t) else "interior_wall"
            if re.search(r"\b(exterior|facade|outside)\b", t):
                target = "exterior_wall"
            ops.append({"op": "change_material", "params": {"field": target, "value": color}})
    if not ops and color and re.search(r"\b(floor|flooring|tile)\b", t):
        ops.append({"op": "change_material", "params": {"field": "floor_color", "value": color}})
    if not ops and color and re.search(r"\b(roof)\b", t):
        ops.append({"op": "change_material", "params": {"field": "roof_color", "value": color}})
    for phrase, (field, value) in sorted(MATERIAL_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            ops.append({"op": "change_material", "params": {"field": field, "value": value}})
            break
    if not ops:
        return None
    return ops, "Change materials/colours (appearance only, no geometry)", 0.8


def _rule_resize(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(bigger|larger|spacious|more spacious|expand|enlarge|increase|grow|"
                     r"smaller|reduce|shrink|decrease|resize|make it|wider|narrower|longer)\b", t):
        return None
    room_ref, ambiguous = _find_room_ref(data, t)
    if not room_ref:
        return None
    room, same = _room_from_ref(data, room_ref)
    if room is None:
        return [], (f"There is no '{room_ref}' in this design to resize. Rooms in the model: "
                    + ", ".join(sorted({r.name for f in data.floors for r in f.rooms})[:12])
                    + "."), 0.0
    grow = bool(re.search(r"\b(bigger|larger|spacious|expand|enlarge|increase|grow|wider|longer)\b", t))
    params: Dict[str, Any] = {"room": room.name, "room_id": room.id}
    pct = _percent(text)
    area = _area(text)
    dim = _dimension(text)
    edge = None
    match = re.search(r"\b(north|south|east|west)\s+(?:wall|side|direction)\b", t)
    if match:
        edge = DIRECTIONS[match.group(1)]
    if pct:
        params["scale"] = round(math_sqrt(1 + (pct if grow else -pct)), 4)
    elif area:
        params["area"] = round(area, 2)
    elif dim and re.search(r"\bby\b", t):
        delta = dim if grow else -dim
        if edge in ("E", "W"):
            params["delta_width"] = delta
        elif edge in ("N", "S"):
            params["delta_depth"] = delta
        else:
            params["scale"] = round(math_sqrt(max(0.5, 1 + delta / max(1.0, min(room.w, room.d)))), 4)
    else:
        params["scale"] = 1.15 if grow else 0.9
    if edge:
        params["edge"] = edge
    if len(same) > 1 and ambiguous and room_ref in ROOM_ALIASES:
        return [{"op": "resize_room", "params": params}], \
            f"Resize {room.name} (there are {len(same)} rooms matching '{room_ref}')", 0.45
    return [{"op": "resize_room", "params": params}], \
        f"Resize {room.name} ({'larger' if grow else 'smaller'})", 0.8


def math_sqrt(value: float) -> float:
    return value ** 0.5


LOCATION_SPLIT = re.compile(r"\b(?:near|next to|beside|adjacent to|opposite|behind|in front of|"
                            r"attached to|close to)\b")


def _rule_add_room(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(add|create|insert|include|put in|need|make room for|build)\b", t):
        return None
    # "make the kitchen bigger" is a resize, not a new room
    if re.search(r"\b(bigger|larger|spacious|smaller|wider|narrower|longer|shorter)\b", t):
        return None
    if not re.search(r"\b(room|space|area|bathroom|toilet|bedroom|kitchen|study|store|"
                     r"balcony|pooja|puja|garage|parking|wardrobe)\b", t):
        return None
    if re.search(r"\b(window|door|furniture|sofa|bed|table|chair|light|fan|opening)\b", t):
        return None
    # the location clause names an existing room, not the new room's type
    near = None
    parts = LOCATION_SPLIT.split(t)
    type_text = parts[0]
    if len(parts) > 1:
        near = _find_room_ref(data, parts[1])[0]
        if near is None:
            type_text = t
    room_type = _room_type(type_text) or _room_type(t)
    if room_type is None:
        return None
    if near is None:
        near = _second_room(data, t, None)
    params: Dict[str, Any] = {"type": room_type}
    area = _area(text)
    dim = _dimension(text)
    if area:
        params["area"] = round(area, 2)
    elif dim and dim > 1.2:
        params["width"] = round(dim, 2)
        params["depth"] = round(dim, 2)
    if near:
        params["near"] = near
    match = re.search(r"\b(ground|first|second|third|terrace)\b", t)
    if match:
        params["floor"] = match.group(1)
    return [{"op": "add_room", "params": params}], \
        f"Add a {ROOM_LABELS.get(room_type, room_type)}" + (f" near {near}" if near else ""), 0.8


def _rule_delete(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(delete|remove|drop|get rid of|take out|no longer need)\b", t):
        return None
    room_ref, _ = _find_room_ref(data, t)
    if room_ref:
        room, _ = _room_from_ref(data, room_ref)
        if room is None:
            return [], (f"There is no '{room_ref}' in this design, so there is nothing to delete. "
                        f"Rooms in the model: "
                        + ", ".join(sorted({r.name for f in data.floors for r in f.rooms})[:12])
                        + "."), 0.0
        give = _second_room(data, t, room_ref)
        params: Dict[str, Any] = {"room": room.name, "room_id": room.id}
        if give:
            params["give_space_to"] = give
        return [{"op": "delete_room", "params": params}], f"Delete {room.name}", 0.8
    match = re.search(r"\b(window|door|sliding door|vent|opening)\b", t)
    if match:
        return None  # handled by the opening rules with an explicit id from the UI
    return None


def _rule_swap(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(swap|exchange|interchange|switch places)\b", t):
        return None
    parts = re.split(r"\band\b|\bwith\b", t)
    if len(parts) < 2:
        return None
    ref_a, _ = _find_room_ref(data, parts[0])
    ref_b, _ = _find_room_ref(data, parts[-1])
    if not ref_a or not ref_b or ref_a == ref_b:
        return None
    room_a, _ = _room_from_ref(data, ref_a)
    room_b, _ = _room_from_ref(data, ref_b)
    if room_a is None or room_b is None:
        return None
    return [{"op": "swap_rooms", "params": {"room": room_a.name, "room_id": room_a.id,
                                            "other_room": room_b.name,
                                            "other_room_id": room_b.id}}], \
        f"Swap {room_a.name} with {room_b.name}", 0.85


def _rule_move(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(move|shift|slide|drag|relocate)\b", t):
        return None
    direction = _direction(t)
    distance = _dimension(text)
    room_ref, _ = _find_room_ref(data, t)
    if room_ref and not re.search(r"\b(sofa|bed|table|chair|wardrobe|tv|furniture|desk)\b", t):
        room, _ = _room_from_ref(data, room_ref)
        if room is None:
            return None
        params: Dict[str, Any] = {"room": room.name, "room_id": room.id}
        if direction:
            params["direction"] = direction
        if distance:
            params["distance"] = round(distance, 2)
        if not direction and not distance:
            return None
        return [{"op": "move_room", "params": params}], \
            f"Move {room.name} {direction or ''} {distance or 1.0:.2f} m", 0.75
    item_kind = re.search(r"\b(sofa|bed|table|chair|wardrobe|tv unit|desk|dining table|"
                          r"study table|shelf|dresser)\b", t)
    if item_kind:
        kind = item_kind.group(1).replace(" ", "_")
        for floor in data.floors:
            for item in floor.furniture:
                if item.kind == kind or item.kind.replace("_", " ") == item_kind.group(1):
                    params = {"item_id": item.id}
                    if direction:
                        params["x" if direction in ("E", "W") else "y"] = round(
                            (item.x if direction in ("E", "W") else item.y)
                            + (distance or 0.5) * (1 if direction in ("E", "N") else -1), 3)
                    return [{"op": "move_furniture", "params": params}], \
                        f"Move the {item.label or kind}", 0.6
    return None


def _rule_rename(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    match = re.search(r"\brename\b\s+(.*?)\s+(?:to|as)\s+(.+)$", t)
    if not match:
        match = re.search(r"\bcall\b\s+(.*?)\s+(.+)$", t)
    if not match:
        return None
    ref = _find_room_ref(data, match.group(1))[0]
    name = match.group(2).strip().strip(".!?'\"")
    if not ref or not name:
        return None
    room, _ = _room_from_ref(data, ref)
    if room is None:
        return None
    return [{"op": "rename_room", "params": {"room": room.name, "room_id": room.id,
                                             "name": name.title()}}], \
        f"Rename {room.name} to '{name.title()}'", 0.9


def _rule_split(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(split|divide|partition|carve)\b", t):
        return None
    ref, _ = _find_room_ref(data, t)
    if not ref:
        return None
    room, _ = _room_from_ref(data, ref)
    if room is None:
        return None
    new_type = None
    for alias, room_type in ROOM_ALIASES.items():
        if alias in t and alias not in ref:
            new_type = room_type
            break
    pct = _percent(text)
    params: Dict[str, Any] = {"room": room.name, "room_id": room.id}
    if new_type:
        params["new_type"] = new_type
    if pct:
        params["fraction"] = round(pct, 2)
    return [{"op": "split_room", "params": params}], \
        f"Split {room.name}" + (f" to make a {new_type.replace('_', ' ')}" if new_type else ""), 0.7


def _rule_opening(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    match = re.search(r"\b(add|put|place|install)\b.*\b(window|door|sliding door|vent)\b", t)
    if not match:
        return None
    kind = "sliding" if "sliding" in t else ("vent" if "vent" in t else match.group(2))
    ref, _ = _find_room_ref(data, t)
    room, _ = _room_from_ref(data, ref) if ref else (None, [])
    if room is None:
        return None
    edge = None
    match_edge = re.search(r"\b(north|south|east|west)\b", t)
    if match_edge:
        edge = DIRECTIONS[match_edge.group(1)]
    width = _dimension(text)
    params: Dict[str, Any] = {"room": room.name, "room_id": room.id, "kind": kind}
    if edge:
        params["edge"] = edge
    if width and 0.4 <= width <= 6:
        params["width"] = round(width, 2)
    return [{"op": "add_opening", "params": params}], \
        f"Add a {kind} to {room.name}" + (f" on the {edge} wall" if edge else ""), 0.8


def _rule_requirements(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    ops: List[Dict[str, Any]] = []
    label = []
    match = re.search(r"\b(\d+)\s*(floors?|storeys?|stories|levels?)\b", t)
    if match:
        ops.append({"op": "change_requirements", "params": {"patch": {"floors": int(match.group(1))}}})
        label.append(f"{match.group(1)} floor(s)")
    match = re.search(r"\b(\d+)\s*(bedrooms?|beds?)\b", t)
    if match:
        ops.append({"op": "change_requirements",
                    "params": {"patch": {"bedrooms": int(match.group(1))}}})
        label.append(f"{match.group(1)} bedroom(s)")
    match = re.search(r"\b(\d+)\s*(bathrooms?|baths?|toilets?)\b", t)
    if match:
        ops.append({"op": "change_requirements",
                    "params": {"patch": {"bathrooms": int(match.group(1))}}})
        label.append(f"{match.group(1)} bathroom(s)")
    for word, field in (("balcony", "wants_balcony"), ("terrace", "wants_terrace"),
                        ("garden", "wants_garden"), ("courtyard", "wants_courtyard"),
                        ("pooja", "wants_pooja"), ("puja", "wants_pooja"),
                        ("study", "wants_study"), ("store room", "wants_store"),
                        ("utility", "wants_utility"), ("servant", "wants_servant_room"),
                        ("shop", "wants_shop")):
        if re.search(rf"\b(add|include|want|need|no|remove|without)\b[^.]*\b{re.escape(word)}\b", t):
            negate = bool(re.search(rf"\b(no|remove|without|drop)\b[^.]*\b{re.escape(word)}\b", t))
            ops.append({"op": "change_requirements", "params": {"patch": {field: not negate}}})
            label.append(f"{field}={'off' if negate else 'on'}")
    match = re.search(r"\bfront setback\b[^.]*?(\d+(?:\.\d+)?)\s*(m|ft)?", t)
    if match:
        value = float(match.group(1)) * (0.3048 if match.group(2) == "ft" else 1.0)
        ops.append({"op": "adjust_setback", "params": {"side": data.plot.road_side.lower(),
                                                       "value": round(value, 2)}})
        label.append(f"front setback {value:.2f} m")
    if not ops:
        return None
    ops.append({"op": "regenerate", "params": {"concept": data.active_concept}})
    return ops, "Change requirements: " + ", ".join(label) + " (regenerates the plan)", 0.75


def _rule_regenerate(data: ProjectData, text: str) -> Optional[Tuple[List[Dict[str, Any]], str, float]]:
    t = _norm(text)
    if not re.search(r"\b(regenerate|redo|redesign|start over|try again|new layout|"
                     r"generate again|another option)\b", t):
        return None
    concept = None
    for phrase, key in sorted(CONCEPT_WORDS.items(), key=lambda kv: -len(kv[0])):
        if phrase in t:
            concept = key
            break
    params: Dict[str, Any] = {}
    if concept:
        params["concept"] = concept
    return [{"op": "regenerate", "params": params}], \
        f"Regenerate the layout" + (f" with the '{concept.replace('_', ' ')}' concept" if concept
                                    else ""), 0.8


RULES = (_rule_rename, _rule_swap, _rule_style, _rule_opening, _rule_resize, _rule_split,
         _rule_delete, _rule_add_room, _rule_move, _rule_requirements, _rule_regenerate,
         _rule_material)


def parse_command(data: ProjectData, text: str) -> ParsedCommand:
    """Parse one instruction. Low confidence means the UI should ask, not guess."""
    raw = (text or "").strip()
    if not raw:
        return ParsedCommand(intent="none", clarification="Type an instruction, for example "
                                                         "'make the kitchen more spacious'.")
    t = _norm(raw)

    if re.search(r"\b(help|what can you do|commands|examples)\b", t) and len(t.split()) <= 4:
        return ParsedCommand(intent="help", confidence=1.0, explanation=_help_text())

    for rule in RULES:
        result = rule(data, raw)
        if not result:
            continue
        ops, explanation, confidence = result
        if not ops:
            return ParsedCommand(intent="unresolved", confidence=0.0, clarification=explanation,
                                 preview_notes=[f"Parsed from: \"{raw}\""])
        warnings: List[str] = []
        structural = any(op["op"] in ("resize_room", "add_room", "delete_room", "swap_rooms",
                                      "split_room", "move_room", "regenerate", "adjust_setback",
                                      "change_requirements") for op in ops)
        if structural:
            warnings.append("This changes walls or the layout. Which walls are load bearing must be "
                            "confirmed by a structural engineer before anything is built.")
        if any(op["op"] == "regenerate" for op in ops):
            warnings.append("Regenerating replaces the current geometry, including any manual "
                            "edits. Save a version first if you want to keep them.")
        clarification = ""
        if confidence < 0.5:
            ref = next((op["params"].get("room") for op in ops if op["params"].get("room")), None)
            if ref:
                same = [r.name for f in data.floors for r in f.rooms
                        if r.type == ROOM_ALIASES.get(ref, "")]
                if len(same) > 1:
                    clarification = (f"There are several rooms matching '{ref}': "
                                     f"{', '.join(same)}. Which one do you mean?")
        return ParsedCommand(intent=ops[0]["op"], operations=ops, explanation=explanation,
                             confidence=confidence, clarification=clarification,
                             structural=structural, warnings=warnings,
                             preview_notes=[f"Parsed from: \"{raw}\""])

    return ParsedCommand(intent="unknown", confidence=0.0,
                         clarification="I did not recognise that instruction. " + _help_text())


def _help_text() -> str:
    return ("Things I can do: 'make the kitchen more spacious', 'add a study near the master "
            "bedroom', 'rename bedroom 2 to kids room', 'swap the kitchen and dining room', "
            "'delete the store room and give the space to the kitchen', 'move the sofa 1 m east', "
            "'paint the living room walls light grey', 'use marble flooring', "
            "'switch to the Scandinavian style', 'add a window on the north wall of the master "
            "bedroom', 'split the living room to add a pooja room', 'make it 3 floors', "
            "'set the front setback to 3 m', 'regenerate with the budget concept'. Every change is "
            "shown as a preview you approve or reject.")


def candidate_operations() -> List[Dict[str, Any]]:
    """Schema for the model-assisted path and for the UI's command help."""
    from .ops import OPERATIONS
    return [{"op": key} for key in sorted(OPERATIONS)]
