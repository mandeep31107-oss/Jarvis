"""Structured edit operations.

Every operation is a pure function on the project document: it either succeeds and
returns notes, or fails and explains why in words the user can act on. Nothing here
guesses silently -- if a wall cannot be moved without breaking the plan, the caller
gets that reason back.

Operations are produced by the command parser (rule based, with an optional model
assist) and are always shown as a *preview* before they touch the saved design.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...schemas import (Floor, FurnitureItem, Opening, ProjectData, Room, Requirements,
                        new_id)
from .. import materials as MAT
from ..geometry import (EXTERIOR_THICKNESS, INTERIOR_THICKNESS, adjacency, derive_walls,
                        room_rect)
from ..layout.furniture import furnish_room
from ..layout.openings import place_openings
from ..layout.partition import GRID, rect_area, rects_overlap, snap_rect
from ..room_catalog import ROOM_SPECS, spec
from ..units import parse_dimension
from ..validation import validate_project

Rect = Tuple[float, float, float, float]

STRUCTURAL_OPS = {"delete_room", "add_room", "split_room", "resize_room", "swap_rooms",
                  "regenerate", "change_requirements", "adjust_setback", "move_room"}

Op = Dict[str, Any]
OpResult = Tuple[bool, List[str]]


# ---------------------------------------------------------------------------
# lookup helpers
# ---------------------------------------------------------------------------

def find_room(data: ProjectData, room_id: Optional[str] = None,
              name: Optional[str] = None) -> Tuple[Optional[Floor], Optional[Room]]:
    if room_id:
        for floor in data.floors:
            room = floor.room(room_id)
            if room is not None:
                return floor, room
    if name:
        target = name.strip().lower()
        best: Tuple[int, Optional[Floor], Optional[Room]] = (99, None, None)
        for floor in data.floors:
            for room in floor.rooms:
                for candidate in {room.name.lower(), room.type.replace("_", " ")}:
                    if candidate == target:
                        return floor, room
                    if target in candidate or candidate in target:
                        score = abs(len(candidate) - len(target))
                        if score < best[0]:
                            best = (score, floor, room)
        if best[1] is not None:
            return best[1], best[2]
    return None, None


def find_opening(data: ProjectData, opening_id: str) -> Tuple[Optional[Floor], Optional[Opening],
                                                              Optional[Room]]:
    for floor in data.floors:
        for op in floor.openings:
            if op.id == opening_id:
                return floor, op, floor.room(op.room_id)
    return None, None, None


def find_furniture(data: ProjectData, item_id: str) -> Tuple[Optional[Floor], Optional[FurnitureItem],
                                                             Optional[Room]]:
    for floor in data.floors:
        for item in floor.furniture:
            if item.id == item_id:
                return floor, item, floor.room(item.room_id)
    return None, None, None


def free_rects(floor: Floor, grid: float = 0.1) -> List[Rect]:
    """Empty rectangles on a floor, found by rasterising what is already built."""
    rooms = [r for r in floor.rooms]
    if not rooms:
        return []
    x0 = min(r.x for r in rooms)
    y0 = min(r.y for r in rooms)
    x1 = max(r.x + r.w for r in rooms)
    y1 = max(r.y + r.d for r in rooms)
    cols = max(1, int(round((x1 - x0) / grid)))
    rows = max(1, int(round((y1 - y0) / grid)))
    mask = [[True] * cols for _ in range(rows)]
    for room in rooms:
        c0 = max(0, int(round((room.x - x0) / grid)))
        c1 = min(cols, int(round((room.x + room.w - x0) / grid)))
        r0 = max(0, int(round((room.y - y0) / grid)))
        r1 = min(rows, int(round((room.y + room.d - y0) / grid)))
        for r in range(r0, r1):
            for c in range(c0, c1):
                mask[r][c] = False
    from ..layout.site import largest_rect
    out: List[Rect] = []
    for _ in range(8):
        area, r0, c0, r1, c1 = largest_rect(mask, rows, cols)
        if area <= 0:
            break
        rect = (round(x0 + c0 * grid, 3), round(y0 + r0 * grid, 3),
                round((c1 - c0 + 1) * grid, 3), round((r1 - r0 + 1) * grid, 3))
        out.append(rect)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                mask[r][c] = False
    return [r for r in out if rect_area(r) >= 1.5]


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def op_rename_room(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find a room called '{params.get('room')}'."]
    name = str(params.get("name") or "").strip()
    if not name:
        return False, ["No new name was given."]
    old = room.name
    room.name = name[:80]
    return True, [f"Renamed '{old}' to '{room.name}'."]


def op_resize_room(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    """Resize a room, propagating the wall movement into an adjoining room.

    Tries the requested wall first, then the other three. A move is only accepted
    when *both* rooms stay usable and nothing overlaps -- otherwise the user gets
    the reason and the largest change that is actually available.
    """
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    rect = room_rect(room)
    requested = _apply_size(rect, params)
    if requested is None:
        return False, ["Could not work out the new size from that request."]

    explicit_edge = params.get("edge")
    candidates: List[Tuple[Optional[str], Rect]] = []
    if explicit_edge:
        candidates.append((explicit_edge, requested))
        candidates.extend((e, r) for e, r in _edge_options(rect, requested) if e != explicit_edge)
    else:
        candidates.extend(_edge_options(rect, requested))
        candidates.append((_dominant_edge(rect, requested), requested))

    best_partial: List[str] = []
    for edge, target in candidates:
        if edge is None:
            continue
        shift = _edge_shift(rect, target, edge)
        if abs(shift) < 0.02:
            continue
        donor = _neighbour_on_edge(floor, room, edge, target)
        if donor is None and shift < 0:
            best_partial.append(f"shrinking on the {edge} side (the freed strip would be left "
                                f"as a gap because no single neighbour spans that wall)")
            continue
        if donor is not None:
            moved_a, moved_b = _shift_pair(rect, room_rect(donor), edge, shift)
            if moved_a is None or moved_b is None:
                continue
            problems = _rect_problems(room, moved_a) + _rect_problems(donor, moved_b)
            if problems:
                best_partial.append(f"moving the wall with {donor.name} on the {edge} side "
                                    f"({problems[0]})")
                continue
            # the neighbour that receives the space may itself be bordered by a third
            # room, so both moved rectangles are re-checked against the whole floor
            hit = _first_conflict(floor, room, moved_a, exclude=(donor.id,)) or \
                _first_conflict(floor, donor, moved_b, exclude=(room.id,))
            if hit is not None:
                best_partial.append(f"moving the wall with {donor.name} on the {edge} side "
                                    f"(that would push into {hit.name})")
                continue
            _set_rect(room, moved_a)
            _set_rect(donor, moved_b)
            _refresh_openings(data, floor, [room, donor])
            return True, [
                f"{room.name} resized to {room.w:.2f} m x {room.d:.2f} m ({room.area:,.2f} m\u00b2). "
                f"The shared wall with {donor.name} moved by {abs(shift):.2f} m, so {donor.name} is "
                f"now {donor.w:.2f} m x {donor.d:.2f} m ({donor.area:,.2f} m\u00b2)."]
        if _first_conflict(floor, room, target) is not None or not _inside_plot(data, target):
            best_partial.append(f"growing on the {edge} side (something else is there)")
            continue
        problems = _rect_problems(room, target)
        if problems:
            best_partial.append(f"the {edge} side ({problems[0]})")
            continue
        delta = rect_area(target) - rect_area(rect)
        _set_rect(room, target)
        _refresh_openings(data, floor, [room])
        notes = [f"{room.name} resized to {room.w:.2f} m x {room.d:.2f} m ({room.area:,.2f} m\u00b2, "
                 f"{'+' if delta >= 0 else ''}{delta:,.2f} m\u00b2). The space is open floor now -- "
                 f"drag a neighbouring wall to claim it, or add a room there."]
        return True, notes

    # nothing worked: explain, and say what *is* possible
    best, best_donor, best_edge = 0.0, None, ""
    for edge in ("E", "W", "N", "S"):
        amount, donor = _max_shift(floor, room, edge)
        if donor is not None and amount > best:
            best, best_donor, best_edge = amount, donor, edge
    tried = list(dict.fromkeys(best_partial))
    reason = "; ".join(tried[:2]) + (f"; {len(tried) - 2} other wall(s) also blocked"
                                     if len(tried) > 2 else "") if tried else \
        "no wall can move that far"
    if best_donor is not None and best > 0.05:
        span = room.d if best_edge in ("E", "W") else room.w
        return False, [
            f"{room.name} cannot be resized that much ({reason}). The largest single wall move "
            f"available right now is {best:.2f} m with {best_donor.name} on the {best_edge} side, "
            f"worth about {best * span:,.2f} m\u00b2. Try a smaller change, move or resize a "
            f"neighbour first, or delete a room to free up space."]
    return False, [
        f"{room.name} cannot be resized that way: {reason}. Its neighbours do not run the full "
        f"length of any wall that could move, so there is no clean way to give it more space. "
        f"Move or resize a neighbour first, or delete a room to free up space."]


def _rect_problems(room: Room, rect: Rect) -> List[str]:
    """Why a rectangle would not be an acceptable size for this room type."""
    s = spec(room.type)
    if s is None:
        return []
    if min(rect[2], rect[3]) < s.min_dim - 1e-6:
        return [f"it would be only {min(rect[2], rect[3]):.2f} m across, below the "
                f"{s.min_dim:.2f} m minimum for a {room.type.replace('_', ' ')}"]
    if rect_area(rect) < s.min_area - 0.05:
        return [f"it would be {rect_area(rect):,.2f} m\u00b2, below the {s.min_area:,.1f} m\u00b2 "
                f"minimum for a {room.type.replace('_', ' ')}"]
    if max(rect[2], rect[3]) / max(0.01, min(rect[2], rect[3])) > s.max_aspect + 0.5:
        return [f"it would be {rect[2]:.2f} m x {rect[3]:.2f} m, too narrow a shape to furnish"]
    return []


def _apply_size(rect: Rect, params: Dict[str, Any]) -> Optional[Rect]:
    x, y, w, d = rect
    new_w = params.get("width")
    new_d = params.get("depth")
    if isinstance(new_w, str):
        new_w = parse_dimension(new_w)
    if isinstance(new_d, str):
        new_d = parse_dimension(new_d)
    dw = params.get("delta_width") or 0.0
    dd = params.get("delta_depth") or 0.0
    area = params.get("area")
    if new_w is None and new_d is None and not dw and not dd and area is None:
        factor = params.get("scale")
        if not factor:
            return None
        k = math.sqrt(max(0.25, float(factor)))   # 'scale' is an area scale
        return snap_rect((x, y, w * k, d * k))
    if area:
        # keep the aspect ratio, hit the requested area
        ratio = max(0.2, w / max(0.01, d))
        new_d = math.sqrt(max(1.0, float(area)) / ratio)
        new_w = new_d * ratio
    w = float(new_w if new_w is not None else w + dw)
    d = float(new_d if new_d is not None else d + dd)
    if w <= 0.3 or d <= 0.3:
        return None
    anchor = params.get("anchor", "sw")
    if anchor == "center":
        x = x + (rect[2] - w) / 2
        y = y + (rect[3] - d) / 2
    elif anchor == "ne":
        x = rect[0] + rect[2] - w
        y = rect[1] + rect[3] - d
    return snap_rect((x, y, round(w, 3), round(d, 3)))


def _no_third_party(floor: Floor, room: Room, rect: Rect) -> bool:
    """The grown rectangle must not touch anything that is not the donor."""
    for other in floor.rooms:
        if other.id == room.id:
            continue
        if rects_overlap(rect, room_rect(other), -0.02):
            return False
    return True


def _donor_ok(donor: Room, rect: Rect) -> bool:
    s = spec(donor.type)
    if s is None:
        return min(rect[2], rect[3]) >= 0.6
    return (min(rect[2], rect[3]) >= s.min_dim - 1e-6
            and rect_area(rect) >= s.min_area * 0.9 - 1e-6)


def _edge_options(rect: Rect, new_rect: Rect) -> List[Tuple[str, Rect]]:
    """Ways to reach the requested size, one per wall that could move."""
    x, y, w, d = rect
    area = rect_area(new_rect)
    options: List[Tuple[str, Rect]] = []
    for edge in ("E", "W", "N", "S"):
        if edge in ("E", "W"):
            new_w = max(0.6, area / max(0.6, d))
            if new_w > w:
                candidate = ((x, y, new_w, d) if edge == "E" else (x + w - new_w, y, new_w, d))
            else:
                continue
        else:
            new_d = max(0.6, area / max(0.6, w))
            if new_d > d:
                candidate = ((x, y, w, new_d) if edge == "N" else (x, y + d - new_d, w, new_d))
            else:
                continue
        options.append((edge, snap_rect(candidate)))
    if not options:      # shrinking: any wall works, prefer the longest
        for edge in ("E", "N", "W", "S"):
            if edge in ("E", "W"):
                new_w = max(0.6, area / max(0.6, d))
                options.append((edge, snap_rect((x, y, new_w, d) if edge == "E"
                                                else (x + w - new_w, y, new_w, d))))
            else:
                new_d = max(0.6, area / max(0.6, w))
                options.append((edge, snap_rect((x, y, w, new_d) if edge == "N"
                                                else (x, y + d - new_d, w, new_d))))
    return options


def _dominant_edge(rect: Rect, new_rect: Rect) -> Optional[str]:
    dx0 = new_rect[0] - rect[0]
    dx1 = (new_rect[0] + new_rect[2]) - (rect[0] + rect[2])
    dy0 = new_rect[1] - rect[1]
    dy1 = (new_rect[1] + new_rect[3]) - (rect[1] + rect[3])
    best = max((("W", -dx0), ("E", dx1), ("S", -dy0), ("N", dy1)), key=lambda t: abs(t[1]))
    return best[0] if abs(best[1]) > 1e-6 else None


def _edge_shift(rect: Rect, new_rect: Rect, edge: Optional[str]) -> float:
    if edge == "E":
        return (new_rect[0] + new_rect[2]) - (rect[0] + rect[2])
    if edge == "W":
        return rect[0] - new_rect[0]
    if edge == "N":
        return (new_rect[1] + new_rect[3]) - (rect[1] + rect[3])
    if edge == "S":
        return rect[1] - new_rect[1]
    return 0.0


def _neighbour_on_edge(floor: Floor, room: Room, edge: str,
                       target: Optional[Rect] = None, coverage: float = 0.95) -> Optional[Room]:
    """Neighbour that can absorb a wall movement along ``edge``.

    The neighbour must span (almost) the whole edge *after* the move, otherwise the
    room would slide past it into whatever is behind -- the classic way a resize
    silently creates an overlap.
    """
    rect = room_rect(room)
    span = (target[3] if edge in ("E", "W") else target[2]) if target else \
           (rect[3] if edge in ("E", "W") else rect[2])
    lo = (target[1] if target else rect[1]) if edge in ("E", "W") else \
         (target[0] if target else rect[0])
    hi = lo + span
    best: Optional[Tuple[float, Room]] = None
    for other in floor.rooms:
        if other.id == room.id or other.is_outdoor:
            continue
        o = room_rect(other)
        if edge == "E":
            if abs(o[0] - (rect[0] + rect[2])) > 0.12:
                continue
            o_lo, o_hi = o[1], o[1] + o[3]
        elif edge == "W":
            if abs((o[0] + o[2]) - rect[0]) > 0.12:
                continue
            o_lo, o_hi = o[1], o[1] + o[3]
        elif edge == "N":
            if abs(o[1] - (rect[1] + rect[3])) > 0.12:
                continue
            o_lo, o_hi = o[0], o[0] + o[2]
        elif edge == "S":
            if abs((o[1] + o[3]) - rect[1]) > 0.12:
                continue
            o_lo, o_hi = o[0], o[0] + o[2]
        else:
            continue
        overlap = min(hi, o_hi) - max(lo, o_lo)
        if overlap >= span * coverage and (best is None or overlap > best[0]):
            best = (overlap, other)
    return best[1] if best else None


def _shift_pair(a: Rect, b: Rect, edge: str, shift: float) -> Tuple[Optional[Rect], Optional[Rect]]:
    """Move the wall ``a`` shares with ``b`` on ``edge``.

    ``shift`` is positive when ``a`` grows (``b`` shrinks by the same amount), so the
    two rectangles keep tiling the floor exactly.
    """
    if edge == "E":
        if b[2] - shift < 0.6:
            return None, None
        return snap_rect((a[0], a[1], a[2] + shift, a[3])), \
            snap_rect((b[0] + shift, b[1], b[2] - shift, b[3]))
    if edge == "W":
        if b[2] - shift < 0.6 or a[0] - shift < -0.01:
            return None, None
        return snap_rect((a[0] - shift, a[1], a[2] + shift, a[3])), \
            snap_rect((b[0], b[1], b[2] - shift, b[3]))
    if edge == "N":
        if b[3] - shift < 0.6:
            return None, None
        return snap_rect((a[0], a[1], a[2], a[3] + shift)), \
            snap_rect((b[0], b[1] + shift, b[2], b[3] - shift))
    if edge == "S":
        if b[3] - shift < 0.6 or a[1] - shift < -0.01:
            return None, None
        return snap_rect((a[0], a[1] - shift, a[2], a[3] + shift)), \
            snap_rect((b[0], b[1], b[2], b[3] - shift))
    return None, None


def _max_shift(floor: Floor, room: Room, edge: str) -> Tuple[float, Optional[Room]]:
    """How far the wall with the best donor on ``edge`` can move, in metres."""
    donor = _neighbour_on_edge(floor, room, edge)
    if donor is None:
        return 0.0, None
    rect = room_rect(donor)
    span = rect[2] if edge in ("E", "W") else rect[3]
    s = spec(donor.type)
    min_span = s.min_dim if s else 0.9
    min_area = s.min_area if s else 4.0
    other_dim = rect[3] if edge in ("E", "W") else rect[2]
    area_limit = max(0.0, span - (min_area / max(0.6, other_dim)))
    dim_limit = max(0.0, span - min_span)
    return max(0.0, min(area_limit, dim_limit)), donor


def _set_rect(room: Room, rect: Rect) -> None:
    room.x, room.y, room.w, room.d = (round(rect[0], 3), round(rect[1], 3),
                                      max(0.1, round(rect[2], 3)), max(0.1, round(rect[3], 3)))


def _first_conflict(floor: Floor, room: Room, rect: Rect,
                    exclude: Tuple[str, ...] = ()) -> Optional[Room]:
    for other in floor.rooms:
        if other.id == room.id or other.id in exclude:
            continue
        if rects_overlap(rect, room_rect(other), 0.02):
            return other
    return None


def _inside_plot(data: ProjectData, rect: Rect, allow_outdoor: bool = False) -> bool:
    plot = data.plot
    tol = 0.02
    if rect[0] < -tol or rect[1] < -tol:
        return False
    if rect[0] + rect[2] > plot.width + tol or rect[1] + rect[3] > plot.depth + tol:
        return False
    return True


def _refresh_openings(data: ProjectData, floor: Floor, rooms: List[Room]) -> None:
    """Re-clamp openings and re-place furniture for the rooms that changed."""
    for room in rooms:
        kept: List[Opening] = []
        for op in floor.openings:
            if op.room_id != room.id:
                kept.append(op)
                continue
            length = room.w if op.edge in ("N", "S") else room.d
            if op.width > length - 0.2:
                continue  # the wall is now too short for this opening
            op.offset = op.clamped_offset(room)
            kept.append(op)
        floor.openings = kept
        floor.furniture = [f for f in floor.furniture if f.room_id != room.id]
        try:
            furnish_room(room, floor, data.requirements)
        except Exception:
            pass


def op_move_room(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    rect = room_rect(room)
    dx = float(params.get("dx", 0) or 0)
    dy = float(params.get("dy", 0) or 0)
    direction = params.get("direction")
    distance = float(params.get("distance", 1.0) or 1.0)
    if direction and not (dx or dy):
        dx, dy = {"E": (distance, 0), "W": (-distance, 0), "N": (0, distance),
                  "S": (0, -distance)}.get(str(direction).upper(), (0, 0))
    if params.get("x") is not None and params.get("y") is not None:
        new_rect = snap_rect((float(params["x"]), float(params["y"]), rect[2], rect[3]))
    else:
        new_rect = snap_rect((rect[0] + dx, rect[1] + dy, rect[2], rect[3]))
    if abs(new_rect[0] - rect[0]) < 0.02 and abs(new_rect[1] - rect[1]) < 0.02:
        return False, ["That move is smaller than the 50 mm grid, so nothing would change."]
    if not _inside_plot(data, new_rect):
        return False, [f"{room.name} would move outside the plot boundary."]
    conflict = _first_conflict(floor, room, new_rect)
    if conflict is not None:
        return False, [f"{room.name} cannot move there: {conflict.name} is in the way. Move or "
                       f"delete {conflict.name} first, or swap the two rooms."]
    _set_rect(room, new_rect)
    _refresh_openings(data, floor, [room])
    return True, [f"Moved {room.name} by ({new_rect[0] - rect[0]:+.2f} m east, "
                  f"{new_rect[1] - rect[1]:+.2f} m north)."]


def op_delete_room(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    if "stair_core" in room.tags and len([f for f in data.floors if not f.is_terrace]) > 1:
        return False, [f"{room.name} is the stair core. Deleting it would disconnect the floors. "
                       f"Delete a floor instead, or move the stair."]
    give_to = params.get("give_space_to")
    floor.rooms = [r for r in floor.rooms if r.id != room.id]
    floor.openings = [o for o in floor.openings if o.room_id != room.id]
    floor.furniture = [f for f in floor.furniture if f.room_id != room.id]
    floor.stairs = [s for s in floor.stairs if s.room_id != room.id]
    for other in floor.rooms:
        other.tags = [t for t in other.tags if not t.endswith(room.id)]
    notes = [f"Deleted {room.name} ({room.area:,.2f} m\u00b2) from {floor.name}. Its openings and "
             f"furniture were removed too."]
    if give_to:
        _, target = find_room(data, None, give_to)
        if target is not None and target.floor_id == floor.id:
            grown = _grow_into_void(floor, target, room_rect(room))
            if grown:
                notes.append(f"The space was given to {target.name}, now {target.area:,.2f} m\u00b2.")
                _refresh_openings(data, floor, [target])
            else:
                notes.append(f"The space could not be added to {target.name} cleanly (the wall is "
                             f"not shared along its full length), so it is left as open floor.")
        else:
            notes.append("The space is left as open floor; drag a neighbouring wall to claim it.")
    else:
        notes.append("The space is now open floor. Resize a neighbouring room to claim it.")
    notes.append("Deleting a room changes the structure of the plan -- a professional must confirm "
                 "which walls were load bearing before anything is built.")
    return True, notes


def _grow_into_void(floor: Floor, room: Room, void: Rect) -> bool:
    rect = room_rect(room)
    for edge, ok in (("E", abs(void[0] - (rect[0] + rect[2])) < 0.12),
                     ("W", abs((void[0] + void[2]) - rect[0]) < 0.12),
                     ("N", abs(void[1] - (rect[1] + rect[3])) < 0.12),
                     ("S", abs((void[1] + void[3]) - rect[1]) < 0.12)):
        if not ok:
            continue
        if edge in ("E", "W"):
            overlap = min(rect[1] + rect[3], void[1] + void[3]) - max(rect[1], void[1])
            if overlap < min(rect[3], void[3]) * 0.8:
                continue
            if edge == "E":
                _set_rect(room, (rect[0], rect[1], rect[2] + void[2], rect[3]))
            else:
                _set_rect(room, (void[0], rect[1], rect[2] + void[2], rect[3]))
            return True
        overlap = min(rect[0] + rect[2], void[0] + void[2]) - max(rect[0], void[0])
        if overlap < min(rect[2], void[2]) * 0.8:
            continue
        if edge == "N":
            _set_rect(room, (rect[0], rect[1], rect[2], rect[3] + void[3]))
        else:
            _set_rect(room, (rect[0], void[1], rect[2], rect[3] + void[3]))
        return True
    return False


def op_add_room(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    room_type = str(params.get("type") or params.get("room_type") or "").strip()
    if room_type not in ROOM_SPECS:
        close = [k for k in ROOM_SPECS if room_type and (room_type in k or k in room_type)]
        return False, [f"'{room_type}' is not a room type I know. Try one of: "
                       + ", ".join(sorted(ROOM_SPECS)[:14]) + ("..." if len(ROOM_SPECS) > 14 else "")
                       + (f" Did you mean '{close[0]}'?" if close else "")]
    floor = _floor_for(data, params.get("floor_id"), params.get("floor"))
    if floor is None:
        return False, ["Could not find the floor to add the room to."]
    s = spec(room_type)
    near_name = params.get("near") or params.get("next_to")
    _, near = find_room(data, params.get("near_room_id"), near_name)
    if near is not None and near.floor_id != floor.id:
        floor = next((f for f in data.floors if f.id == near.floor_id), floor)
    area = float(params.get("area") or (s.comfort_area if s else 8.0))
    width = params.get("width")
    depth = params.get("depth")
    if isinstance(width, str):
        width = parse_dimension(width)
    if isinstance(depth, str):
        depth = parse_dimension(depth)
    candidates: List[Rect] = []
    if width and depth:
        candidates.append((0, 0, float(width), float(depth)))
    for target in (area, s.min_area if s else 4.0):
        if not target:
            continue
        for ratio in (1.3, 1.0, 0.75):
            w = math.sqrt(max(2.0, float(target)) * ratio)
            d = max(2.0, float(target)) / w
            candidates.append((0, 0, round(w, 2), round(d, 2)))
            candidates.append((0, 0, round(d, 2), round(w, 2)))
    placements: List[Rect] = []
    if near is not None:
        placements.extend(_around(room_rect(near), candidates))
    placements.extend(_in_free_space(floor, candidates))
    placements.extend(_along_walls(floor, candidates))
    for rect in placements:
        rect = snap_rect(rect)
        if rect_area(rect) < 1.5 or not _inside_plot(data, rect):
            continue
        probe = Room(id="probe", floor_id=floor.id, name="probe", type=room_type,
                     x=rect[0], y=rect[1], w=max(0.4, rect[2]), d=max(0.4, rect[3]))
        if _first_conflict(floor, probe, rect) is not None:
            continue
        if s is not None and min(rect[2], rect[3]) < s.min_dim - 1e-6:
            continue
        new_room = Room(id=new_id("rm"), floor_id=floor.id,
                        name=params.get("name") or (s.label if s else room_type.replace("_", " ").title()),
                        type=room_type, x=rect[0], y=rect[1], w=rect[2], d=rect[3],
                        ceiling_height=floor.height - floor.slab_thickness,
                        is_outdoor=bool(s and s.outdoor), is_wet=bool(s and s.wet),
                        tags=["user_added"])
        floor.rooms.append(new_room)
        if near is not None:
            new_room.tags.append(f"attached_to:{near.id}")
        _refresh_openings(data, floor, [new_room])
        try:
            place_openings([floor], data.plot, None, data.requirements)
        except Exception:
            pass
        notes = [f"Added {new_room.name} ({new_room.w:.2f} m x {new_room.d:.2f} m = "
                 f"{new_room.area:,.2f} m\u00b2) on {floor.name}"
                 + (f" next to {near.name}." if near is not None else ".")]
        notes.append("Doors and windows were placed automatically; adjust them in the editor. A "
                     "new room changes the wall layout, so treat it as a structural change until "
                     "an engineer confirms which walls carry load.")
        return True, notes
    return False, [f"No space for a {room_type.replace('_', ' ')} of about {float(area):,.1f} m\u00b2 "
                   f"on {floor.name}"
                   + (f" next to {near.name}" if near is not None else "")
                   + ". Try a smaller size, another floor, or delete/resize an existing room first."]


def _floor_for(data: ProjectData, floor_id: Optional[str],
               name: Optional[str]) -> Optional[Floor]:
    if floor_id:
        return data.floor(floor_id)
    if name:
        key = str(name).lower()
        for floor in data.floors:
            if key in floor.name.lower():
                return floor
        if "ground" in key or "0" == key:
            return data.floors[0] if data.floors else None
        if "terrace" in key:
            return next((f for f in data.floors if f.is_terrace), None)
    return data.floors[0] if data.floors else None


def _around(anchor: Rect, sizes: List[Rect]) -> List[Rect]:
    out: List[Rect] = []
    ax, ay, aw, ad = anchor
    for _, _, w, d in sizes:
        out.append((ax, ay + ad, w, d))          # north
        out.append((ax, ay - d, w, d))           # south
        out.append((ax + aw, ay, w, d))          # east
        out.append((ax - w, ay, w, d))           # west
        out.append((ax + aw - w, ay + ad, w, d))
        out.append((ax, ay + ad - d, w, d))
    return out


def _along_walls(floor: Floor, sizes: List[Rect], step: float = 0.25) -> List[Rect]:
    """Slide candidate sizes along the floor's outer edge to find a gap."""
    rooms = [r for r in floor.rooms if not r.is_outdoor]
    if not rooms:
        return []
    x0 = min(r.x for r in rooms)
    y0 = min(r.y for r in rooms)
    x1 = max(r.x + r.w for r in rooms)
    y1 = max(r.y + r.d for r in rooms)
    out: List[Rect] = []
    for _, _, w, d in sizes:
        x = x0
        while x + w <= x1 + 1e-6:
            out.append((round(x, 3), round(y0, 3), w, d))
            out.append((round(x, 3), round(y1 - d, 3), w, d))
            x += step
        y = y0
        while y + d <= y1 + 1e-6:
            out.append((round(x0, 3), round(y, 3), w, d))
            out.append((round(x1 - w, 3), round(y, 3), w, d))
            y += step
    return out


def _in_free_space(floor: Floor, sizes: List[Rect]) -> List[Rect]:
    out: List[Rect] = []
    for rect in free_rects(floor):
        for _, _, w, d in sizes:
            if w <= rect[2] + 1e-6 and d <= rect[3] + 1e-6:
                out.append((rect[0], rect[1], min(w, rect[2]), min(d, rect[3])))
    return out


def op_swap_rooms(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor_a, a = find_room(data, params.get("room_id"), params.get("room"))
    floor_b, b = find_room(data, params.get("other_room_id"), params.get("other_room"))
    if a is None or b is None:
        return False, ["I need two existing rooms to swap."]
    if floor_a is not floor_b:
        return False, ["Rooms on different floors cannot be swapped directly. Move one of them to "
                       "the other floor first."]
    ra, rb = room_rect(a), room_rect(b)
    a.name, b.name = b.name, a.name
    a.type, b.type = b.type, a.type
    a.is_wet, b.is_wet = b.is_wet, a.is_wet
    _set_rect(a, rb)
    _set_rect(b, ra)
    for op in floor_a.openings:
        if op.room_id == a.id:
            op.room_id = b.id
        elif op.room_id == b.id:
            op.room_id = a.id
    for item in floor_a.furniture:
        if item.room_id == a.id:
            item.room_id = b.id
        elif item.room_id == b.id:
            item.room_id = a.id
    _refresh_openings(data, floor_a, [a, b])
    return True, [f"Swapped {a.name} and {b.name} (positions and sizes exchanged). Doors, windows "
                  f"and furniture were re-placed for the new geometry."]


def op_split_room(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    new_type = str(params.get("new_type") or "store")
    if new_type not in ROOM_SPECS:
        return False, [f"'{new_type}' is not a known room type."]
    s = spec(new_type)
    explicit_fraction = params.get("fraction")
    fractions = [max(0.15, min(0.6, float(explicit_fraction)))] if explicit_fraction else \
        [0.35, 0.5, 0.28, 0.6]
    axes = [params["axis"]] if params.get("axis") else \
        (["x", "y"] if room.w >= room.d else ["y", "x"])
    rect = room_rect(room)
    keep = new_rect = None
    best_attempt = ""
    for axis in axes:
        for fraction in fractions:
            if axis == "x":
                cut = max(0.9, rect[2] * fraction)
                trial_keep = (rect[0], rect[1], rect[2] - cut, rect[3])
                trial_new = (rect[0] + rect[2] - cut, rect[1], cut, rect[3])
            else:
                cut = max(0.9, rect[3] * fraction)
                trial_keep = (rect[0], rect[1], rect[2], rect[3] - cut)
                trial_new = (rect[0], rect[1] + rect[3] - cut, rect[2], cut)
            best_attempt = (f"{min(trial_new[2], trial_new[3]):.2f} m across on the {axis} axis "
                            f"with a {fraction:.0%} share")
            if s is None or min(trial_new[2], trial_new[3]) >= s.min_dim:
                if s is None or rect_area(trial_keep) >= (spec(room.type).min_area * 0.8
                                                          if spec(room.type) else 4.0):
                    keep, new_rect = trial_keep, trial_new
                    break
        if keep is not None:
            break
    if keep is None or new_rect is None:
        own = spec(room.type)
        return False, [f"{room.name} ({room.area:,.2f} m\u00b2) cannot be split into itself plus a "
                       f"{new_type.replace('_', ' ')}: both parts must stay usable. The new room "
                       f"needs at least {s.min_dim if s else 1.0:.2f} m across and "
                       f"{s.min_area if s else 4.0:,.1f} m\u00b2, and {room.name} must keep about "
                       f"{own.min_area * 0.8 if own else 8.0:,.1f} m\u00b2. Best attempt was "
                       f"{best_attempt}. Enlarge {room.name} first, or take space from a "
                       f"neighbouring room instead."]
    _set_rect(room, snap_rect(keep))
    new_room = Room(id=new_id("rm"), floor_id=floor.id,
                    name=params.get("name") or (s.label if s else new_type.replace("_", " ").title()),
                    type=new_type, x=snap_rect(new_rect)[0], y=snap_rect(new_rect)[1],
                    w=snap_rect(new_rect)[2], d=snap_rect(new_rect)[3],
                    ceiling_height=floor.height - floor.slab_thickness,
                    is_wet=bool(s and s.wet), is_outdoor=bool(s and s.outdoor),
                    tags=["user_added", f"split_from:{room.id}"])
    floor.rooms.append(new_room)
    _refresh_openings(data, floor, [room, new_room])
    try:
        place_openings([floor], data.plot, None, data.requirements)
    except Exception:
        pass
    return True, [f"Split {room.name}: it is now {room.area:,.2f} m\u00b2 and the new "
                  f"{new_room.name} is {new_room.area:,.2f} m\u00b2. This adds a new internal wall -- "
                  f"a structural change until an engineer confirms it."]


def op_change_material(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    field = params.get("field") or params.get("property")
    value = params.get("value")
    if not field or value is None:
        return False, ["I need a material property and a value, e.g. floor_color = #cbb89a."]
    materials, notes = MAT.apply_material_change(data, str(field), value)
    if notes and notes[0].endswith("is not a material property."):
        candidates = ", ".join(sorted(MAT.MATERIAL_FIELDS))
        return False, [f"'{field}' is not a material property. Available: {candidates}."]
    data.materials = materials
    return True, notes


def op_change_style(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    key = str(params.get("style") or params.get("value") or "")
    if key not in MAT.STYLES:
        return False, [f"Unknown style '{key}'. Available: " + ", ".join(MAT.STYLE_ORDER)]
    materials, notes = MAT.apply_style(data, key)
    data.materials = materials
    data.requirements.style = key
    return True, notes


def op_room_finish(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    updated, notes = MAT.apply_room_finish(data, room, wall_color=params.get("wall_color"),
                                           floor_material=params.get("floor_material"),
                                           floor_color=params.get("floor_color"))
    floor.rooms = [updated if r.id == room.id else r for r in floor.rooms]
    return (True, notes) if notes else (False, ["Nothing to change."])


def op_add_opening(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    kind = str(params.get("kind") or "door")
    if kind not in ("door", "window", "sliding", "pass_through", "vent"):
        kind = "window" if "window" in kind else "door"
    edge = str(params.get("edge") or "S").upper()
    if edge not in ("N", "S", "E", "W"):
        return False, [f"'{edge}' is not a wall. Use N, S, E or W."]
    length = room.w if edge in ("N", "S") else room.d
    width = float(params.get("width") or (0.9 if kind == "door" else 1.2))
    if width > length - 0.3:
        return False, [f"A {width:.2f} m {kind} does not fit on the {edge} wall of {room.name} "
                       f"({length:.2f} m long)."]
    if params.get("offset") is not None:
        offset = float(params["offset"])
    else:
        offset = _free_offset(floor, room, edge, width)
        if offset is None:
            taken = ", ".join(
                f"{o.kind} at {o.clamped_offset(room):.2f}-"
                f"{o.clamped_offset(room) + o.width:.2f} m"
                for o in floor.openings if o.room_id == room.id and o.edge == edge)
            return False, [f"There is no {width:.2f} m gap left on the {edge} wall of {room.name} "
                           f"({length:.2f} m long, already used by: {taken or 'nothing'}). Try a "
                           f"narrower opening or a different wall."]
    opening = Opening(id=new_id("op"), kind=kind, room_id=room.id, floor_id=floor.id, edge=edge,
                      offset=round(min(max(offset, 0.15), max(0.15, length - width - 0.15)), 3),
                      width=round(width, 3),
                      height=float(params.get("height") or (2.1 if kind != "window" else 1.35)),
                      sill=float(params.get("sill") or (0.0 if kind != "window" else 0.95)),
                      connects_to=params.get("connects_to"))
    floor.openings.append(opening)
    return True, [f"Added a {opening.width:.2f} m {kind} on the {edge} wall of {room.name}. "
                  f"Widening an opening in an external wall is a structural change -- a lintel must "
                  f"be designed by an engineer."]


def _free_offset(floor: Floor, room: Room, edge: str, width: float,
                 margin: float = 0.15) -> Optional[float]:
    """First position on a wall where an opening of ``width`` fits between existing ones."""
    length = room.w if edge in ("N", "S") else room.d
    if width > length - 2 * margin:
        return None
    blocked: List[Tuple[float, float]] = []
    for op in floor.openings:
        if op.room_id != room.id or op.edge != edge:
            continue
        start = op.clamped_offset(room)
        blocked.append((start - margin, start + op.width + margin))
    blocked.sort()
    cursor = margin
    for lo, hi in blocked:
        if lo - cursor >= width:
            return cursor
        cursor = max(cursor, hi)
    if length - margin - cursor >= width:
        return cursor
    return None


def op_move_opening(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, opening, room = find_opening(data, str(params.get("opening_id") or ""))
    if opening is None or room is None:
        return False, ["Could not find that door or window."]
    length = room.w if opening.edge in ("N", "S") else room.d
    delta = float(params.get("delta") or 0)
    target = params.get("offset")
    new_offset = float(target) if target is not None else opening.offset + delta
    new_offset = min(max(new_offset, 0.1), max(0.1, length - opening.width - 0.1))
    old = opening.offset
    opening.offset = round(new_offset, 3)
    return True, [f"Moved the {opening.kind} in {room.name} along the {opening.edge} wall from "
                  f"{old:.2f} m to {opening.offset:.2f} m."]


def op_delete_opening(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, opening, room = find_opening(data, str(params.get("opening_id") or ""))
    if floor is None or opening is None:
        return False, ["Could not find that door or window."]
    floor.openings = [o for o in floor.openings if o.id != opening.id]
    return True, [f"Deleted the {opening.kind} from {room.name if room else 'the room'}. If this "
                  f"was the only way in, the room is now unreachable -- the validation panel will "
                  f"flag it."]


def op_add_furniture(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, room = find_room(data, params.get("room_id"), params.get("room"))
    if room is None:
        return False, [f"Could not find the room '{params.get('room')}'."]
    from ..layout.furniture import FURNITURE_SPECS
    kind = str(params.get("kind") or "chair")
    if kind not in FURNITURE_SPECS:
        return False, [f"'{kind}' is not in the furniture catalogue. Examples: "
                       + ", ".join(sorted(FURNITURE_SPECS)[:12]) + ", ..."]
    fw, fd, fh = FURNITURE_SPECS[kind][:3]
    x = params.get("x")
    y = params.get("y")
    if x is None or y is None:
        x = room.x + room.w / 2
        y = room.y + room.d / 2
    item = FurnitureItem(id=new_id("fn"), kind=kind, room_id=room.id, floor_id=floor.id,
                         x=round(float(x), 3), y=round(float(y), 3), w=fw, d=fd, height=fh,
                         label=str(params.get("label") or kind.replace("_", " ").title()),
                         rotation=float(params.get("rotation") or 0))
    floor.furniture.append(item)
    return True, [f"Added {item.label} to {room.name}. It is not checked against door swings or "
                  f"other furniture until you validate."]


def op_move_furniture(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, item, room = find_furniture(data, str(params.get("item_id") or ""))
    if item is None or room is None:
        return False, ["Could not find that furniture item."]
    x = float(params.get("x", item.x))
    y = float(params.get("y", item.y))
    rect = room_rect(room)
    hw, hd = item.w / 2, item.d / 2
    x = min(max(x, rect[0] + hw), rect[0] + rect[2] - hw)
    y = min(max(y, rect[1] + hd), rect[1] + rect[3] - hd)
    item.x, item.y = round(x, 3), round(y, 3)
    return True, [f"Moved {item.label or item.kind} inside {room.name} to "
                  f"({item.x:.2f}, {item.y:.2f})."]


def op_delete_furniture(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    floor, item, room = find_furniture(data, str(params.get("item_id") or ""))
    if floor is None or item is None:
        return False, ["Could not find that furniture item."]
    floor.furniture = [f for f in floor.furniture if f.id != item.id]
    return True, [f"Removed {item.label or item.kind}."]


def op_change_requirements(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    patch = params.get("patch") or {}
    if not patch:
        key = params.get("field")
        if not key:
            return False, ["Nothing to change."]
        patch = {key: params.get("value")}
    notes: List[str] = []
    for key, value in patch.items():
        if not hasattr(data.requirements, key):
            return False, [f"'{key}' is not a design requirement."]
        current = getattr(data.requirements, key)
        if isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, int) and not isinstance(value, bool):
            value = int(value)
        elif isinstance(current, float):
            value = float(value)
        setattr(data.requirements, key, value)
        notes.append(f"Requirement '{key}' set to {value}.")
    notes.append("Requirements changed. Regenerate the design to apply them -- your current manual "
                 "edits will be replaced, so save a version first.")
    return True, notes


def op_adjust_setback(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    side = str(params.get("side") or "").lower()
    value = params.get("value")
    if side not in ("north", "south", "east", "west", "n", "s", "e", "w", "front", "rear"):
        return False, [f"'{side}' is not a setback side. Use north/south/east/west or front/rear."]
    if isinstance(value, str):
        value = parse_dimension(value)
    if value is None:
        return False, ["I could not read a distance from that request."]
    value = max(0.0, float(value))
    mapping = {"n": "north", "s": "south", "e": "east", "w": "west", "front": data.plot.road_side.lower()}
    field = mapping.get(side, side)
    if field == "front":
        field = data.plot.road_side.lower()
    if field == "rear":
        field = {"N": "south", "S": "north", "E": "west", "W": "east"}[data.plot.road_side]
    field = {"n": "north", "s": "south", "e": "east", "w": "west"}.get(field, field)
    setattr(data.plot.setbacks, field, round(value, 3))
    data.plot.setbacks.source = "user"
    data.plot.setbacks.verified = bool(params.get("verified"))
    return True, [f"{field.capitalize()} setback set to {value:.2f} m. Regenerate the design to "
                  f"apply it. Unless you verified this with your local authority, treat it as your "
                  f"own assumption."]


def op_regenerate(data: ProjectData, params: Dict[str, Any]) -> OpResult:
    from ..designer import generate_design
    concept = params.get("concept")
    patch = params.get("requirements") or {}
    for key, value in patch.items():
        if hasattr(data.requirements, key):
            setattr(data.requirements, key, value)
    concepts = params.get("concepts") or ([concept] if concept else
                                          ["space_efficient", "budget_conscious", "family_comfort",
                                           "modern_contemporary", "traditional_indian"])
    generate_design(data, concepts=concepts, active=concept or data.active_concept)
    return True, [f"Regenerated the design with concept '{data.active_concept}'. All manual edits "
                  f"were replaced by the new geometry."]


OPERATIONS: Dict[str, Callable[[ProjectData, Dict[str, Any]], OpResult]] = {
    "rename_room": op_rename_room,
    "resize_room": op_resize_room,
    "move_room": op_move_room,
    "delete_room": op_delete_room,
    "add_room": op_add_room,
    "swap_rooms": op_swap_rooms,
    "split_room": op_split_room,
    "change_material": op_change_material,
    "change_style": op_change_style,
    "room_finish": op_room_finish,
    "add_opening": op_add_opening,
    "move_opening": op_move_opening,
    "delete_opening": op_delete_opening,
    "add_furniture": op_add_furniture,
    "move_furniture": op_move_furniture,
    "delete_furniture": op_delete_furniture,
    "change_requirements": op_change_requirements,
    "adjust_setback": op_adjust_setback,
    "regenerate": op_regenerate,
}

APPEARANCE_OPS = {"change_material", "change_style", "room_finish", "add_furniture",
                  "move_furniture", "delete_furniture"}


def is_structural(op: Op) -> bool:
    return op.get("op") in STRUCTURAL_OPS and op.get("op") not in APPEARANCE_OPS


def apply_operations(data: ProjectData, operations: List[Op]) -> Tuple[ProjectData, List[str], bool]:
    """Apply operations in order. Returns (data, notes, any_structural)."""
    notes: List[str] = []
    structural = False
    for op in operations:
        name = op.get("op")
        handler = OPERATIONS.get(str(name))
        if handler is None:
            notes.append(f"Skipped unknown operation '{name}'.")
            continue
        ok, messages = handler(data, op.get("params") or {})
        notes.extend(messages)
        if not ok:
            notes.append(f"Operation '{name}' was not applied.")
        elif is_structural(op):
            structural = True
    return data, notes, structural


def preview_operations(data: ProjectData, operations: List[Op]) -> Dict[str, Any]:
    """Apply on a deep copy and report what would change -- never touches the original."""
    candidate = data.model_copy(deep=True)
    before_issues = {f"{i.code}:{i.room_id or ''}" for i in validate_project(data)}
    before_area = sum(r.area for f in data.floors for r in f.rooms if not r.is_outdoor)
    before_rooms = {r.id: (r.name, round(r.area, 2)) for f in data.floors for r in f.rooms}
    notes: List[str] = []
    applied: List[str] = []
    rejected: List[str] = []
    structural = False
    for op in operations:
        name = str(op.get("op"))
        handler = OPERATIONS.get(name)
        if handler is None:
            rejected.append(f"{name}: unknown operation")
            continue
        ok, messages = handler(candidate, op.get("params") or {})
        notes.extend(messages)
        (applied if ok else rejected).append(name)
        if ok and is_structural(op):
            structural = True
    after_issues = {f"{i.code}:{i.room_id or ''}" for i in validate_project(candidate)}
    after_area = sum(r.area for f in candidate.floors for r in f.rooms if not r.is_outdoor)
    changed_rooms = []
    for room in candidate.all_rooms():
        old = before_rooms.get(room.id)
        if old is None:
            changed_rooms.append({"id": room.id, "name": room.name, "change": "added",
                                  "area_after": round(room.area, 2)})
        elif abs(old[1] - room.area) > 0.02 or old[0] != room.name:
            changed_rooms.append({"id": room.id, "name": room.name, "change": "modified",
                                  "area_before": old[1], "area_after": round(room.area, 2),
                                  "delta": round(room.area - old[1], 2)})
    for room_id, (name, _) in before_rooms.items():
        if candidate.find_room(room_id)[1] is None:
            changed_rooms.append({"id": room_id, "name": name, "change": "deleted"})
    new_issues = sorted(after_issues - before_issues)
    fixed_issues = sorted(before_issues - after_issues)
    return {
        "ok": bool(applied) and not rejected,
        "applied": applied, "rejected": rejected, "notes": notes,
        "structural": structural,
        "area_before_m2": round(before_area, 2), "area_after_m2": round(after_area, 2),
        "area_delta_m2": round(after_area - before_area, 2),
        "changed_rooms": changed_rooms,
        "new_issues": [i.model_dump() for i in validate_project(candidate)
                       if f"{i.code}:{i.room_id or ''}" in set(new_issues)],
        "fixed_issues": list(fixed_issues),
        "preview": candidate,
        "warning": ("This changes the wall layout. Which walls carry load is a structural question "
                    "-- a licensed engineer must confirm it before anything is built."
                    if structural else
                    "Appearance-only change: no geometry was modified."),
    }
