"""Design validation.

Every check produces a machine readable :class:`ValidationIssue` the UI shows next
to the plan. Checks are deliberately conservative: they flag things a human should
look at rather than pretending to certify a design. Nothing here verifies
structural safety, fire safety or legal compliance -- those need a licensed
professional, and the issues say so.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

from ..schemas import (Floor, Opening, ProjectData, Room, ValidationIssue,
                       ValidationResult)
from .geometry import room_rect
from .layout.partition import rect_area, rects_overlap
from .room_catalog import is_habitable, spec

Rect = Tuple[float, float, float, float]

OVERLAP_TOL = 0.02          # m² of accidental overlap tolerated
EDGE_TOL = 1e-3
MIN_DOOR_WIDTH = 0.7
DOOR_SWING_DEPTH = 0.9

_RANK = {"error": 0, "warning": 1, "info": 2}


def _issue(severity: str, code: str, message: str, *, floor: Optional[Floor] = None,
           rooms: Optional[List[Room]] = None, fix: Optional[str] = None,
           source: str = "geometry") -> ValidationIssue:
    rooms = rooms or []
    return ValidationIssue(
        severity=severity, code=code, message=message, source=source,
        floor_id=floor.id if floor else None, floor_name=floor.name if floor else None,
        room_id=rooms[0].id if rooms else None, room_ids=[r.id for r in rooms],
        suggested_fix=fix)


def _is_external(op: Opening) -> bool:
    return op.connects_to == "exterior"


def _door_like(op: Opening) -> bool:
    return op.kind in ("door", "sliding", "pass_through")


# ---------------------------------------------------------------------------
# rooms
# ---------------------------------------------------------------------------

def check_room_dimensions(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        for room in floor.rooms:
            if room.is_outdoor:
                if room.area < 1.0 and room.type != "garden":
                    issues.append(_issue("info", "outdoor_tiny",
                                         f"{room.name} is only {room.area:,.2f} m\u00b2 -- probably too small "
                                         f"to be useful.", floor=floor, rooms=[room],
                                         fix="Enlarge it or delete it."))
                continue
            s = spec(room.type)
            if s is None:
                continue
            short = min(room.w, room.d)
            if short < s.min_dim - 1e-6:
                issues.append(_issue("error", "room_narrow",
                                     f"{room.name} is only {short:.2f} m across. A "
                                     f"{room.type.replace('_', ' ')} needs at least {s.min_dim:.2f} m to be "
                                     f"usable.", floor=floor, rooms=[room],
                                     fix=f"Widen {room.name} to at least {s.min_dim:.2f} m, or borrow area "
                                         f"from an adjoining room."))
            if room.area < s.min_area - 0.05:
                issues.append(_issue("warning", "room_small",
                                     f"{room.name} is {room.area:,.2f} m\u00b2, below the {s.min_area:,.2f} m\u00b2 "
                                     f"minimum for comfortable use.", floor=floor, rooms=[room],
                                     fix=f"Enlarge {room.name} or move a wall to borrow area from a "
                                         f"neighbour."))
            aspect = max(room.w, room.d) / max(0.01, short)
            if aspect > s.max_aspect + 0.01:
                issues.append(_issue("warning", "room_aspect",
                                     f"{room.name} is {room.w:.2f} m x {room.d:.2f} m -- an aspect ratio of "
                                     f"{aspect:.1f}:1 makes furniture layout awkward.",
                                     floor=floor, rooms=[room],
                                     fix="Make the room squarer: shorten the long side, widen the short one."))
            if room.ceiling_height and not (2.1 <= room.ceiling_height <= 6.0):
                issues.append(_issue("warning", "ceiling_height",
                                     f"{room.name} has an unusual ceiling height of "
                                     f"{room.ceiling_height:.2f} m.", floor=floor, rooms=[room],
                                     fix="Use 2.4-3.6 m unless this is a deliberate double-height space."))
    return issues


def check_overlaps(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        rooms = floor.rooms
        for i, a in enumerate(rooms):
            ra = room_rect(a)
            for b in rooms[i + 1:]:
                rb = room_rect(b)
                ix = max(0.0, min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0]))
                iy = max(0.0, min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1]))
                area = ix * iy
                if area <= OVERLAP_TOL:
                    continue
                # an indoor room overlapping an outdoor feature is a real problem;
                # two different outdoor features overlapping (garden under a
                # balcony, say) is normal and not worth an error
                if a.is_outdoor != b.is_outdoor:
                    pass
                elif a.is_outdoor and b.is_outdoor and a.type != b.type:
                    continue
                sev = "error" if area > 0.5 else "warning"
                issues.append(_issue(sev, "room_overlap",
                                     f"{a.name} overlaps {b.name} by {area:,.2f} m\u00b2 on {floor.name}.",
                                     floor=floor, rooms=[a, b],
                                     fix="Move or resize one of the rooms so they no longer intersect."))
    return issues


def check_containment(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    plot = data.plot
    poly = [(p.x, p.y) for p in plot.boundary] if len(plot.boundary) >= 3 else None
    for floor in data.floors:
        for room in floor.rooms:
            if room.is_outdoor and room.type in ("balcony", "terrace", "garden", "courtyard",
                                                 "porch", "parking"):
                # outdoor features may sit in a setback, but never off the plot
                r = room_rect(room)
                if r[0] < -EDGE_TOL or r[1] < -EDGE_TOL \
                        or r[0] + r[2] > plot.width + EDGE_TOL \
                        or r[1] + r[3] > plot.depth + EDGE_TOL:
                    issues.append(_issue("error", "room_outside_plot",
                                         f"{room.name} extends beyond the plot boundary.",
                                         floor=floor, rooms=[room],
                                         fix="Drag it back inside the plot."))
                continue
            r = room_rect(room)
            if r[0] < -EDGE_TOL or r[1] < -EDGE_TOL \
                    or r[0] + r[2] > plot.width + EDGE_TOL \
                    or r[1] + r[3] > plot.depth + EDGE_TOL:
                issues.append(_issue("error", "room_outside_plot",
                                     f"{room.name} extends beyond the plot boundary on {floor.name}.",
                                     floor=floor, rooms=[room],
                                     fix="Drag the room back inside the plot."))
                continue
            if poly is not None and not _rect_in_polygon(r, poly):
                issues.append(_issue("warning", "room_outside_shape",
                                     f"{room.name} crosses the irregular plot outline on {floor.name}.",
                                     floor=floor, rooms=[room],
                                     fix="Trim the room so it stays inside the plotted boundary."))
    return issues


def _rect_in_polygon(rect: Rect, poly: List[Tuple[float, float]], samples: int = 6) -> bool:
    x, y, w, d = rect
    for i in range(samples + 1):
        for j in range(samples + 1):
            if not _point_in_polygon(x + w * i / samples, y + d * j / samples, poly):
                return False
    return True


def _point_in_polygon(px: float, py: float, poly: List[Tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


def check_setbacks(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    sb = data.plot.setbacks
    for floor in data.floors:
        if floor.is_terrace:
            continue
        breached: List[Room] = []
        sides: Set[str] = set()
        for room in floor.rooms:
            if room.is_outdoor:
                continue
            r = room_rect(room)
            if r[0] < sb.west - EDGE_TOL:
                sides.add(f"west ({sb.west:.2f} m)")
                breached.append(room)
            if r[1] < sb.south - EDGE_TOL:
                sides.add(f"south ({sb.south:.2f} m)")
                breached.append(room)
            if r[0] + r[2] > data.plot.width - sb.east + EDGE_TOL:
                sides.add(f"east ({sb.east:.2f} m)")
                breached.append(room)
            if r[1] + r[3] > data.plot.depth - sb.north + EDGE_TOL:
                sides.add(f"north ({sb.north:.2f} m)")
                breached.append(room)
        if breached:
            issues.append(_issue("warning", "setback_breach",
                                 f"{len(breached)} room(s) on {floor.name} cross the declared setback on the "
                                 f"{', '.join(sorted(sides))} side.", floor=floor, rooms=breached[:8],
                                 fix="Pull the rooms inside the setback line, or correct the setback values "
                                     "if you have confirmed different local requirements.",
                                 source="regulation"))
    return issues


# ---------------------------------------------------------------------------
# openings
# ---------------------------------------------------------------------------

def check_openings(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        rooms = {r.id: r for r in floor.rooms}
        groups: Dict[str, List[Opening]] = {}
        for op in floor.openings:
            room = rooms.get(op.room_id)
            if room is None:
                issues.append(_issue("error", "opening_orphan",
                                     f"A {op.kind} refers to a room that no longer exists.",
                                     floor=floor,
                                     fix="Delete the stray opening or re-attach it to a room."))
                continue
            rect = room_rect(room)
            length = rect[2] if op.edge in ("N", "S") else rect[3]
            if op.width <= 0.1:
                issues.append(_issue("error", "opening_narrow",
                                     f"The {op.kind} in {room.name} is only {op.width * 1000:.0f} mm wide.",
                                     floor=floor, rooms=[room],
                                     fix="Set a sensible width (700 mm minimum for a door)."))
            if op.width > length + EDGE_TOL:
                issues.append(_issue("error", "opening_too_wide",
                                     f"A {op.kind} in {room.name} is {op.width:.2f} m wide but the "
                                     f"{op.edge} wall is only {length:.2f} m long.",
                                     floor=floor, rooms=[room],
                                     fix="Narrow the opening or lengthen the wall."))
            if op.kind == "door" and op.width < MIN_DOOR_WIDTH - 1e-6:
                issues.append(_issue("warning", "door_narrow",
                                     f"A door in {room.name} is {op.width * 1000:.0f} mm wide; 700 mm is the "
                                     f"practical minimum to pass through comfortably.",
                                     floor=floor, rooms=[room],
                                     fix="Widen the door, or mark it as a hatch/cupboard access."))
            if data.requirements.accessibility and _door_like(op) and op.width < 0.85:
                issues.append(_issue("warning", "door_accessibility",
                                     f"A door in {room.name} is {op.width * 1000:.0f} mm wide. For step-free "
                                     f"or wheelchair access, 850-900 mm clear width is recommended.",
                                     floor=floor, rooms=[room],
                                     fix="Widen this door to at least 850 mm.", source="accessibility"))
            if op.kind == "window" and op.sill > 1.5:
                issues.append(_issue("info", "window_high",
                                     f"A window in {room.name} has its sill at {op.sill:.2f} m, higher than "
                                     f"the usual 0.9-1.1 m, so it will not give a useful view out.",
                                     floor=floor, rooms=[room],
                                     fix="Lower the sill to about 0.9 m unless it is a clerestory window."))
            if _door_like(op) and op.height < 1.9:
                issues.append(_issue("warning", "door_low",
                                     f"A door in {room.name} is only {op.height:.2f} m high.",
                                     floor=floor, rooms=[room],
                                     fix="Set the door height to 2.0-2.1 m."))
            groups.setdefault(f"{op.room_id}|{op.edge}", []).append(op)

        for key, ops in groups.items():
            room_id, edge = key.split("|")
            room = rooms.get(room_id)
            if room is None or len(ops) < 2:
                continue
            spans = sorted(((op.clamped_offset(room), op.clamped_offset(room) + op.width, op)
                            for op in ops), key=lambda s: s[0])
            for i in range(len(spans) - 1):
                _, hi_a, op_a = spans[i]
                lo_b, _, op_b = spans[i + 1]
                if hi_a > lo_b + EDGE_TOL:
                    issues.append(_issue("error", "opening_collision",
                                         f"A {op_a.kind} and a {op_b.kind} overlap on the {edge} wall of "
                                         f"{room.name}.", floor=floor, rooms=[room],
                                         fix="Slide one of them along the wall until they no longer overlap."))
    return issues


def check_reachability(data: ProjectData) -> List[ValidationIssue]:
    """Every indoor room must be reachable from the entrance through doors."""
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        rooms = {r.id: r for r in floor.rooms}
        if not rooms:
            continue
        graph: Dict[str, Set[str]] = {rid: set() for rid in rooms}
        external: Set[str] = set()
        for op in floor.openings:
            if not _door_like(op) or op.room_id not in rooms:
                continue
            if _is_external(op):
                external.add(op.room_id)
                continue
            other = op.connects_to
            if other in rooms:
                graph[op.room_id].add(other)
                graph[other].add(op.room_id)
        entrances = {r.id for r in floor.rooms if "entrance" in r.tags or r.type in ("lobby", "porch")}
        # the stair core is an entry point on every level: you arrive from below
        stair_ids = {r.id for r in floor.rooms if r.type == "stairs"} if (
            floor.index > 0 or floor.stairs) else set()
        starts = entrances | external | stair_ids
        if not starts:
            if not floor.is_terrace and floor.index == 0:
                issues.append(_issue("warning", "no_entrance",
                                     f"{floor.name} has no door to the outside and no entrance room.",
                                     floor=floor,
                                     fix="Add a main door on an external wall."))
            continue
        seen: Set[str] = set(starts)
        queue = deque(starts)
        while queue:
            cur = queue.popleft()
            for nxt in graph.get(cur, ()):  # noqa: B007
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        for room in floor.rooms:
            if room.id in seen or room.is_outdoor or room.type == "stairs":
                continue
            if room.type in ("terrace", "balcony", "garden", "courtyard"):
                continue
            issues.append(_issue("error", "room_unreachable",
                                 f"{room.name} on {floor.name} has no door connecting it to the rest of "
                                 f"the floor.", floor=floor, rooms=[room],
                                 fix=f"Add a door from {room.name} into an adjoining room or corridor."))
    return issues


# ---------------------------------------------------------------------------
# stairs, daylight, programme
# ---------------------------------------------------------------------------

def check_stairs(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    levels = [f for f in data.floors if not f.is_terrace]
    if len(levels) > 1:
        cores: List[Tuple[Floor, Optional[Room]]] = []
        for floor in levels:
            stair = next((r for r in floor.rooms if r.type == "stairs" and "stair_core" in r.tags), None)
            if stair is None:
                stair = next((r for r in floor.rooms if r.type == "stairs"), None)
            if stair is None:
                issues.append(_issue("error", "stair_missing",
                                     f"{floor.name} has no staircase, so the level above cannot be "
                                     f"reached.", floor=floor,
                                     fix="Add a staircase on this floor, stacked over the one below."))
            cores.append((floor, stair))
        for (fa, a), (fb, b) in zip(cores, cores[1:]):
            if a is None or b is None:
                continue
            ra, rb = room_rect(a), room_rect(b)
            ix = max(0.0, min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0]))
            iy = max(0.0, min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1]))
            smaller = min(rect_area(ra), rect_area(rb)) or 1.0
            if ix * iy / smaller < 0.5:
                issues.append(_issue("error", "stair_misaligned",
                                     f"The staircase on {fb.name} is not stacked over the one on {fa.name}. "
                                     f"Stairs must line up vertically or the slab opening will not match.",
                                     floor=fb, rooms=[b],
                                     fix=f"Move the {fb.name} stair core over the {fa.name} core."))
    for floor in data.floors:
        for st in floor.stairs:
            if st.rise_m and not (0.14 <= st.rise_m <= 0.20):
                issues.append(_issue("warning", "stair_riser",
                                     f"The stair on {floor.name} has {st.rise_m * 1000:.0f} mm risers. "
                                     f"150-190 mm is the normal comfortable range and many codes require "
                                     f"it.", floor=floor,
                                     fix="Change the number of risers or the floor-to-floor height.",
                                     source="regulation"))
            if st.tread_m and st.tread_m < 0.25:
                issues.append(_issue("warning", "stair_tread",
                                     f"The stair on {floor.name} has {st.tread_m * 1000:.0f} mm treads; "
                                     f"250 mm or more is normally required.", floor=floor,
                                     fix="Lengthen the flight or add a landing.", source="regulation"))
            if st.width_m and st.width_m < 0.85:
                issues.append(_issue("warning", "stair_width",
                                     f"The stair on {floor.name} is {st.width_m:.2f} m wide; 0.9 m is the "
                                     f"usual minimum for a dwelling.", floor=floor,
                                     fix="Widen the stair core.", source="regulation"))
            if st.risers and st.risers > 16 and st.flights < 2:
                issues.append(_issue("info", "stair_flight",
                                     f"The stair on {floor.name} has {st.risers} risers in one flight; "
                                     f"codes usually ask for a landing after 12-16.", floor=floor,
                                     fix="Split the stair into two flights with a landing.",
                                     source="regulation"))
    return issues


def check_daylight(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        if floor.is_terrace:
            continue
        for room in floor.rooms:
            if room.is_outdoor or not is_habitable(room.type):
                continue
            external_ops = [op for op in floor.openings
                            if op.room_id == room.id and _is_external(op)]
            glass = sum(op.width * max(0.3, op.height - op.sill)
                        for op in external_ops if op.kind in ("window", "sliding"))
            if not external_ops:
                issues.append(_issue("warning", "no_daylight",
                                     f"{room.name} on {floor.name} has no opening on an external wall, so "
                                     f"it gets no natural light or ventilation.", floor=floor, rooms=[room],
                                     fix="Move the room to an external wall, add a window, or accept it as "
                                         "an internal service space."))
            elif glass < room.area * 0.08:
                issues.append(_issue("info", "low_daylight",
                                     f"{room.name} has {glass:,.2f} m\u00b2 of glazing for {room.area:,.2f} m\u00b2 "
                                     f"of floor. Around 8-10% of floor area is a common daylight target.",
                                     floor=floor, rooms=[room],
                                     fix="Enlarge the windows or add a second window on another wall."))
            if room.is_wet and not external_ops:
                issues.append(_issue("info", "wet_room_ventilation",
                                     f"{room.name} is a wet room with no external opening; it will need a "
                                     f"mechanical exhaust fan.", floor=floor, rooms=[room],
                                     fix="Add an exhaust duct or an external window."))
    return issues


def check_programme(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    req = data.requirements
    counts: Dict[str, int] = {}
    for floor in data.floors:
        if floor.is_terrace:
            continue
        for room in floor.rooms:
            if room.is_outdoor:
                continue
            counts[room.type] = counts.get(room.type, 0) + 1
    beds = counts.get("bedroom", 0) + counts.get("master_bedroom", 0)
    if beds < req.bedrooms:
        issues.append(_issue("warning", "programme_bedrooms",
                             f"You asked for {req.bedrooms} bedroom(s); this design has {beds}.",
                             fix="Add a floor, enlarge the footprint, or shrink other rooms.",
                             source="programme"))
    baths = counts.get("bathroom", 0) + counts.get("toilet", 0)
    if baths < req.bathrooms:
        issues.append(_issue("warning", "programme_bathrooms",
                             f"You asked for {req.bathrooms} bathroom(s); this design has {baths} "
                             f"(bathrooms plus toilets).",
                             fix="Add a bathroom on an upper floor or enlarge the footprint.",
                             source="programme"))
    if counts.get("kitchen", 0) == 0 and req.kitchen_type != "platform_only":
        issues.append(_issue("error", "programme_kitchen", "The design has no kitchen.",
                             fix="Add a kitchen, or regenerate with a larger footprint.",
                             source="programme"))
    if req.wants_parking != "none":
        parking = [r for f in data.floors for r in f.rooms if r.type == "parking"]
        if not parking:
            issues.append(_issue("warning", "programme_parking",
                                 "Parking was requested but no parking space is shown.",
                                 fix="Increase the front setback or plan a stilt/basement level.",
                                 source="programme"))
        else:
            need = {"two_wheeler": 2.0, "one_car": 12.0, "two_car": 24.0}.get(req.wants_parking, 12.0)
            got = sum(r.area for r in parking)
            if got < need * 0.85:
                issues.append(_issue("warning", "parking_small",
                                     f"Parking totals {got:,.1f} m\u00b2, below the ~{need:,.0f} m\u00b2 typical "
                                     f"for {req.wants_parking.replace('_', ' ')}.",
                                     fix="Enlarge the bay or check your vehicle's dimensions.",
                                     source="programme"))
    for want, kind, label in ((req.wants_study, "study", "study"),
                              (req.wants_pooja, "puja", "prayer room"),
                              (req.wants_utility, "utility", "utility area"),
                              (req.wants_store, "store", "store room"),
                              (req.wants_servant_room, "servant_room", "helper's room"),
                              (req.wants_shop, "shop", "shop / workspace"),
                              (req.wants_balcony, "balcony", "balcony"),
                              (req.wants_terrace, "terrace", "terrace"),
                              (req.wants_garden, "garden", "garden"),
                              (req.wants_courtyard, "courtyard", "courtyard")):
        if not want:
            continue
        found = counts.get(kind, 0) or sum(1 for f in data.floors for r in f.rooms
                                           if r.is_outdoor and r.type == kind)
        if found == 0:
            issues.append(_issue("info", "programme_optional",
                                 f"You asked for a {label} but this design does not include one.",
                                 fix=f"Add a {label} manually or free up area elsewhere.",
                                 source="programme"))
    if req.floors > 1:
        occupied = len([f for f in data.floors if not f.is_terrace])
        if occupied != req.floors:
            issues.append(_issue("info", "floor_count",
                                 f"You asked for {req.floors} floors; the model has {occupied} occupied "
                                 f"levels.", fix="Regenerate with the floor count you want.",
                                 source="programme"))
    return issues


# ---------------------------------------------------------------------------
# furniture, circulation, regulations
# ---------------------------------------------------------------------------

def check_furniture(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        rooms = {r.id: r for r in floor.rooms}
        for item in floor.furniture:
            room = rooms.get(item.room_id)
            if room is None:
                issues.append(_issue("warning", "furniture_orphan",
                                     f"{item.label or item.kind} belongs to a room that no longer exists.",
                                     floor=floor, fix="Delete the item or reassign it to a room."))
                continue
            r = room_rect(room)
            hw, hd = item.w / 2, item.d / 2
            if item.x - hw < r[0] - 0.02 or item.x + hw > r[0] + r[2] + 0.02 \
                    or item.y - hd < r[1] - 0.02 or item.y + hd > r[1] + r[3] + 0.02:
                issues.append(_issue("warning", "furniture_outside",
                                     f"{item.label or item.kind} sticks out of {room.name} on {floor.name}.",
                                     floor=floor, rooms=[room],
                                     fix=f"Move {item.label or item.kind} back inside {room.name}."))
        for op in floor.openings:
            if not _door_like(op):
                continue
            room = rooms.get(op.room_id)
            if room is None:
                continue
            swing = _door_swing_rect(room, op)
            if swing is None:
                continue
            for item in floor.furniture:
                if item.room_id != room.id or item.z >= 0.4:
                    continue
                box = (item.x - item.w / 2, item.y - item.d / 2, item.w, item.d)
                ix = max(0.0, min(swing[0] + swing[2], box[0] + box[2]) - max(swing[0], box[0]))
                iy = max(0.0, min(swing[1] + swing[3], box[1] + box[3]) - max(swing[1], box[1]))
                blocked = ix * iy
                # a shallow overlap is normal in a small bathroom; only a real
                # obstruction is worth telling the user about
                if blocked > 0.15 and item.height > 0.35:
                    issues.append(_issue("warning", "door_blocked",
                                         f"{item.label or item.kind} blocks the swing of a door in "
                                         f"{room.name} on {floor.name}.", floor=floor, rooms=[room],
                                         fix=f"Move {item.label or item.kind} about 0.9 m away from the "
                                             f"doorway."))
    return issues


def _door_swing_rect(room: Room, op: Opening) -> Optional[Rect]:
    r = room_rect(room)
    depth = min(DOOR_SWING_DEPTH, max(0.4, op.width))
    off = op.clamped_offset(room)
    if op.edge == "S":
        return (r[0] + off, r[1], op.width, depth)
    if op.edge == "N":
        return (r[0] + off, r[1] + r[3] - depth, op.width, depth)
    if op.edge == "W":
        return (r[0], r[1] + off, depth, op.width)
    return (r[0] + r[2] - depth, r[1] + off, depth, op.width)


def check_circulation(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    for floor in data.floors:
        if floor.is_terrace:
            continue
        floor_area = sum(r.area for r in floor.rooms if not r.is_outdoor)
        circ = sum(r.area for r in floor.rooms
                   if r.type in ("corridor", "lobby", "stairs") and not r.is_outdoor)
        if floor_area > 1 and circ / floor_area > 0.30:
            issues.append(_issue("info", "circulation_high",
                                 f"{circ / floor_area * 100:.0f}% of {floor.name} is corridors, lobby and "
                                 f"stairs. Above 25-30% you are paying for circulation instead of usable "
                                 f"rooms.", floor=floor,
                                 fix="Group rooms around a central lobby to shorten corridors.",
                                 source="comfort"))
    return issues


def check_area_limits(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    plot = data.plot
    if plot.area <= 0 or not data.floors:
        return issues
    ground = sum(r.area for r in data.floors[0].rooms if not r.is_outdoor)
    built = sum(r.area for f in data.floors for r in f.rooms if not r.is_outdoor)
    coverage = ground / plot.area
    if coverage > 0.75:
        issues.append(_issue("warning", "coverage_high",
                             f"Ground coverage is {coverage * 100:.0f}% of the plot. Many Indian "
                             f"municipalities cap coverage between 60% and 75% depending on plot size and "
                             f"road width -- confirm your local byelaw.", source="regulation",
                             fix="Reduce the ground floor footprint or increase the setbacks."))
    far = built / plot.area
    if far > 3.0:
        issues.append(_issue("warning", "far_high",
                             f"Floor area ratio is about {far:.2f}. Residential zones commonly cap FAR "
                             f"between 1.0 and 3.0; this needs verification with your local authority.",
                             source="regulation", fix="Remove a floor or shrink the footprint."))
    if data.requirements.budget_target and data.budget and data.budget.total_cost:
        if data.budget.total_cost > data.requirements.budget_target * 1.15:
            issues.append(_issue("warning", "budget_exceeded",
                                 f"The estimate of {data.budget.currency} "
                                 f"{data.budget.total_cost:,.0f} is more than 15% above your target of "
                                 f"{data.requirements.budget_target:,.0f}.", source="budget",
                                 fix="Choose an economical finish level, reduce the built-up area, or "
                                     "raise the target."))
    return issues


def check_structure_warnings(data: ProjectData) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    long_rooms = [r for f in data.floors for r in f.rooms
                  if not r.is_outdoor and max(r.w, r.d) > 5.5]
    for room in long_rooms[:6]:
        floor = next((f for f in data.floors if f.id == room.floor_id), None)
        issues.append(_issue("info", "structural_review",
                             f"{room.name} spans {max(room.w, room.d):.2f} m. Wall lengths beyond roughly "
                             f"4.5-5.5 m usually need a column or beam -- a structural engineer must "
                             f"decide that.", floor=floor, rooms=[room], source="structure",
                             fix="Expect a column or beam here. Do not treat this layout as buildable."))
    if data.requirements.floors >= 3:
        issues.append(_issue("info", "structural_review",
                             "Three or more floors need foundation design, column scheduling and possibly "
                             "a framed structure. None of that is designed here.", source="structure"))
    if any(r.type in ("basement",) for f in data.floors for r in f.rooms):
        issues.append(_issue("info", "structural_review",
                             "Basements involve excavation, retaining structures and drainage design "
                             "which are entirely outside this tool.", source="structure"))
    return issues


CHECKS = (
    check_room_dimensions,
    check_overlaps,
    check_containment,
    check_setbacks,
    check_openings,
    check_reachability,
    check_stairs,
    check_daylight,
    check_programme,
    check_furniture,
    check_circulation,
    check_area_limits,
    check_structure_warnings,
)


def validate_project(data: ProjectData, include_structural: bool = True) -> List[ValidationIssue]:
    checks: Iterable = CHECKS if include_structural else tuple(
        c for c in CHECKS if c is not check_structure_warnings)
    issues: List[ValidationIssue] = []
    for check in checks:
        try:
            issues.extend(check(data))
        except Exception as exc:  # one broken check must not hide the others
            issues.append(ValidationIssue(severity="info", code="check_failed",
                                          message=f"The '{check.__name__}' check could not run ({exc})."))
    issues.sort(key=lambda i: (_RANK.get(i.severity, 3), i.code))
    return issues


def validation_result(data: ProjectData, include_structural: bool = True) -> ValidationResult:
    issues = validate_project(data, include_structural)
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return ValidationResult(ok=counts["error"] == 0, issues=issues, counts=counts)
