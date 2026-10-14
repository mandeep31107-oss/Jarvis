"""Turns user requirements into a concrete room programme for each floor.

Zoning works in compass space (``+x = East``, ``+y = North``) so that optional
Vastu preferences, road-facing preferences and climate logic can all be expressed
in the same coordinate system.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..room_catalog import PARKING_DIMS, spec
from .concepts import Concept
from .partition import Item, Rect, rect_area

# Normalised target point for each compass zone (x -> East, y -> North)
ZONE_POINTS: Dict[str, Tuple[float, float]] = {
    "N": (0.5, 0.85), "NE": (0.85, 0.85), "E": (0.85, 0.5), "SE": (0.85, 0.15),
    "S": (0.5, 0.15), "SW": (0.15, 0.15), "W": (0.15, 0.5), "NW": (0.15, 0.85),
    "C": (0.5, 0.5),
}

OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}

# Preferred zone per room type when Vastu is OFF (relative to the road side)
RELATIVE_ZONES: Dict[str, str] = {
    "lobby": "front", "porch": "front", "living": "front", "puja": "front",
    "shop": "front", "balcony": "front", "garden": "front", "parking": "front",
    "dining": "mid", "stairs": "mid", "corridor": "mid", "toilet": "mid",
    "study": "mid", "courtyard": "center", "open_to_below": "mid",
    "kitchen": "rear", "utility": "rear", "bedroom": "rear",
    "master_bedroom": "rear", "bathroom": "rear", "store": "rear",
    "servant_room": "rear", "terrace": "rear",
}

# Vastu suggestions (optional guidance, never a substitute for engineering review)
VASTU_ZONES: Dict[str, str] = {
    "kitchen": "SE", "utility": "SE", "master_bedroom": "SW", "bedroom": "W",
    "puja": "NE", "living": "N", "dining": "W", "toilet": "NW", "bathroom": "NW",
    "stairs": "S", "store": "NW", "courtyard": "C", "study": "E",
    "servant_room": "NW", "lobby": "N", "shop": "N", "corridor": "C",
    "balcony": "N", "terrace": "C", "garden": "N", "parking": "NW",
    "open_to_below": "C", "porch": "N",
}


def front_zone(road_side: str) -> str:
    return road_side


def rear_zone(road_side: str) -> str:
    return OPPOSITE[road_side]


def side_zones(road_side: str) -> Tuple[str, str]:
    if road_side in ("N", "S"):
        return ("E", "W")
    return ("N", "S")


# Where each room type ideally sits, expressed as
# (u = position across the frontage, 0 = left when standing on the road facing the
#  plot, 1 = right;  v = depth, 0 = road side, 1 = rear).
LAYOUT_HINTS: Dict[str, Tuple[float, float]] = {
    "shop": (0.22, 0.08), "porch": (0.35, 0.08), "parking": (0.14, 0.22),
    "lobby": (0.62, 0.14), "stairs": (0.88, 0.20), "puja": (0.76, 0.10),
    "living": (0.33, 0.32), "balcony": (0.35, 0.05), "garden": (0.6, 0.05),
    "dining": (0.38, 0.52), "corridor": (0.62, 0.50), "courtyard": (0.5, 0.5),
    "study": (0.16, 0.62), "utility": (0.90, 0.48), "kitchen": (0.86, 0.66),
    "toilet": (0.86, 0.80), "bathroom": (0.86, 0.86), "store": (0.90, 0.94),
    "servant_room": (0.88, 0.96), "bedroom": (0.30, 0.76),
    "master_bedroom": (0.28, 0.88), "open_to_below": (0.5, 0.5), "terrace": (0.5, 0.5),
}


def hint_to_point(kind: str, road_side: str, rect: Rect) -> Tuple[float, float]:
    """Convert a frontage/depth hint into absolute compass plan coordinates."""
    u, v = LAYOUT_HINTS.get(kind, (0.5, 0.5))
    if road_side == "S":
        nx, ny = u, v
    elif road_side == "N":
        nx, ny = 1.0 - u, 1.0 - v
    elif road_side == "E":
        nx, ny = 1.0 - v, 1.0 - u
    else:
        nx, ny = v, u
    return (rect[0] + nx * rect[2], rect[1] + ny * rect[3])


def resolve_zone(kind: str, road_side: str, vastu: bool) -> str:
    """Vastu gives an explicit compass zone; otherwise use the layout hints."""
    if vastu and kind in VASTU_ZONES:
        return VASTU_ZONES[kind]
    return "hint"


def zone_target(zone: str, rect: Rect, kind: str = "", road_side: str = "S") -> Tuple[float, float]:
    if zone == "hint":
        return hint_to_point(kind or "living", road_side, rect)
    nx, ny = ZONE_POINTS.get(zone, (0.5, 0.5))
    return (rect[0] + nx * rect[2], rect[1] + ny * rect[3])


TYPE_MAX_AREA: Dict[str, float] = {
    "bathroom": 6.5, "toilet": 4.0, "utility": 5.5, "store": 7.0, "puja": 6.5,
    "corridor": 9.0, "lobby": 9.0, "bedroom": 21.0, "master_bedroom": 26.0,
    "living": 34.0, "dining": 18.0, "kitchen": 15.0, "study": 15.0,
    "servant_room": 12.0, "shop": 26.0, "stairs": 12.0,
    "balcony": 9.0, "terrace": 60.0, "courtyard": 20.0, "garden": 60.0,
    "parking": 26.0, "porch": 12.0, "open_to_below": 30.0,
}


def make_item(
    kind: str,
    name: str,
    zone: str,
    rect: Rect,
    generosity: float = 1.0,
    weight: float = 1.0,
    fixed: Optional[Rect] = None,
    tag: str = "",
    area_scale: float = 1.0,
    road_side: str = "S",
) -> Item:
    s = spec(kind)
    tx, ty = zone_target(zone, rect, kind, road_side)
    target = max(s.min_area, s.comfort_area * generosity * area_scale)
    cap = TYPE_MAX_AREA.get(kind, max(target * 1.8, s.comfort_area * 1.7))
    return Item(
        type=kind,
        target=target,
        min_area=s.min_area * (0.92 if not s.habitable else 1.0),
        max_area=max(target * 1.25, min(cap, max(target * 1.75, s.comfort_area * 1.6))),
        min_dim=s.min_dim,
        max_aspect=s.max_aspect,
        tx=tx if fixed is None else (fixed[0] + fixed[2] / 2),
        ty=ty if fixed is None else (fixed[1] + fixed[3] / 2),
        weight=s.weight * weight,
        name=name,
        fixed=fixed,
        outdoor=s.outdoor,
        tag=tag,
    )


def lock_item_to_rect(item: Item) -> Item:
    """Make a fixed item demand exactly its rectangle (used for stair cores)."""
    area = rect_area(item.fixed) if item.fixed else item.target
    item.target = area
    item.min_area = area * 0.97
    item.max_area = area * 1.03
    if item.fixed:
        item.min_dim = min(item.fixed[2], item.fixed[3]) * 0.95
    item.max_aspect = 4.0
    return item


OPTIONAL_ORDER = ["store", "study", "puja", "servant_room", "shop", "utility", "corridor", "balcony"]


def fit_program(items: List[Item], available_area: float, slack_ratio: float = 0.82
                ) -> Tuple[List[Item], List[str]]:
    """Make the programme fit the space that is actually available.

    A plan needs *slack*: if the minimum room areas add up to 100% of the
    footprint, every room ends up the width of a corridor. So we merge the dining
    area into the living room and drop genuinely optional rooms until the minimum
    areas occupy no more than ``slack_ratio`` of the available area. Bedrooms are
    only touched as a last resort, and every change is reported to the user.
    """
    notes: List[str] = []
    kept = list(items)

    def minimum(rows: List[Item]) -> float:
        return sum(r.min_area for r in rows if r.fixed is None) + sum(
            rect_area(r.fixed) for r in rows if r.fixed is not None)

    def has_slack(rows: List[Item]) -> bool:
        return minimum(rows) <= available_area * slack_ratio

    # 1. merge dining into living when the plan is tight
    if not has_slack(kept):
        living = next((r for r in kept if r.type == "living"), None)
        dining = next((r for r in kept if r.type == "dining"), None)
        if living is not None and dining is not None:
            living.name = "Living + Dining"
            living.target = living.target + dining.target * 0.85
            living.max_area = max(living.max_area, living.target * 1.4)
            living.min_area = min(available_area * 0.45, living.min_area + dining.min_area * 0.55)
            living.meta["merged_dining"] = True
            kept.remove(dining)
            notes.append("Merged the dining area into the living room (open living-dining) so the "
                         "remaining rooms keep a usable width on this plot.")

    # 2. drop optional rooms
    for kind in OPTIONAL_ORDER:
        if has_slack(kept):
            break
        victims = [r for r in kept if r.type == kind and r.fixed is None]
        if not victims:
            continue
        victim = max(victims, key=lambda r: r.min_area)
        kept.remove(victim)
        notes.append(f"Dropped the {victim.name or kind} from this floor: without at least "
                     f"{100 * (1 - slack_ratio):.0f}% circulation slack the other rooms would be "
                     f"below their minimum recommended size ({minimum(kept):,.1f} m\u00b2 of "
                     f"minimum areas in {available_area:,.1f} m\u00b2).")

    # 3. an upper floor family living is nice, but never at the cost of a bedroom
    if not has_slack(kept):
        optional_living = [r for r in kept if r.tag == "optional_living"]
        if optional_living:
            kept.remove(optional_living[0])
            notes.append(f"Dropped the {optional_living[0].name} so the requested bedrooms still fit "
                         f"on this floor. You can add it back in the editor if you enlarge the floor.")

    # 4. last resort: reduce bedrooms and their attached bathrooms
    if not has_slack(kept):
        bedrooms = [r for r in kept if r.type in ("bedroom", "master_bedroom")]
        while len(bedrooms) > 1 and not has_slack(kept):
            victim = min(bedrooms, key=lambda r: r.min_area)
            kept.remove(victim)
            bedrooms.remove(victim)
            for bath in [r for r in kept if r.meta.get("attached_item") is victim]:
                kept.remove(bath)
            notes.append(f"Removed {victim.name or victim.type}: this floor cannot fit every requested "
                         f"room above its minimum size. Add a floor, enlarge the plot, or accept "
                         f"smaller rooms.")

    if not has_slack(kept):
        notes.append(f"Even after dropping optional rooms, the minimum areas need "
                     f"{minimum(kept):,.1f} m\u00b2 of the {available_area:,.1f} m\u00b2 available. "
                     f"Some rooms will be below the recommended minimum -- review them in the editor.")
    return kept, notes


def distribute_bedrooms(bedrooms: int, floors: int, concept: Concept, accessibility: bool,
                        ground_capacity: float = 0.0) -> List[int]:
    """How many bedrooms land on each floor (index 0 = ground).

    A ground floor bedroom is genuinely useful (elders, guests) but it costs a lot
    of area, so on a small footprint all bedrooms go upstairs unless accessibility
    needs say otherwise.
    """
    if floors <= 1:
        return [bedrooms]
    ground = 0
    if accessibility and bedrooms >= 1:
        ground = 1
    elif concept.ground_bedroom_bias > 0.6 and ground_capacity >= 78 and bedrooms >= 2:
        ground = 1
    elif bedrooms >= 4 and floors == 2 and ground_capacity >= 95:
        ground = 1
    remaining = bedrooms - ground
    per_upper = [0] * (floors - 1)
    for i in range(remaining):
        per_upper[i % (floors - 1)] += 1
    return [ground] + per_upper


def bathroom_program(bedrooms_on_floor: int, req_baths: int, req_attached: int, floor_index: int,
                     total_floors: int) -> Tuple[int, int]:
    """Returns (attached_baths, common_baths) for one floor."""
    attached = min(bedrooms_on_floor, req_attached) if bedrooms_on_floor else 0
    common = 1 if (floor_index == 0 or bedrooms_on_floor or total_floors == 1) else 0
    remaining_common = max(0, req_baths - req_attached)
    if floor_index == 0:
        common = max(common, min(remaining_common, 2))
    else:
        common = max(common, 1 if remaining_common > 1 else 0)
    return attached, common


def parking_rect(req_parking: str, footprint: Rect, road_side: str, band_depth: float) -> Optional[Rect]:
    """Place parking in the front band (setback area) facing the road."""
    pw, pd = PARKING_DIMS.get(req_parking, (0.0, 0.0))
    if pw <= 0 or pd <= 0 or band_depth < 1.0:
        return None
    fx, fy, fw, fd = footprint
    depth = min(pd, max(2.4, band_depth))
    if road_side == "S":
        x = fx + 0.3
        y = fy - depth - 0.2
        if y < 0:
            y = 0.0
        return (x, y, min(pw, fw - 0.6), depth)
    if road_side == "N":
        return (fx + 0.3, fy + fd + 0.2, min(pw, fw - 0.6), depth)
    if road_side == "E":
        return (fx + fw + 0.2, fy + 0.3, depth, min(pw, fd - 0.6))
    return (max(0.0, fx - depth - 0.2), fy + 0.3, depth, min(pw, fd - 0.6))


def footprint_rect(envelope: Rect, target_area: float, road_side: str, prefer_aspect: float = 1.45) -> Rect:
    """Choose the building footprint inside the buildable envelope."""
    ex, ey, ew, ed = envelope
    area = min(target_area, rect_area(envelope))
    if area <= 0:
        return envelope

    fw = (area * prefer_aspect) ** 0.5
    fw = min(ew, max(2.6, fw))
    fd = area / fw
    if fd > ed:
        fd = ed
        fw = min(ew, area / fd)
    fw = round(fw, 3)
    fd = round(fd, 3)

    if ew - fw < 1.2:
        fw, x = ew, ex
    else:
        x = ex + (ew - fw) / 2.0

    if road_side == "S":
        y = ey
    elif road_side == "N":
        y = ey + ed - fd
    elif road_side == "E":
        x, y = ex + ew - fw, ey
    else:
        x, y = ex, ey

    if road_side in ("E", "W") and ed - fd >= 1.2:
        y = ey + (ed - fd) / 2.0

    return (round(x, 3), round(y, 3), fw, fd)


def stair_rect_for(footprint: Rect, road_side: str, stair_w: float = 2.4, stair_d: float = 4.6,
                   front_offset: float = 0.0) -> Rect:
    """Put the stair core in the corner nearest the road.

    A corner placement means carving the stair out of the footprint leaves *one*
    service band instead of two narrow stubs, and it puts the staircase next to the
    entrance -- the normal arrangement for a duplex with independent upper access.
    """
    fx, fy, fw, fd = footprint
    stair_w = min(stair_w, fw * 0.45)
    stair_d = min(stair_d, fd * 0.55)
    front_offset = max(0.0, min(front_offset, (fd if road_side in ("N", "S") else fw) * 0.35))
    if road_side == "S":
        x, y = fx + fw - stair_w, fy + front_offset
    elif road_side == "N":
        x, y = fx + fw - stair_w, fy + fd - stair_d - front_offset
    elif road_side == "E":
        x, y = fx + fw - stair_d - front_offset, fy
    else:
        x, y = fx + front_offset, fy
    if road_side in ("E", "W"):
        return (round(x, 3), round(y, 3), round(stair_d, 3), round(stair_w, 3))
    return (round(x, 3), round(y, 3), round(stair_w, 3), round(stair_d, 3))


def courtyard_rect(footprint: Rect, road_side: str, ratio: float = 0.16) -> Rect:
    """Open-to-sky courtyard carved out of the footprint (traditional concept)."""
    fx, fy, fw, fd = footprint
    area = rect_area(footprint) * ratio
    side = max(2.2, min(area ** 0.5, min(fw, fd) * 0.45))
    if road_side in ("N", "S"):
        x = fx + (fw - side) / 2.0
        y = fy + (fd - side) * (0.35 if road_side == "S" else 0.65)
    else:
        x = fx + (fw - side) * (0.35 if road_side == "E" else 0.65)
        y = fy + (fd - side) / 2.0
    return (round(x, 3), round(y, 3), round(side, 3), round(side, 3))
