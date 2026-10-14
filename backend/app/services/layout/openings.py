"""Door, window and circulation placement.

Doors are placed so that every room is reachable from the entrance, attached
bathrooms open into their own bedroom, and wet rooms get a ventilation opening.
Windows are placed only on genuinely exterior walls -- a room that ends up with no
external wall is reported as a design warning rather than silently given a fake
window.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ...schemas import Edge, Floor, Opening, Plot, Requirements, Room
from .concepts import Concept
from .partition import r3

ACCESS_RANK = {
    "corridor": 0, "lobby": 1, "stairs": 2, "living": 3, "dining": 4, "open_to_below": 5,
    "shop": 5, "porch": 6, "balcony": 7, "courtyard": 8, "kitchen": 9, "study": 10,
    "puja": 10, "servant_room": 11, "bedroom": 12, "master_bedroom": 12, "store": 13,
    "utility": 14, "bathroom": 15, "toilet": 15, "garden": 9, "terrace": 8, "parking": 6,
}

DOOR_WIDTHS = {
    "bathroom": 0.78, "toilet": 0.72, "store": 0.8, "utility": 0.8, "puja": 0.9,
    "stairs": 0.9, "corridor": 0.9, "servant_room": 0.85,
}
DEFAULT_DOOR_WIDTH = 0.9
MAIN_DOOR_WIDTH = 1.1
SLIDING_WIDTH = 1.5

WINDOW_RULES = {
    # type: (min_w, max_w, share_of_wall, sill, height)
    "living": (1.2, 2.4, 0.34, 0.95, 1.5),
    "dining": (1.0, 1.8, 0.28, 0.95, 1.35),
    "kitchen": (0.9, 1.4, 0.26, 1.10, 1.05),
    "utility": (0.6, 1.0, 0.22, 1.10, 0.9),
    "bedroom": (1.0, 1.5, 0.28, 0.95, 1.35),
    "master_bedroom": (1.2, 1.8, 0.30, 0.95, 1.5),
    "bathroom": (0.5, 0.75, 0.20, 1.80, 0.6),
    "toilet": (0.4, 0.6, 0.18, 1.80, 0.5),
    "study": (1.0, 1.5, 0.28, 0.95, 1.35),
    "puja": (0.5, 0.9, 0.2, 1.20, 0.8),
    "store": (0.0, 0.0, 0.0, 0.0, 0.0),
    "stairs": (0.7, 1.1, 0.22, 1.20, 1.1),
    "lobby": (0.7, 1.2, 0.22, 0.95, 1.2),
    "corridor": (0.6, 1.0, 0.2, 1.10, 0.9),
    "servant_room": (0.9, 1.2, 0.25, 0.95, 1.2),
    "shop": (1.2, 2.4, 0.4, 0.6, 1.8),
}

CLEARANCE = 0.35          # keep-out distance between openings / from corners
EDGE_ORDER: Tuple[Edge, ...] = ("S", "N", "E", "W")


def _edge_span(room: Room, edge: Edge) -> Tuple[float, float]:
    if edge in ("N", "S"):
        return (0.0, room.w)
    return (0.0, room.d)


def find_offset(room: Room, edge: Edge, width: float, used: Dict[Tuple[str, Edge], List[Tuple[float, float]]],
                prefer: Optional[float] = None) -> Optional[float]:
    """Find a collision free offset (centre of the opening) along a room edge.

    Blocked spans are tracked in *world* coordinates so a door reserved on one
    side of a wall also blocks the matching position on the other side.
    """
    length = room.w if edge in ("N", "S") else room.d
    base = room.x if edge in ("N", "S") else room.y
    if length < width + 0.2:
        return None
    blocked = used.setdefault((room.id, edge), [])
    candidates: List[float] = []
    if prefer is not None:
        candidates.append(prefer)
    candidates.append(length / 2.0)
    candidates.append(width / 2 + CLEARANCE)
    candidates.append(length - width / 2 - CLEARANCE)
    for frac in (0.3, 0.7, 0.22, 0.78, 0.4, 0.6):
        candidates.append(length * frac)

    for cand in candidates:
        c0 = cand - width / 2
        c1 = cand + width / 2
        if c0 < CLEARANCE * 0.55 or c1 > length - CLEARANCE * 0.55:
            continue
        w0, w1 = base + c0, base + c1
        if any(not (w1 + CLEARANCE <= b0 or w0 - CLEARANCE >= b1) for b0, b1 in blocked):
            continue
        blocked.append((r3(w0), r3(w1)))
        return r3(cand)
    return None


def _reserve(used: Dict[Tuple[str, Edge], List[Tuple[float, float]]], room: Room, edge: Edge,
             offset: float, width: float) -> None:
    base = room.x if edge in ("N", "S") else room.y
    blocked = used.setdefault((room.id, edge), [])
    blocked.append((r3(base + offset - width / 2), r3(base + offset + width / 2)))


def add_opening(floor: Floor, room: Room, edge: Edge, offset: float, width: float, kind: str,
                connects_to: Optional[str], used: Dict, height: float = 2.1, sill: float = 0.0,
                style: str = "flush", material: str = "wood") -> Optional[Opening]:
    op = Opening(
        kind=kind, room_id=room.id, floor_id=floor.id, edge=edge, offset=r3(offset),
        width=r3(width), height=r3(height), sill=r3(sill), connects_to=connects_to,
        style=style, material=material,
    )
    floor.openings.append(op)
    _reserve(used, room, edge, offset, width)
    if connects_to and connects_to != "exterior":
        other = floor.room(connects_to)
        if other is not None:
            from ..geometry import OPPOSITE_EDGE
            opp = OPPOSITE_EDGE[edge]
            other_base = other.x if opp in ("N", "S") else other.y
            world = (room.x if edge in ("N", "S") else room.y) + offset
            _reserve(used, other, opp, world - other_base, width)
    return op


def place_openings(floors: List[Floor], plot: Plot, concept: Concept, req: Requirements) -> List[str]:
    """Populate ``floor.openings`` for every floor. Returns planning notes."""
    notes: List[str] = []
    road = plot.road_side

    for floor in floors:
        used: Dict[Tuple[str, Edge], List[Tuple[float, float]]] = {}
        rooms = floor.rooms
        if not rooms:
            continue
        adj: Dict[str, List[Tuple[str, Edge, float, float]]] = {}
        for room in rooms:
            adj[room.id] = []
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                from ..geometry import neighbour_on, OPPOSITE_EDGE
                hit = neighbour_on(a, b)
                if hit:
                    edge, lo, hi = hit
                    adj[a.id].append((b.id, edge, lo, hi))
                    adj[b.id].append((a.id, OPPOSITE_EDGE[edge], lo, hi))

        # ------------------------------------------------ entrance door
        entrance_room: Optional[Room] = None
        entrance_edge: Optional[Edge] = None
        if floor.index == 0:
            preferred = ["lobby", "living", "porch", "shop", "dining", "corridor", "kitchen", "bedroom"]
            for kind in preferred:
                for room in rooms:
                    if room.type != kind or room.is_outdoor:
                        continue
                    edge = _road_facing_exterior_edge(room, rooms, road)
                    if edge:
                        entrance_room, entrance_edge = room, edge
                        break
                if entrance_room:
                    break
            if entrance_room and entrance_edge:
                width = MAIN_DOOR_WIDTH * (1.1 if req.finish_level == "premium" else 1.0)
                length = entrance_room.w if entrance_edge in ("N", "S") else entrance_room.d
                offset = find_offset(entrance_room, entrance_edge, width, used, prefer=length / 2)
                if offset is not None:
                    add_opening(floor, entrance_room, entrance_edge, offset, width, "door", "exterior",
                                used, style="main", material=req.preferred_materials[0] if req.preferred_materials else "wood")
                    entrance_room.tags.append("entrance")
                    notes.append(f"Main entrance placed on the {entrance_edge} (road side) wall of the {entrance_room.name}.")
                else:
                    notes.append(f"The {entrance_room.name} wall facing the road is too short for a "
                                 f"{width:.2f} m entrance door; reconsider the layout.")

        # ------------------------------------------------ attached bathrooms first
        for room in rooms:
            attached_to = next((t.split(":", 1)[1] for t in room.tags if t.startswith("attached_to:")), None)
            if not attached_to:
                continue
            parent = floor.room(attached_to)
            link = next((n for n in adj[room.id] if n[0] == attached_to), None)
            if parent is None or link is None:
                notes.append(f"{room.name} was meant to be attached to a bedroom but does not touch it. "
                             f"It will be entered from another space instead.")
                continue
            _, edge, lo, hi = link
            width = DOOR_WIDTHS.get(room.type, DEFAULT_DOOR_WIDTH)
            offset = find_offset(room, edge, width, used, prefer=(lo + hi) / 2 - (room.x if edge in ("N", "S") else room.y))
            if offset is not None:
                add_opening(floor, room, edge, offset, width, "door", parent.id, used)

        # ------------------------------------------------ reachable-set doors (BFS)
        reachable = {entrance_room.id} if entrance_room else set()
        has_door = {op.room_id for op in floor.openings}
        reachable |= {rid for rid in has_door}

        def rank(room: Room) -> int:
            return ACCESS_RANK.get(room.type, 12)

        guard = 0
        while guard < 400:
            guard += 1
            pending = [r for r in rooms if r.id not in reachable and not r.is_outdoor]
            if not pending:
                break
            best = None
            for room in pending:
                for other_id, edge, lo, hi in adj[room.id]:
                    if other_id not in reachable:
                        continue
                    other = floor.room(other_id)
                    if other is None:
                        continue
                    span = hi - lo
                    score = (rank(other), -span)
                    if best is None or score < best[0]:
                        best = (score, room, other, edge, lo, hi)
            if best is None:
                # nothing reachable touches it -- connect to the largest neighbour anyway
                room = pending[0]
                if not adj[room.id]:
                    notes.append(f"{room.name} is completely enclosed by other rooms: it has no door and no "
                                 f"external wall. Move it or add circulation space.")
                    reachable.add(room.id)
                    continue
                other_id, edge, lo, hi = max(adj[room.id], key=lambda n: n[3] - n[2])
                other = floor.room(other_id)
                best = ((99, 0), room, other, edge, lo, hi)

            _, room, other, edge, lo, hi = best
            width = DOOR_WIDTHS.get(room.type, DEFAULT_DOOR_WIDTH)
            base = room.x if edge in ("N", "S") else room.y
            prefer = (lo + hi) / 2 - base
            offset = find_offset(room, edge, width, used, prefer=prefer)
            if offset is None:
                offset = find_offset(room, edge, width, used)
            if offset is None:
                placed = False
                for alt_edge in EDGE_ORDER:
                    if alt_edge == edge:
                        continue
                    link = next((n for n in adj[room.id] if n[1] == alt_edge), None)
                    if not link:
                        continue
                    _, _, alo, ahi = link
                    abase = room.x if alt_edge in ("N", "S") else room.y
                    offset = find_offset(room, alt_edge, width, used, prefer=(alo + ahi) / 2 - abase)
                    if offset is not None:
                        edge, other = alt_edge, floor.room(link[0])
                        placed = True
                        break
                if not placed:
                    notes.append(f"No wall of {room.name} has room for a door in this layout.")
                    reachable.add(room.id)
                    continue
            add_opening(floor, room, edge, offset, width, "door", other.id if other else None, used)
            reachable.add(room.id)
            if other:
                reachable.add(other.id)

        # ------------------------------------------------ outdoor rooms
        for room in rooms:
            if not room.is_outdoor:
                continue
            if room.type in ("garden", "parking", "terrace") and floor.index == 0 and room.type != "terrace":
                pass
            for other_id, edge, lo, hi in adj[room.id]:
                other = floor.room(other_id)
                if other is None or other.is_outdoor:
                    continue
                span = hi - lo
                if span < 1.2:
                    continue
                width = min(SLIDING_WIDTH, span - 0.7) if room.type in ("balcony", "courtyard", "garden", "terrace") \
                    else min(DEFAULT_DOOR_WIDTH, span - 0.6)
                if width < 0.7:
                    continue
                kind = "sliding" if width >= 1.2 else "door"
                base = other.x if edge in ("N", "S") else other.y
                from ..geometry import OPPOSITE_EDGE
                offset = find_offset(other, OPPOSITE_EDGE[edge], width, used, prefer=(lo + hi) / 2 - base)
                if offset is None:
                    continue
                add_opening(floor, other, OPPOSITE_EDGE[edge], offset, width, kind, room.id, used,
                            height=2.15, style="sliding" if kind == "sliding" else "flush",
                            material="aluminium" if kind == "sliding" else "wood")
                break

        # ------------------------------------------------ windows
        from ..geometry import exterior_edges
        for room in rooms:
            if room.type == "store" or room.is_outdoor:
                continue
            rule = WINDOW_RULES.get(room.type)
            if not rule or rule[1] <= 0:
                continue
            min_w, max_w, share, sill, height = rule
            if concept.glazing == "large":
                max_w *= 1.35
                share *= 1.25
            elif concept.glazing == "minimal":
                max_w *= 0.8
                share *= 0.85
            ext = exterior_edges(room, rooms)
            if not ext:
                if not room.is_outdoor:
                    notes.append(f"{room.name} has no external wall, so it cannot get natural light or "
                                 f"ventilation in this layout.")
                continue
            ext_sorted = sorted(ext, key=lambda e: -(e[2] - e[1]))
            targets = 1
            if room.type in ("living", "master_bedroom", "dining", "shop") and len(ext_sorted) > 1:
                targets = 2
            if room.type in ("bedroom", "kitchen", "study") and len(ext_sorted) > 1 and concept.glazing != "minimal":
                targets = 2
            placed = 0
            for edge, lo, hi in ext_sorted:
                if placed >= targets:
                    break
                length = hi - lo
                width = min(max_w, max(min_w, length * share))
                if length < width + 0.7:
                    width = max(min_w * 0.8, length - 0.7)
                if width < 0.4:
                    continue
                base = room.x if edge in ("N", "S") else room.y
                offset = find_offset(room, edge, width, used, prefer=(lo + hi) / 2 - base)
                if offset is None:
                    continue
                add_opening(floor, room, edge, offset, width, "window", "exterior", used,
                            height=height, sill=sill, style="casement", material="aluminium")
                placed += 1
            if placed == 0 and not room.is_outdoor:
                notes.append(f"Could not fit a window on the external walls of {room.name} "
                             f"(walls are too short or already used by doors).")
            if placed >= 2 and room.type in ("living", "master_bedroom", "bedroom", "kitchen"):
                notes.append(f"{room.name} has windows on two walls for cross ventilation.")

    return notes


def _road_facing_exterior_edge(room: Room, rooms: List[Room], road: str) -> Optional[Edge]:
    from ..geometry import exterior_edges
    ext = exterior_edges(room, rooms)
    for edge, lo, hi in ext:
        if edge == road and (hi - lo) >= MAIN_DOOR_WIDTH + 0.7:
            return edge
    for edge, lo, hi in ext:
        if (hi - lo) >= MAIN_DOOR_WIDTH + 0.7:
            return edge
    return None


def opening_summary(floor: Floor) -> Dict[str, int]:
    counts: Dict[str, int] = {"door": 0, "window": 0, "sliding": 0, "vent": 0, "pass_through": 0}
    for op in floor.openings:
        counts[op.kind] = counts.get(op.kind, 0) + 1
    return counts
