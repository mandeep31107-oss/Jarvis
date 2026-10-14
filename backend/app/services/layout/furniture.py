"""Parametric furniture placement.

Every item is an axis-aligned box in plan space. ``x, y`` is the **centre** of the
footprint, ``w`` runs East-West, ``d`` runs North-South and ``z`` is the height of
the item's base above the floor (used for wall cabinets, mirrors and TVs).

Placement is greedy and rule based: items are anchored to walls according to their
function, keep clear of door swings and tall items keep clear of windows. Anything
that does not fit is reported instead of being silently overlapped.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..room_catalog import spec
from ...schemas import Edge, Floor, FurnitureItem, Opening, Requirements, Room
from .concepts import Concept
from .partition import r3

# kind -> (length along wall, depth into room, height, z offset)
FURNITURE_SPECS: Dict[str, Tuple[float, float, float, float]] = {
    "bed_king": (1.95, 2.05, 0.55, 0.0),
    "bed_double": (1.55, 2.0, 0.5, 0.0),
    "bed_single": (1.0, 1.95, 0.45, 0.0),
    "wardrobe": (1.6, 0.6, 2.15, 0.0),
    "wardrobe_large": (2.4, 0.62, 2.2, 0.0),
    "wardrobe_small": (0.95, 0.55, 2.0, 0.0),
    "side_table": (0.45, 0.4, 0.5, 0.0),
    "dresser": (1.2, 0.45, 0.8, 0.0),
    "accent_chair": (0.75, 0.75, 0.85, 0.0),
    "sofa_3": (2.15, 0.92, 0.78, 0.0),
    "sofa_2": (1.5, 0.88, 0.78, 0.0),
    "coffee_table": (1.1, 0.6, 0.42, 0.0),
    "tv_unit": (1.7, 0.42, 0.5, 0.0),
    "rug": (2.6, 1.8, 0.02, 0.0),
    "plant": (0.45, 0.45, 1.0, 0.0),
    "tree": (1.4, 1.4, 3.2, 0.0),
    "dining_table": (1.65, 0.95, 0.76, 0.0),
    "dining_chair": (0.46, 0.46, 0.92, 0.0),
    "sideboard": (1.45, 0.45, 0.85, 0.0),
    "kitchen_counter": (2.6, 0.62, 0.9, 0.0),
    "kitchen_upper": (2.4, 0.36, 0.72, 1.45),
    "sink": (0.62, 0.45, 0.06, 0.88),
    "stove": (0.6, 0.5, 0.05, 0.88),
    "fridge": (0.72, 0.7, 1.75, 0.0),
    "washing_machine": (0.62, 0.62, 0.85, 0.0),
    "utility_shelf": (1.1, 0.4, 1.7, 0.0),
    "toilet": (0.4, 0.66, 0.78, 0.0),
    "wash_basin": (0.52, 0.44, 0.85, 0.0),
    "shower": (0.95, 0.95, 2.05, 0.0),
    "mirror_cabinet": (0.6, 0.14, 0.7, 1.35),
    "desk": (1.4, 0.62, 0.75, 0.0),
    "study_desk_small": (1.05, 0.55, 0.75, 0.0),
    "office_chair": (0.55, 0.55, 1.05, 0.0),
    "bookshelf": (1.25, 0.34, 1.9, 0.0),
    "puja_unit": (1.05, 0.5, 1.85, 0.0),
    "low_seat": (1.2, 0.5, 0.42, 0.0),
    "shelving": (1.5, 0.45, 1.95, 0.0),
    "shoe_rack": (1.0, 0.36, 0.9, 0.0),
    "bench": (1.2, 0.45, 0.45, 0.0),
    "balcony_chair": (0.62, 0.62, 0.8, 0.0),
    "seating_outdoor": (1.5, 0.75, 0.7, 0.0),
    "tulsi_stand": (0.5, 0.5, 0.95, 0.0),
    "counter_shop": (2.1, 0.6, 1.05, 0.0),
}

WALL_PREFERENCE: Dict[str, List[Edge]] = {
    "bed_king": ["N", "E", "W", "S"],
    "bed_double": ["N", "E", "W", "S"],
    "bed_single": ["N", "E", "W", "S"],
    "wardrobe": ["S", "W", "E", "N"],
    "wardrobe_large": ["S", "W", "E", "N"],
    "kitchen_counter": ["E", "W", "N", "S"],
    "kitchen_upper": ["E", "W", "N", "S"],
    "sofa_3": ["S", "N", "W", "E"],
    "tv_unit": ["N", "S", "E", "W"],
    "toilet": ["N", "S", "E", "W"],
    "shower": ["N", "E", "W", "S"],
    "wash_basin": ["S", "E", "W", "N"],
}

FREE_STANDING = {"coffee_table", "rug", "dining_table", "accent_chair", "office_chair",
                 "plant", "tree", "tulsi_stand", "low_seat", "balcony_chair", "seating_outdoor"}

TALL_ITEMS = {"wardrobe", "wardrobe_large", "wardrobe_small", "bookshelf", "shelving", "fridge",
              "kitchen_upper", "puja_unit", "utility_shelf", "mirror_cabinet", "sideboard"}


def _opening_keepouts(room: Room, openings: List[Opening]) -> List[Tuple[float, float, float, float]]:
    """Rectangles (x, y, w, d) inside the room that must stay clear."""
    boxes: List[Tuple[float, float, float, float]] = []
    for op in openings:
        if op.room_id != room.id:
            continue
        if op.kind == "window":
            continue  # windows only constrain tall items (handled via _blocks_window)
        if op.kind in ("door", "sliding"):
            # door swing: scaled to the room, because a 1 m swing keep-out in a
            # 2 m bathroom would make every fixture "not fit"
            depth = min(0.95, max(0.55, 0.34 * min(room.w, room.d)))
        else:
            depth = 0.25
        centre = op.clamped_offset(room)
        width = op.width + 0.2
        if op.edge == "S":
            boxes.append((room.x + centre - width / 2, room.y, width, depth))
        elif op.edge == "N":
            boxes.append((room.x + centre - width / 2, room.y + room.d - depth, width, depth))
        elif op.edge == "W":
            boxes.append((room.x, room.y + centre - width / 2, depth, width))
        else:
            boxes.append((room.x + room.w - depth, room.y + centre - width / 2, depth, width))
    return boxes


def _window_spans(room: Room, openings: List[Opening]) -> List[Tuple[Edge, float, float]]:
    spans = []
    for op in openings:
        if op.room_id == room.id and op.kind in ("window", "sliding"):
            centre = op.clamped_offset(room)
            spans.append((op.edge, centre - op.width / 2 - 0.15, centre + op.width / 2 + 0.15))
    return spans


def _intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float], gap: float = 0.02) -> bool:
    return not (a[0] + a[2] + gap <= b[0] or b[0] + b[2] + gap <= a[0] or
                a[1] + a[3] + gap <= b[1] or b[1] + b[3] + gap <= a[1])


def _box_from_anchor(room: Room, edge: Edge, along_pos: float, length: float, depth: float) -> Tuple[float, float, float, float]:
    """Return (x0, y0, w, d) for an item anchored to a wall."""
    if edge == "S":
        return (room.x + along_pos - length / 2, room.y, length, depth)
    if edge == "N":
        return (room.x + along_pos - length / 2, room.y + room.d - depth, length, depth)
    if edge == "W":
        return (room.x, room.y + along_pos - length / 2, depth, length)
    return (room.x + room.w - depth, room.y + along_pos - length / 2, depth, length)


def _edge_candidates(room: Room, edge: Edge, length: float) -> List[float]:
    span = room.w if edge in ("N", "S") else room.d
    if span < length + 0.1:
        return []
    centre = span / 2
    out = [centre]
    for frac in (0.38, 0.62, 0.28, 0.72, 0.2, 0.8):
        pos = span * frac
        if length / 2 + 0.05 <= pos <= span - length / 2 - 0.05:
            out.append(pos)
    return out


def _blocks_window(room: Room, edge: Edge, along_pos: float, length: float, windows: List[Tuple[Edge, float, float]]) -> bool:
    for wed, lo, hi in windows:
        if wed != edge:
            continue
        if along_pos - length / 2 < hi and along_pos + length / 2 > lo:
            return True
    return False


def furnish_room(floor: Floor, room: Room, concept: Concept, req: Requirements,
                 notes: List[str]) -> List[FurnitureItem]:
    kinds = list(spec(room.type).furniture)
    if not kinds:
        return []
    if room.is_outdoor and room.type not in ("balcony", "courtyard", "garden", "terrace"):
        return []

    openings = [op for op in floor.openings if op.room_id == room.id]
    keepouts = _opening_keepouts(room, openings)
    windows = _window_spans(room, openings)
    placed: List[FurnitureItem] = []
    failed: List[Tuple[str, float, float, float, float]] = []
    occupied: List[Tuple[float, float, float, float]] = []
    inset = 0.05
    inner = (room.x + inset, room.y + inset, max(0.1, room.w - 2 * inset), max(0.1, room.d - 2 * inset))
    outer = (room.x, room.y, room.w, room.d)

    def fits(box: Tuple[float, float, float, float], tall: bool, allow_overlap_rug: bool = False) -> bool:
        tol = 0.02
        if box[0] < outer[0] - tol or box[1] < outer[1] - tol:
            return False
        if box[0] + box[2] > outer[0] + outer[2] + tol or box[1] + box[3] > outer[1] + outer[3] + tol:
            return False
        for ko in keepouts:
            if _intersects(box, ko, gap=0.02):
                return False
        for other in occupied:
            if allow_overlap_rug and (other[3] < 0.05 or box[3] < 0.05):
                continue
            if _intersects(box, other):
                return False
        return True

    def commit(kind: str, box: Tuple[float, float, float, float], z: float = 0.0,
               height: Optional[float] = None, rotation: float = 0.0, label: str = "") -> FurnitureItem:
        length, depth, default_h, default_z = FURNITURE_SPECS.get(kind, (box[2], box[3], 0.8, 0.0))
        item = FurnitureItem(
            kind=kind, room_id=room.id, floor_id=floor.id,
            x=r3(box[0] + box[2] / 2), y=r3(box[1] + box[3] / 2),
            w=r3(box[2]), d=r3(box[3]), height=r3(height if height is not None else default_h),
            z=r3(z if z else default_z), rotation=rotation, label=label,
        )
        placed.append(item)
        if box[3] >= 0.05 and (z if z else default_z) < 0.4:
            occupied.append(box)   # wall mounted units do not block floor space
        return item

    # ---- beds first (they define the room) ---------------------------------
    for kind in list(kinds):
        if not kind.startswith("bed_"):
            continue
        length, depth, height, z = FURNITURE_SPECS[kind]
        spot = _place_on_wall(room, kind, length, depth, keepouts, occupied, windows, fits, inner)
        if spot is None:
            if length > 1.2:
                smaller = "bed_double" if kind == "bed_king" else "bed_single"
                length2, depth2, h2, z2 = FURNITURE_SPECS[smaller]
                spot = _place_on_wall(room, smaller, length2, depth2, keepouts, occupied, windows, fits, inner)
                if spot is not None:
                    commit(smaller, spot, z2, h2)
                    notes.append(f"{room.name}: only a smaller bed fits in this layout "
                                 f"({length2:.2f} m x {depth2:.2f} m).")
                    kinds.remove(kind)
                    continue
            notes.append(f"{room.name}: not enough clear space for a bed; the room needs to be larger "
                         f"or the door moved.")
            kinds.remove(kind)
            continue
        commit(kind, spot, z, height)
        kinds.remove(kind)

    # ---- kitchen runs -------------------------------------------------------
    if room.type == "kitchen":
        length, depth, height, z = FURNITURE_SPECS["kitchen_counter"]
        run_len = min(length, max(1.8, (room.w if room.d >= room.w else room.d) - 0.7))
        spot = _place_on_wall(room, "kitchen_counter", run_len, depth, keepouts, occupied, windows, fits, inner,
                              ignore_windows=True)
        if spot is None:
            notes.append("Kitchen: no wall long enough for a counter run; consider enlarging the kitchen.")
        else:
            counter = commit("kitchen_counter", spot, 0.0, height, label="Counter + cabinets")
            edge, along = _spot_anchor(room, spot, depth)
            # sink and hob on the counter
            sink_box = _inset_on_counter(room, edge, along, spot, 0.62, 0.45, 0.3)
            if sink_box:
                commit("sink", sink_box, 0.88, 0.06)
            stove_box = _inset_on_counter(room, edge, along, spot, 0.6, 0.5, 0.68)
            if stove_box:
                commit("stove", stove_box, 0.88, 0.05)
            if "kitchen_upper" in kinds:
                upper = _place_on_wall(room, "kitchen_upper", min(2.4, run_len), 0.36, [], [], [], 
                                       lambda b, t, a=False: True, inner, force_edge=edge)
                if upper:
                    commit("kitchen_upper", upper, 1.45, 0.72)
                kinds.remove("kitchen_upper")
            kinds = [k for k in kinds if k not in ("kitchen_counter", "sink", "stove")]
            _ = counter

    # ---- bathroom / toilet --------------------------------------------------
    if room.type in ("bathroom", "toilet"):
        order = ("shower", "toilet", "wash_basin", "mirror_cabinet")
        if room.area < 6.5:
            order = ("toilet", "wash_basin", "shower", "mirror_cabinet")
        for kind in order:
            if kind not in kinds:
                continue
            length, depth, height, z = FURNITURE_SPECS[kind]
            if kind == "shower" and room.area < 6.5:
                length = depth = min(length, 0.82)   # compact shower zone
            if kind == "shower" and min(room.w, room.d) < 1.5:
                notes.append(f"{room.name}: too small for a separate shower area; a bath fittings "
                             f"cluster will be used instead.")
                kinds.remove(kind)
                continue
            spot = _place_on_wall(room, kind, length, depth, keepouts, occupied, windows, fits, inner,
                                  ignore_windows=(kind == "mirror_cabinet"))
            if spot is None:
                failed.append((kind, length, depth, height, z))
                kinds.remove(kind)
                continue
            commit(kind, spot, z, height)
            kinds.remove(kind)
        kinds = [k for k in kinds if k not in ("shower", "toilet", "wash_basin", "mirror_cabinet")]

    # ---- everything else ----------------------------------------------------
    for kind in list(kinds):
        if kind == "chairs":
            _place_dining_chairs(room, placed, occupied, commit, notes)
            kinds.remove(kind)
            continue
        if kind not in FURNITURE_SPECS:
            continue
        length, depth, height, z = FURNITURE_SPECS[kind]
        if kind == "sofa_3" and min(room.w, room.d) < 2.6:
            kind = "sofa_2"
            length, depth, height, z = FURNITURE_SPECS[kind]
        if kind in FREE_STANDING:
            box = _place_free(room, length, depth, keepouts, occupied, fits, inner, placed)
            if box is None:
                if kind not in ("rug", "plant", "tree", "tulsi_stand"):
                    failed.append((kind, length, depth, height, z))
                continue
            commit(kind, box, z, height, label="")
            continue

        if kind == "tv_unit":
            # face the sofa
            sofa = next((p for p in placed if p.kind.startswith("sofa")), None)
            if sofa is not None:
                edge = _opposite_edge_of(room, sofa)
                spot = _place_on_wall(room, kind, length, depth, keepouts, occupied, windows, fits, inner,
                                      force_edge=edge, ignore_windows=True)
                if spot:
                    commit(kind, spot, z, height)
                    continue
        spot = _place_on_wall(room, kind, length, depth, keepouts, occupied, windows, fits, inner,
                              ignore_windows=kind not in TALL_ITEMS)
        if spot is None:
            if kind in ("side_table", "accent_chair", "rug", "study_desk_small", "low_seat", "bench",
                        "plant", "mirror_cabinet", "dressing"):
                continue
            failed.append((kind, length, depth, height, z))
            continue
        commit(kind, spot, z, height)

    # ---- second pass: shrink the door swing keep-out -----------------------
    if failed:
        relaxed = [(k[0] + k[2] * 0.2, k[1] + k[3] * 0.2, k[2] * 0.6, k[3] * 0.6) for k in keepouts]

        def relaxed_fits(box, tall, allow_overlap_rug=False):
            tol = 0.02
            if box[0] < room.x - tol or box[1] < room.y - tol:
                return False
            if box[0] + box[2] > room.x + room.w + tol or box[1] + box[3] > room.y + room.d + tol:
                return False
            for ko in relaxed:
                if _intersects(box, ko, gap=0.02):
                    return False
            for other in occupied:
                if allow_overlap_rug and (other[3] < 0.05 or box[3] < 0.05):
                    continue
                if _intersects(box, other):
                    return False
            return True

        for kind, length, depth, height, z in failed:
            box = None
            if kind in FREE_STANDING:
                box = _place_free(room, length, depth, relaxed, occupied, relaxed_fits, inner, placed)
            else:
                box = _place_on_wall(room, kind, length, depth, relaxed, occupied, windows,
                                     relaxed_fits, inner, ignore_windows=kind not in TALL_ITEMS)
            if box is None:
                notes.append(f"{room.name}: no space for the {kind.replace('_', ' ')} -- the room is at "
                             f"its minimum size in this layout. Enlarge the room or move a door.")
                continue
            commit(kind, box, z, height)
            notes.append(f"{room.name}: the {kind.replace('_', ' ')} sits close to the door swing. "
                         f"Use an outward opening or sliding door here.")

    return placed


def _spot_anchor(room: Room, box: Tuple[float, float, float, float], depth: float) -> Tuple[Edge, float]:
    cy = box[1] + box[3] / 2
    cx = box[0] + box[2] / 2
    if abs(box[1] - room.y) < 0.05:
        return ("S", cx - room.x)
    if abs(box[1] + box[3] - (room.y + room.d)) < 0.05:
        return ("N", cx - room.x)
    if abs(box[0] - room.x) < 0.05:
        return ("W", cy - room.y)
    return ("E", cy - room.y)


def _inset_on_counter(room: Room, edge: Edge, along: float, counter: Tuple[float, float, float, float],
                      length: float, depth: float, offset_frac: float) -> Optional[Tuple[float, float, float, float]]:
    """Position a sink/hob on the counter run."""
    cx, cy, cw, cd = counter
    if edge in ("N", "S"):
        pos = cx + cw * offset_frac
        if pos - length / 2 < cx + 0.05 or pos + length / 2 > cx + cw - 0.05:
            return None
        y = cy + (cd - depth) / 2
        return (pos - length / 2, y, length, depth)
    pos = cy + cd * offset_frac
    if pos - length / 2 < cy + 0.05 or pos + length / 2 > cy + cd - 0.05:
        return None
    x = cx + (cw - depth) / 2
    return (x, pos - length / 2, depth, length)


def _place_on_wall(room: Room, kind: str, length: float, depth: float,
                   keepouts: List[Tuple[float, float, float, float]],
                   occupied: List[Tuple[float, float, float, float]],
                   windows: List[Tuple[Edge, float, float]], fits, inner=None,
                   ignore_windows: bool = False, force_edge: Optional[Edge] = None,
                   ) -> Optional[Tuple[float, float, float, float]]:
    edges = [force_edge] if force_edge else WALL_PREFERENCE.get(kind, ["N", "S", "E", "W"])
    tall = kind in TALL_ITEMS
    for edge in edges:
        span = room.w if edge in ("N", "S") else room.d
        if span < length - 0.02 or (room.d if edge in ("N", "S") else room.w) < depth - 0.02:
            continue
        for along in _edge_candidates(room, edge, length):
            if not ignore_windows and tall and _blocks_window(room, edge, along, length, windows):
                continue
            box = _box_from_anchor(room, edge, along, length, depth)
            if fits(box, tall):
                return box
        # second pass: shrink slightly
        shrunk = min(length, span - 0.1)
        if shrunk < length * 0.6:
            continue
        for along in _edge_candidates(room, edge, shrunk):
            box = _box_from_anchor(room, edge, along, shrunk, depth)
            if fits(box, tall):
                return box
    return None


def _place_free(room: Room, length: float, depth: float, keepouts, occupied, fits, inner,
                placed: List[FurnitureItem]) -> Optional[Tuple[float, float, float, float]]:
    fracs = (0.5, 0.36, 0.64, 0.26, 0.74, 0.18, 0.82)
    for scale in (1.0, 0.88, 0.76):
        ln, dp = length * scale, depth * scale
        if ln > inner[2] or dp > inner[3]:
            ln, dp = min(ln, inner[2] - 0.05), min(dp, inner[3] - 0.05)
        if ln < 0.35 or dp < 0.35:
            continue
        for fx in fracs:
            for fy in fracs:
                cx = inner[0] + inner[2] * fx
                cy = inner[1] + inner[3] * fy
                box = (cx - ln / 2, cy - dp / 2, ln, dp)
                if box[0] < inner[0] - 0.02 or box[1] < inner[1] - 0.02:
                    continue
                if box[0] + box[2] > inner[0] + inner[2] + 0.02 or box[1] + box[3] > inner[1] + inner[3] + 0.02:
                    continue
                if fits(box, False, allow_overlap_rug=True):
                    return box
    return None


def _opposite_edge_of(room: Room, item: FurnitureItem) -> Edge:
    if item.y - item.d / 2 <= room.y + 0.1:
        return "N"
    if item.y + item.d / 2 >= room.y + room.d - 0.1:
        return "S"
    if item.x - item.w / 2 <= room.x + 0.1:
        return "E"
    return "W"


def _place_dining_chairs(room: Room, placed: List[FurnitureItem], occupied, commit, notes) -> None:
    table = next((p for p in placed if p.kind == "dining_table"), None)
    if table is None:
        return
    chair_len, chair_depth, chair_h, _ = FURNITURE_SPECS["dining_chair"]
    gap = 0.08
    seats: List[Tuple[float, float, float]] = []
    horizontal = table.w >= table.d
    count_each = 2 if table.w >= 1.5 else 1
    if horizontal:
        for i in range(count_each):
            off = (i - (count_each - 1) / 2) * (chair_len + gap)
            seats.append((table.x + off, table.y - table.d / 2 - chair_depth / 2 - gap, 180.0))
            seats.append((table.x + off, table.y + table.d / 2 + chair_depth / 2 + gap, 0.0))
    else:
        for i in range(count_each):
            off = (i - (count_each - 1) / 2) * (chair_len + gap)
            seats.append((table.x - table.w / 2 - chair_depth / 2 - gap, table.y + off, 90.0))
            seats.append((table.x + table.w / 2 + chair_depth / 2 + gap, table.y + off, 270.0))
    added = 0
    for cx, cy, rot in seats:
        w = chair_depth if rot in (90.0, 270.0) else chair_len
        d = chair_len if rot in (90.0, 270.0) else chair_depth
        box = (cx - w / 2, cy - d / 2, w, d)
        if box[0] < room.x or box[1] < room.y or box[0] + box[2] > room.x + room.w or box[1] + box[3] > room.y + room.d:
            continue
        commit("dining_chair", box, 0.0, chair_h, rotation=rot)
        added += 1
    if added == 0:
        notes.append(f"{room.name}: chairs do not fit around the dining table -- leave at least "
                     f"0.75 m of circulation around it.")


def furnish_floor(floor: Floor, concept: Concept, req: Requirements) -> List[str]:
    notes: List[str] = []
    for room in floor.rooms:
        if room.type in ("stairs", "open_to_below"):
            continue
        items = furnish_room(floor, room, concept, req, notes)
        floor.furniture.extend(items)
    return notes
