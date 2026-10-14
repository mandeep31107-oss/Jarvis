"""The planning engine: requirements -> structured, tiled floor plans.

Pipeline
--------
1. site      : setbacks -> buildable envelope (rectangles, L-shape aware)
2. programme : requirements + concept -> room requests with area targets
3. footprint : how much of the envelope to build on, and where
4. partition : cost driven guillotine split into rooms that tile the footprint
5. repair    : enforce minimum dimensions by moving shared walls
6. fittings  : doors, windows, circulation, furniture, stairs
7. reporting : metrics, assumptions and warnings
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..room_catalog import PARKING_DIMS, spec
from ...schemas import (Assumption, DesignMetrics, Floor, Plot, Requirements, Room,
                       Stair, new_id)
from .concepts import CONCEPTS, Concept, get_concept
from .openings import place_openings
from .furniture import furnish_floor
from .partition import (Item, Rect, carve, fits_inside, intersection_area, partition, r3,
                        rect_area, rects_overlap, repair_min_dims, snap_cells, snap_rect,
                        snap_rect_inward)
from .program import (distribute_bedrooms, fit_program, footprint_rect, lock_item_to_rect,
                      make_item, parking_rect, resolve_zone, stair_rect_for, courtyard_rect,
                      zone_target)
from .site import (bbox, decompose_polygon, largest_rect_in_polygon, offset_polygon,
                   polygon_area)

FLOOR_HEIGHT = 3.0
DEFAULT_FRONT_SETBACK = 2.5   # m, indicative only -- see assumptions
RISER = 0.175
TREAD = 0.27
STAIR_WIDTH = 1.0


@dataclass
class GenerationResult:
    floors: List[Floor]
    metrics: DesignMetrics
    assumptions: List[Assumption]
    notes: List[str]
    warnings: List[str]
    footprint: List[Rect]
    envelope: List[Rect]
    site_polygon: List[Tuple[float, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------

def build_site(plot: Plot, concept: Concept, req: Requirements, notes: List[str],
               assumptions: List[Assumption],
               parking_outdoor: bool = True) -> Tuple[List[Rect], List[Rect], List[Tuple[float, float]]]:
    """Return (envelope_rects, front_band_rects, site_polygon)."""
    road = plot.road_side
    park_w, park_d = PARKING_DIMS.get(req.wants_parking, (0.0, 0.0))

    front_need = max(plot.setbacks.for_side(road), DEFAULT_FRONT_SETBACK)
    if parking_outdoor and req.wants_parking != "none":
        front_need = max(front_need, park_d + 0.4)
    if concept.verandah:
        front_need = max(front_need, 2.2)
    if req.wants_garden:
        front_need = max(front_need, 2.0)
    rear_need = max(plot.setbacks.opposite(road), 0.9)
    side_need = max(plot.setbacks.east, plot.setbacks.west, 0.75)

    # Narrow plots: keep at least a usable width, otherwise the plan cannot work.
    if road in ("N", "S") and plot.width - 2 * side_need < 5.4:
        side_need = max(0.0, (plot.width - 5.4) / 2)
        notes.append(f"Side setbacks reduced to {side_need:.2f} m to keep a workable building width. "
                     f"Confirm the permitted setbacks with your local authority.")
    if road in ("E", "W") and plot.depth - 2 * side_need < 5.4:
        side_need = max(0.0, (plot.depth - 5.4) / 2)
        notes.append(f"Side setbacks reduced to {side_need:.2f} m to keep a workable building depth.")
    if plot.depth - front_need - rear_need < 4.0:
        front_need = max(1.0, plot.depth - rear_need - 4.0)
        notes.append("Front setback reduced so the house can be at least 4 m deep.")

    plot.apply_setbacks(front_need, rear_need, side_need)
    assumptions.append(Assumption(
        topic="setbacks",
        statement=(f"Assumed setbacks: front {front_need:.2f} m, rear {rear_need:.2f} m, "
                   f"sides {side_need:.2f} m (road on the {road} side). These are planning "
                   f"assumptions, not verified byelaw values."),
        severity="warning",
    ))

    if plot.boundary and len(plot.boundary) >= 3 and plot.shape in ("l_shape", "irregular", "custom"):
        pts = [(p.x, p.y) for p in plot.boundary]
        inset, mode = offset_polygon(pts, plot.setbacks)
        if mode == "rejected":
            notes.append("The requested setbacks leave no buildable area inside the drawn boundary; "
                         "the original boundary was used instead.")
            inset = pts
        if mode == "uniform":
            assumptions.append(Assumption(
                topic="boundary",
                statement="The plot boundary has diagonal edges, so setbacks were applied as a uniform "
                          "inward offset. Verify the exact buildable envelope.",
                severity="warning"))
        rects = decompose_polygon(inset, grid=0.25, max_rects=6, min_rect_area=3.0)
        if not rects:
            rects = [plot.buildable_rect()]
        # front band for parking / porch / garden on irregular plots
        band: Optional[Rect] = None
        bx0, by0, bw0, bd0 = bbox(pts)
        if road == "S":
            strip = (bx0, by0, bw0, min(front_need, bd0))
        elif road == "N":
            strip = (bx0, by0 + bd0 - min(front_need, bd0), bw0, min(front_need, bd0))
        elif road == "E":
            strip = (bx0 + bw0 - min(front_need, bw0), by0, min(front_need, bw0), bd0)
        else:
            strip = (bx0, by0, min(front_need, bw0), bd0)
        band = largest_rect_in_polygon(pts, strip, grid=0.25)
        if band is not None:
            band = snap_rect_inward(band)
            if rect_area(band) < 2.0:
                band = None
        return rects, ([band] if band else []), inset

    env = plot.buildable_rect()
    ex, ey, ew, ed = env

    # Front band (setback area) is split into parking / porch / garden later.
    if road == "S":
        band = (ex, max(0.0, ey - front_need), ew, min(front_need, ey))
    elif road == "N":
        band = (ex, ey + ed, ew, min(front_need, plot.depth - (ey + ed)))
    elif road == "E":
        band = (ex + ew, ey, min(front_need, plot.width - (ex + ew)), ed)
    else:
        band = (max(0.0, ex - front_need), ey, min(front_need, ex), ed)
    bands = [band] if rect_area(band) > 0.5 else []
    return [env], bands, [(0.0, 0.0), (plot.width, 0.0), (plot.width, plot.depth), (0.0, plot.depth)]


# ---------------------------------------------------------------------------
# Programme
# ---------------------------------------------------------------------------

def _scale_targets(items: List[Item], area: float) -> None:
    if not items or area <= 0:
        return
    items = [i for i in items if i.fixed is None]
    if not items:
        return
    for _ in range(8):
        total = sum(i.target for i in items)
        if total <= 0:
            return
        factor = area / total
        capped = False
        for it in items:
            new = it.target * factor
            if new > it.max_area:
                it.target = it.max_area
                capped = True
            elif new < it.min_area:
                it.target = it.min_area
            else:
                it.target = new
        if not capped or abs(sum(i.target for i in items) - area) < area * 0.02:
            break


def build_programme(req: Requirements, concept: Concept, envelope: Rect, road: str,
                    stair_rect: Optional[Rect] = None, ground_area: float = 0.0,
                    indoor_parking: bool = False,
                    parking_fixed: Optional[Rect] = None,
                    skip_types: Optional[set] = None) -> Dict[int, List[Item]]:
    """Room requests per floor index."""
    skip = skip_types or set()
    floors = max(1, req.floors)
    bedroom_counts = distribute_bedrooms(req.bedrooms, floors, concept, req.accessibility,
                                         ground_capacity=ground_area)
    out: Dict[int, List[Item]] = {}

    def zone(kind: str) -> str:
        return resolve_zone(kind, road, req.vastu)

    g = concept.generosity
    for fi in range(floors):
        items: List[Item] = []
        beds = bedroom_counts[fi] if fi < len(bedroom_counts) else 0
        master_here = (fi == 0 and floors == 1) or (fi == 1 and floors > 1) or (fi == 0 and beds > 0 and floors > 1 and bedroom_counts[0] > 0)
        attached, common = _bathrooms(beds, req, fi, floors)

        if fi == 0:
            if concept.verandah:
                pass  # porch is outdoor, added separately
            if concept.corridor and not concept.open_plan and ground_area >= 82:
                items.append(make_item("corridor", "Corridor", zone("corridor"), envelope, g,
                                       area_scale=0.8, road_side=road))
            if "lobby" not in skip:
                items.append(make_item("lobby", "Entrance Lobby", zone("lobby"), envelope, g,
                                       weight=1.4, area_scale=0.75, road_side=road))
            if concept.open_plan and req.wants_dining:
                items.append(make_item("living", "Living + Dining", zone("living"), envelope, g,
                                       weight=1.6, area_scale=1.35, tag="open_plan", road_side=road))
            else:
                if req.wants_living:
                    items.append(make_item("living", "Living Room", zone("living"), envelope, g, weight=1.5, road_side=road))
                if req.wants_dining:
                    items.append(make_item("dining", "Dining Room", zone("dining"), envelope, g, road_side=road))
            items.append(make_item("kitchen", "Kitchen", zone("kitchen"), envelope, g, weight=1.3, road_side=road))
            if (indoor_parking and req.wants_parking != "none" and parking_fixed is not None
                    and "parking" not in skip):
                park = make_item("parking", "Covered Parking", zone("parking"), envelope, g,
                                 weight=2.0, fixed=parking_fixed, road_side=road)
                lock_item_to_rect(park)
                park.min_dim = min(parking_fixed[2], parking_fixed[3]) * 0.98
                park.max_aspect = 3.2
                park.outdoor = False
                items.append(park)
            if req.wants_utility:
                items.append(make_item("utility", "Utility / Wash Area", zone("utility"), envelope, g,
                                       area_scale=0.8, road_side=road))
            if req.wants_pooja:
                items.append(make_item("puja", "Prayer Room", zone("puja"), envelope, g, road_side=road))
            if req.wants_study and floors == 1:
                items.append(make_item("study", "Study / Home Office", zone("study"), envelope, g, road_side=road))
            if req.wants_store and floors == 1:
                items.append(make_item("store", "Store Room", zone("store"), envelope, g, road_side=road))
            if req.wants_servant_room:
                items.append(make_item("servant_room", "Servant Room", zone("servant_room"), envelope, g, road_side=road))
            if req.wants_shop:
                items.append(make_item("shop", "Shop / Workspace", zone("shop"), envelope, g, weight=1.4, road_side=road))
        else:
            if concept.key in ("family_comfort", "modern_contemporary") and beds >= 2:
                items.append(make_item("living", "Family Living", zone("living"), envelope, g,
                                       weight=1.2, area_scale=0.8, road_side=road,
                                       tag="optional_living"))
            if req.wants_study and fi == 1 and beds <= 2:
                items.append(make_item("study", "Study / Home Office", zone("study"), envelope, g, road_side=road))
            if req.wants_store and fi == 1:
                items.append(make_item("store", "Store Room", zone("store"), envelope, g, area_scale=0.8, road_side=road))

        # bedrooms on this floor
        for b in range(beds):
            is_master = (b == 0 and master_here) or (fi == 0 and floors == 1 and b == 0)
            kind = "master_bedroom" if is_master else "bedroom"
            if is_master:
                name = "Master Bedroom"
            elif fi == 0:
                name = f"Bedroom {b + 1}"
            else:
                name = f"Bedroom {fi + 1}.{b + 1}"
            item = make_item(kind, name, zone(kind), envelope, g,
                                             weight=1.25 if is_master else 1.0, road_side=road)
            items.append(item)
            if b < attached:
                bath = make_item("bathroom", f"Attached Bathroom ({name})", zone("bathroom"), envelope, g,
                                 area_scale=0.75, road_side=road)
                bath.meta["attached_item"] = item
                # keep the attached bathroom next to its bedroom
                bath.tx, bath.ty = item.tx + 0.35, item.ty + 0.15
                bath.weight *= 1.5
                items.append(bath)

        for c in range(common):
            kind = "bathroom" if (c == 0 and (fi == 0 or req.bathrooms <= 2)) else "toilet"
            label = "Common Bathroom" if kind == "bathroom" else "Toilet"
            if fi > 0:
                label = f"{label} (Floor {fi + 1})"
            items.append(make_item(kind, label, zone(kind), envelope, g, area_scale=0.8, road_side=road))

        if stair_rect is not None and "stairs" not in skip:
            items.append(lock_item_to_rect(
                make_item("stairs", "Staircase", zone("stairs"), envelope, g,
                          fixed=stair_rect, weight=2.0, road_side=road)))
        out[fi] = items

    for items in out.values():
        kept = set(id(i) for i in items)
        for item in items:
            parent = item.meta.get("attached_item")
            if parent is not None and id(parent) not in kept:
                item.meta.pop("attached_item", None)
    return out


def _bathrooms(beds: int, req: Requirements, floor_index: int, floors: int) -> Tuple[int, int]:
    attached_budget = max(0, req.attached_baths)
    if floors == 1:
        attached = min(beds, attached_budget)
    else:
        attached = min(beds, max(0, attached_budget - (0 if floor_index == 0 else 0)))
        attached = min(attached, attached_budget)
    common_total = max(0, req.bathrooms - req.attached_baths)
    if floors == 1:
        common = max(1, common_total) if req.bathrooms > 0 else 0
    else:
        common = max(1, math.ceil(common_total / floors)) if (common_total or floor_index == 0) else 1
        if common_total == 0:
            common = 1 if floor_index == 0 else 0
    return attached, min(common, 3)


# ---------------------------------------------------------------------------
# Partitioning one floor
# ---------------------------------------------------------------------------

def _rescale_group(items: List[Item], area: float) -> None:
    if not items or area <= 0:
        return
    free = [i for i in items if i.fixed is None]
    if not free:
        return
    fixed_area = sum(rect_area(i.fixed) for i in items if i.fixed is not None)
    budget = max(1.0, area - fixed_area)
    total = sum(i.target for i in free)
    if total <= 0:
        return
    factor = budget / total
    for it in free:
        it.target = min(max(it.target * factor, it.min_area), max(it.max_area, it.min_area * 1.05))


def plan_floor(items: List[Item], regions: List[Rect], holes: List[Rect],
               diag: float) -> Tuple[List[Tuple[Rect, Item]], List[str]]:
    from .program import make_item  # local import keeps the module graph acyclic
    notes: List[str] = []
    pieces: List[Rect] = []
    for region in regions:
        current = [region]
        for hole in holes:
            nxt: List[Rect] = []
            for piece in current:
                nxt.extend(carve(piece, hole))
            current = nxt
        pieces.extend(current)
    pieces = [p for p in pieces if rect_area(p) > 0.5]
    if not pieces:
        return [], ["No buildable area left after applying setbacks and reserved spaces."]

    assignment: Dict[int, List[Item]] = {i: [] for i in range(len(pieces))}

    def score_piece(item: Item, idx: int, piece: Rect) -> float:
        cx, cy = piece[0] + piece[2] / 2, piece[1] + piece[3] / 2
        dist = math.hypot(cx - item.tx, cy - item.ty)
        used = sum(i.target for i in assignment[idx])
        capacity = rect_area(piece)
        overflow = max(0.0, (used + item.target) - capacity) / max(capacity, 1.0)
        short = min(piece[2], piece[3])
        infeasible = max(0.0, item.min_dim - short) * 26.0
        if item.fixed is None:
            infeasible += max(0.0, item.min_area - capacity) * 12.0
        score = dist / max(diag, 1e-6) + 9.0 * overflow + infeasible
        if item.fixed is not None:
            from .partition import intersection_area
            iou = intersection_area(piece, item.fixed) / max(rect_area(item.fixed), 1e-6)
            score -= 6.0 * iou
        return score

    for item in sorted(items, key=lambda it: -it.target):
        best_idx = min(range(len(pieces)), key=lambda idx: score_piece(item, idx, pieces[idx]))
        assignment[best_idx].append(item)

    # rebalance: move a room into an empty piece only when it actually fits there
    def fits_piece(item: Item, piece: Rect) -> bool:
        return (item.fixed is not None
                or (item.min_area <= rect_area(piece)
                    and item.min_dim <= min(piece[2], piece[3]) + 0.02))

    for _ in range(6):
        empties = [i for i in assignment if not assignment[i]]
        if not empties:
            break
        moved = False
        for e in empties:
            cx, cy = pieces[e][0] + pieces[e][2] / 2, pieces[e][1] + pieces[e][3] / 2
            candidates = []
            for j, its in assignment.items():
                if j == e or len(its) < 2:
                    continue
                for it in its:
                    if fits_piece(it, pieces[e]):
                        candidates.append((math.hypot(cx - it.tx, cy - it.ty), j, it))
            if not candidates:
                continue
            candidates.sort(key=lambda c: c[0])
            _, j, victim = candidates[0]
            assignment[j].remove(victim)
            assignment[e].append(victim)
            moved = True
            break
        if not moved:
            break

    # Slivers (leftover pieces too narrow to be a room) are merged into an adjacent
    # assigned piece so the plan does not sprout 40 cm wide "store rooms".
    def _clean_union(a: Rect, b: Rect) -> bool:
        if abs(a[0] - b[0]) < 1e-6 and abs(a[2] - b[2]) < 1e-6:
            return abs(a[1] + a[3] - b[1]) < 1e-6 or abs(b[1] + b[3] - a[1]) < 1e-6
        if abs(a[1] - b[1]) < 1e-6 and abs(a[3] - b[3]) < 1e-6:
            return abs(a[0] + a[2] - b[0]) < 1e-6 or abs(b[0] + b[2] - a[0]) < 1e-6
        return False

    for _pass in range(3):
        merged = False
        for idx, piece in enumerate(list(pieces)):
            if assignment.get(idx) or rect_area(piece) <= 0:
                continue
            if min(piece[2], piece[3]) >= 0.9 and rect_area(piece) >= 1.8:
                continue
            for j, other in enumerate(pieces):
                if j == idx or not assignment.get(j) or rect_area(other) <= 0:
                    continue
                if not _clean_union(piece, other):
                    continue
                pieces[j] = (min(piece[0], other[0]), min(piece[1], other[1]),
                             max(piece[0] + piece[2], other[0] + other[2]) - min(piece[0], other[0]),
                             max(piece[1] + piece[3], other[1] + other[3]) - min(piece[1], other[1]))
                pieces[idx] = (0.0, 0.0, 0.0, 0.0)
                assignment[idx] = []
                merged = True
                break
            if merged:
                break
        if not merged:
            break

    # a leftover piece becomes useful ancillary space instead of a hole in the plan
    for idx, piece in enumerate(list(pieces)):
        if assignment.get(idx):
            continue
        area = rect_area(piece)
        if area < 1.2 or min(piece[2], piece[3]) < 0.9:
            continue
        kind = "store" if area < 4.0 else ("corridor" if area < 9.0 else "store")
        name = "Store Room" if kind == "store" else "Circulation / Landing"
        filler = make_item(kind, name, "hint", piece, 1.0, road_side="S")
        filler.tx, filler.ty = piece[0] + piece[2] / 2, piece[1] + piece[3] / 2
        filler.target = area
        filler.min_area = min(filler.min_area, area * 0.9)
        filler.max_area = max(area * 1.05, filler.max_area)
        filler.min_dim = min(filler.min_dim, min(piece[2], piece[3]) * 0.95)
        filler.meta["auto_added"] = True
        assignment[idx] = [filler]
        notes.append(f"Added a {name.lower()} ({area:,.1f} m\u00b2) to use the leftover space next to "
                     f"the stair core. Rename or resize it in the editor if you prefer something else.")

    pieces = [p for i, p in enumerate(pieces) if assignment.get(i)]
    assignment = {i: v for i, v in enumerate([assignment[k] for k in sorted(assignment) if assignment[k]])}

    cells: List[Tuple[Rect, Item]] = []
    for idx, piece in enumerate(pieces):
        group = assignment.get(idx, [])
        if not group:
            continue
        # rescale to the piece so a room never starves because its neighbour was
        # handed a bigger slice of the footprint
        _rescale_group(group, rect_area(piece))
        cells.extend(partition(piece, group, diag))

    # Reserved geometry (stair core, lobby, parking bay, courtyard, terrace) takes
    # part in the wall-moving passes as an immovable blocker, otherwise a room can
    # be widened straight into the staircase.
    blockers: List[Tuple[Rect, Item]] = []
    for hole in holes:
        blockers.append((hole, Item(type="_reserved", target=rect_area(hole), min_area=0.0,
                                    max_area=1e9, min_dim=0.05, max_aspect=99.0,
                                    tx=hole[0], ty=hole[1], fixed=hole, name="reserved",
                                    weight=0.0)))
    all_cells = cells + blockers
    notes.extend(repair_min_dims(all_cells))
    all_cells = snap_cells(all_cells, list(holes))
    cells = [(rect, item) for rect, item in all_cells if item.type != "_reserved"]
    return cells, notes


# ---------------------------------------------------------------------------
# Stairs
# ---------------------------------------------------------------------------

def stair_geometry(height: float = FLOOR_HEIGHT) -> Dict[str, float]:
    """Dog-legged (two flight) stair sized for the floor height."""
    risers = max(6, math.ceil(height / RISER))
    rise = height / risers
    per_flight = math.ceil(risers / 2)
    run = (per_flight - 1) * TREAD
    width = 2 * STAIR_WIDTH + 0.2
    depth = run + STAIR_WIDTH + 0.3
    return {
        "risers": risers, "rise": round(rise, 4), "tread": TREAD,
        "per_flight": per_flight, "run": round(run, 3),
        "width": round(width, 3), "depth": round(depth, 3),
        "flight_width": STAIR_WIDTH, "landing": STAIR_WIDTH,
    }


def make_stair(floor: Floor, rect: Rect, goes_up: bool = True, room_id: Optional[str] = None) -> Stair:
    sg = stair_geometry(floor.height)
    return Stair(
        id=new_id("st"), floor_id=floor.id, room_id=room_id,
        x=r3(rect[0]), y=r3(rect[1]), w=r3(rect[2]), d=r3(rect[3]),
        direction="N", flights=2, rise_m=sg["rise"], tread_m=sg["tread"],
        risers=int(sg["risers"]), width_m=sg["flight_width"], landing_m=sg["landing"],
        goes_up=goes_up,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_house(plot: Plot, req: Requirements, concept_key: str = "family_comfort") -> GenerationResult:
    concept = get_concept(concept_key)
    notes: List[str] = []
    warnings: List[str] = []
    assumptions: List[Assumption] = []

    plot = plot.model_copy(deep=True)
    road = plot.road_side
    floors_count = max(1, req.floors)
    stairs_needed = floors_count > 1
    wants_roof_terrace = bool(req.wants_terrace)
    park_w, park_d = PARKING_DIMS.get(req.wants_parking, (0.0, 0.0))

    # ---- decide where the parking goes ------------------------------------
    # Parking in the front setback is nicer, but on a small plot it can leave too
    # little buildable area. In that case the car park becomes a covered room
    # inside the footprint (very common on Indian urban plots).
    parking_outdoor = False
    indoor_parking = False
    if req.wants_parking != "none":
        # Would the house still work if the front setback absorbed the car?
        probe = plot.model_copy(deep=True)
        probe_env, _, _ = build_site(probe, concept, req, [], [], parking_outdoor=True)
        probe_area = sum(rect_area(r) for r in probe_env)
        probe_items = build_programme(req, concept, max(probe_env, key=rect_area), road, None,
                                      ground_area=probe_area)[0]
        probe_min = sum(i.min_area for i in probe_items)
        if stairs_needed:
            _sg = stair_geometry(FLOOR_HEIGHT)
            probe_min += _sg["width"] * _sg["depth"]
        parking_outdoor = probe_area >= 22.0 and probe_area * 0.95 >= probe_min
        if not parking_outdoor:
            indoor_parking = True
            notes.append("Open parking does not fit in the front setback without making the house too "
                         "small, so the parking is shown as covered parking inside the ground floor. "
                         "Confirm whether your byelaws count it towards permissible coverage.")

    envelope_rects, front_bands, site_poly = build_site(plot, concept, req, notes, assumptions,
                                                       parking_outdoor)
    env_area = sum(rect_area(r) for r in envelope_rects)
    envelope = max(envelope_rects, key=rect_area)
    diag = math.hypot(plot.width, plot.depth)

    if env_area < 14.0:
        warnings.append(
            f"After setbacks only {env_area:,.1f} m\u00b2 of the plot is buildable. "
            f"Reduce setbacks, add floors, or reconsider the plot size."
        )

    # ---- programme + footprint sizing -------------------------------------
    provisional = build_programme(req, concept, envelope, road, None, ground_area=env_area,
                                  indoor_parking=False)[0]
    if indoor_parking:
        provisional.append(make_item("parking", "Covered Parking", "hint", envelope, 1.0,
                                     road_side=road))
    target_total = sum(i.target for i in provisional)
    min_total = sum(i.min_area for i in provisional)

    cap = env_area * min(0.94, concept.coverage + 0.12)
    footprint_area = min(cap, max(target_total, min_total * 1.02))
    footprint_area = max(footprint_area, min(min_total, env_area))

    if min_total > env_area:
        warnings.append(
            f"The minimum areas of the requested rooms need {min_total:,.1f} m\u00b2 but only "
            f"{env_area:,.1f} m\u00b2 is buildable on this plot. Add a floor, drop rooms, or accept "
            f"below-recommendation room sizes."
        )
    elif min_total > footprint_area * 0.98:
        notes.append("Rooms are at or near their minimum recommended size on this plot. "
                     "Adding a floor would give a more comfortable home.")
    if target_total < env_area * concept.coverage * 0.55 and floors_count == 1:
        notes.append(f"The requested programme only needs about {target_total:,.0f} m\u00b2, so the "
                     f"footprint was kept compact and the rest of the plot stays as open space.")

    # ---- footprint rectangles ---------------------------------------------
    if len(envelope_rects) == 1:
        footprint_rects = [snap_rect_inward(footprint_rect(envelope, footprint_area, road))]
    else:
        ordered = sorted(envelope_rects, key=rect_area, reverse=True)
        chosen: List[Rect] = []
        acc = 0.0
        for rect in ordered:
            if acc >= footprint_area * 0.98:
                break
            chosen.append(rect)
            acc += rect_area(rect)
        footprint_rects = [snap_rect_inward(r) for r in (chosen or [ordered[0]])]
        footprint_area = sum(rect_area(r) for r in footprint_rects)

    sg = stair_geometry(FLOOR_HEIGHT)
    fp0 = footprint_rects[0]

    # ---- reserved geometry: stair core, entrance lobby, parking bay --------
    # These are positioned analytically. A stair core, an entrance and a parking
    # bay all have hard dimensional requirements, so the planner reserves them
    # instead of hoping the space partition lands on a sensible shape.
    wants_lobby = footprint_area >= 40
    lobby_depth = 2.2 if wants_lobby else 0.0
    stair_size = (sg["width"], sg["depth"]) if stairs_needed else None
    zone = front_zone_rects(fp0, road, stair_size, lobby_depth,
                            req.wants_parking if indoor_parking else "none")
    stair_rect = zone.get("stairs")
    lobby_rect = zone.get("lobby")
    if stair_rect is not None and rect_area(stair_rect) < 5.0:
        notes.append("The stair core is tight for this footprint. A steeper stair or a winder flight "
                     "may be needed, which requires professional design.")
    if wants_lobby and lobby_rect is None:
        notes.append("No space for a separate entrance lobby; the entrance opens straight into the "
                     "living area.")

    parking_rect_indoor = zone.get("parking")
    if indoor_parking and parking_rect_indoor is None:
        indoor_parking = False
        warnings.append("A covered parking bay does not fit inside this footprint without blocking the "
                        "entrance or the stair. Parking was left out -- park in the setback (if "
                        "permitted), widen the frontage, or plan a stilt floor.")
    if road in ("E", "W") and (stair_rect is not None or lobby_rect is not None):
        notes.append("For an east/west facing plot the entrance and stair are placed at the far end of "
                     "the road frontage, with the driveway running along the side. Check this matches "
                     "how you will actually approach the house.")

    reserved_upper: List[Tuple[Rect, str, str]] = []
    if stair_rect is not None:
        reserved_upper.append((stair_rect, "stairs", "Staircase"))
    reserved_ground: List[Tuple[Rect, str, str]] = list(reserved_upper)
    if parking_rect_indoor is not None:
        reserved_ground.append((parking_rect_indoor, "parking", "Covered Parking"))
    if lobby_rect is not None:
        reserved_ground.append((lobby_rect, "lobby", "Entrance Lobby"))

    programme = build_programme(req, concept, envelope, road, None, ground_area=footprint_area,
                                skip_types={k for _, k, _ in reserved_ground})

    floors: List[Floor] = []
    ground_stair_rect: Optional[Rect] = stair_rect

    for fi in range(floors_count):
        items = list(programme.get(fi, []))
        floor = Floor(id=new_id("fl"), index=fi,
                      name="Ground Floor" if fi == 0 else f"Floor {fi}",
                      level=r3(fi * FLOOR_HEIGHT), height=FLOOR_HEIGHT)

        reserved = [(rect, kind, name) for rect, kind, name in (reserved_ground if fi == 0 else reserved_upper)
                    if any(_rect_in(footprint_rects, rect) or fits_inside(rect, fp) for fp in footprint_rects)]
        holes: List[Rect] = [rect for rect, _, _ in reserved]
        reserved_types = {kind for _, kind, _ in reserved}
        items = [i for i in items if i.type not in reserved_types]

        available = max(4.0, footprint_area - sum(rect_area(h) for h in holes))
        _scale_targets(items, available)
        items, fit_notes = fit_program(items, available)
        notes.extend((f"Floor {fi + 1}: {n}" if fi else n) for n in fit_notes)

        cyard: Optional[Rect] = None
        if concept.courtyard and fi == 0 and available > 45:
            cyard = snap_rect_inward(courtyard_rect(fp0, road, ratio=0.14))
            if not any(rects_overlap(cyard, h) for h in holes):
                holes.append(cyard)
            else:
                cyard = None

        terrace_hole: Optional[Rect] = None
        need = sum(i.target for i in items)
        if fi > 0 and need < available * 0.82:
            deficit = available - min(need * 1.12, available * 0.95)
            candidate = _terrace_band(footprint_rects, road, deficit)
            if candidate is not None:
                candidate = snap_rect(candidate)
            if candidate and rect_area(candidate) > 4.0 and not any(rects_overlap(candidate, h) for h in holes):
                terrace_hole = candidate
                holes.append(candidate)

        cells, cell_notes = plan_floor(items, footprint_rects, holes, diag)
        notes.extend(cell_notes)
        notes.extend(_fix_attached(cells))

        room_of_item: Dict[int, Room] = {}
        for rect, item in cells:
            sp = spec(item.type)
            room = Room(id=new_id("rm"), floor_id=floor.id, name=item.name or sp.label,
                        type=item.type, x=r3(rect[0]), y=r3(rect[1]), w=r3(rect[2]), d=r3(rect[3]),
                        ceiling_height=FLOOR_HEIGHT - 0.15, is_outdoor=sp.outdoor, is_wet=sp.wet)
            room_of_item[id(item)] = room
            floor.rooms.append(room)

        for rect, item in cells:
            parent = item.meta.get("attached_item")
            if parent is None:
                continue
            child = room_of_item.get(id(item))
            parent_room = room_of_item.get(id(parent))
            if child is not None and parent_room is not None:
                child.tags.append(f"attached_to:{parent_room.id}")

        for rect, kind, name in reserved:
            sp = spec(kind)
            tags = ["reserved"]
            if kind == "stairs":
                tags.append("stair_core")
            if kind == "lobby":
                tags.append("entrance_zone")
            room = Room(id=new_id("rm"), floor_id=floor.id, name=name, type=kind,
                        x=r3(rect[0]), y=r3(rect[1]), w=r3(rect[2]), d=r3(rect[3]),
                        ceiling_height=FLOOR_HEIGHT if kind != "parking" else FLOOR_HEIGHT - 0.15,
                        is_outdoor=sp.outdoor and kind != "parking", tags=tags)
            floor.rooms.append(room)
            if kind == "stairs":
                floor.stairs.append(make_stair(floor, rect, goes_up=True, room_id=room.id))

        if cyard is not None:
            floor.rooms.append(Room(id=new_id("rm"), floor_id=floor.id, name="Courtyard",
                                     type="courtyard", x=r3(cyard[0]), y=r3(cyard[1]),
                                     w=r3(cyard[2]), d=r3(cyard[3]), is_outdoor=True))
        if terrace_hole is not None:
            floor.rooms.append(Room(id=new_id("rm"), floor_id=floor.id, name="Terrace (open to sky)",
                                     type="terrace", x=r3(terrace_hole[0]), y=r3(terrace_hole[1]),
                                     w=r3(terrace_hole[2]), d=r3(terrace_hole[3]), is_outdoor=True))
        floors.append(floor)

    # ---- outdoor rooms in the front band (ground floor) --------------------
    ground = floors[0]
    band = front_bands[0] if front_bands else None
    if band is not None and rect_area(band) > 1.0:
        bx, by, bw, bd = band
        cursor = bx if road in ("N", "S") else by
        if parking_outdoor:
            depth = min(park_d, bd) if road in ("N", "S") else min(park_d, bw)
            width = min(park_w, bw) if road in ("N", "S") else min(park_w, bd)
            if road in ("N", "S"):
                rect = (cursor, by, width, depth)
                cursor += width + 0.3
            elif road == "E":
                rect = (bx + bw - depth, cursor, depth, width)
                cursor += width + 0.3
            else:
                rect = (bx, cursor, depth, width)
                cursor += width + 0.3
            rect = snap_rect(rect)
            ground.rooms.append(Room(id=new_id("rm"), floor_id=ground.id,
                                     name="Car Parking" if "car" in req.wants_parking else "Two Wheeler Parking",
                                     type="parking", x=rect[0], y=rect[1], w=rect[2],
                                     d=rect[3], is_outdoor=True))
        def _outdoor(kind: str, name: str, rect: Rect) -> None:
            rect = snap_rect(rect)
            ground.rooms.append(Room(id=new_id("rm"), floor_id=ground.id, name=name, type=kind,
                                     x=rect[0], y=rect[1], w=rect[2], d=rect[3],
                                     ceiling_height=0.0, is_outdoor=True, tags=["outdoor", "site"]))

        if concept.verandah:
            left = (bw - (cursor - bx)) if road in ("N", "S") else (bd - (cursor - by))
            width = min(3.0, left)
            if width > 2.0:
                if road in ("N", "S"):
                    rect = (cursor, by + (bd - min(2.0, bd)) / 2, width, min(2.0, bd))
                    cursor += width + 0.3
                elif road == "E":
                    rect = (bx + bw - min(2.0, bw), cursor, min(2.0, bw), width)
                    cursor += width + 0.3
                else:
                    rect = (bx, cursor, min(2.0, bw), width)
                    cursor += width + 0.3
                _outdoor("porch", "Porch / Verandah", rect)
        if req.wants_garden:
            if road in ("N", "S"):
                leftover = min(bw - (cursor - bx), 6.0)
                if leftover > 1.8:
                    _outdoor("garden", "Front Garden", (cursor, by, leftover, bd))
            else:
                leftover = min(bd - (cursor - by), 6.0)
                if leftover > 1.8:
                    _outdoor("garden", "Front Garden", (bx, cursor, bw, leftover))

    # ---- balconies on upper floors ----------------------------------------
    for fi in range(1, len(floors)):
        floor = floors[fi]
        if not req.wants_balcony or band is None:
            continue
        bx, by, bw, bd = band
        host = _best_balcony_host(floor, road)
        if host is None:
            continue
        depth = min(1.5, max(1.2, bd if road in ("N", "S") else bw))
        width = min(3.4, max(1.6, (host.w if road in ("N", "S") else host.d) * 0.8))
        if road == "S":
            x = host.x + max(0.0, (host.w - width) / 2)
            y = fp0[1] - depth
        elif road == "N":
            x = host.x + max(0.0, (host.w - width) / 2)
            y = fp0[1] + fp0[3]
        elif road == "E":
            x, y, width, depth = fp0[0] + fp0[2], host.y + max(0.0, (host.d - width) / 2), depth, width
        else:
            x, y, width, depth = fp0[0] - depth, host.y + max(0.0, (host.d - width) / 2), depth, width
        x = min(max(x, 0.05), plot.width - width - 0.05)
        y = min(max(y, 0.05), plot.depth - depth - 0.05)
        rect = (r3(x), r3(y), r3(width), r3(depth))
        clash = any(rects_overlap(rect, (r.x, r.y, r.w, r.d), tol=-0.02) for r in floor.rooms)
        if clash or rect_area(rect) < 2.0:
            notes.append("The balcony would overlap another space or fall outside the plot, so it was "
                         "left out of this floor. You can add one manually in the editor.")
            continue
        floor.rooms.append(Room(id=new_id("rm"), floor_id=floor.id, name=f"Balcony (Floor {fi + 1})",
                                 type="balcony", x=rect[0], y=rect[1], w=rect[2], d=rect[3],
                                 is_outdoor=True, tags=[f"attached_to:{host.id}"]))

    # ---- roof terrace ------------------------------------------------------
    if wants_roof_terrace:
        level = r3(len([f for f in floors if not f.is_terrace]) * FLOOR_HEIGHT)
        terrace_floor = Floor(id=new_id("fl"), index=len(floors), name="Terrace",
                              level=level, height=1.15, slab_thickness=0.15, is_terrace=True)
        fp = footprint_rects[0]
        stair_room = None
        pieces = [fp]
        if ground_stair_rect is not None:
            pieces = carve(fp, ground_stair_rect)
            stair_room = Room(id=new_id("rm"), floor_id=terrace_floor.id, name="Stair Headroom",
                              type="stairs", x=r3(ground_stair_rect[0]), y=r3(ground_stair_rect[1]),
                              w=r3(ground_stair_rect[2]), d=r3(ground_stair_rect[3]))
            terrace_floor.rooms.append(stair_room)
            terrace_floor.stairs.append(make_stair(terrace_floor, ground_stair_rect, goes_up=False,
                                                   room_id=stair_room.id))
        pieces = sorted([p for p in pieces if rect_area(p) > 1.5], key=rect_area, reverse=True)
        for idx, piece in enumerate(pieces):
            terrace_floor.rooms.append(Room(
                id=new_id("rm"), floor_id=terrace_floor.id,
                name="Roof Terrace" if idx == 0 else f"Terrace Area {idx + 1}", type="terrace",
                x=r3(piece[0]), y=r3(piece[1]), w=r3(piece[2]), d=r3(piece[3]), is_outdoor=True))
        floors.append(terrace_floor)

    # ---- doors, windows, furniture ----------------------------------------
    for floor in floors:
        if floor.is_terrace:
            continue
        notes.extend(place_openings([floor], plot, concept, req))
    notes.extend(_outdoor_openings(floors, plot))
    for floor in floors:
        if floor.is_terrace:
            continue
        notes.extend(furnish_floor(floor, concept, req))

    metrics = compute_metrics(plot, floors, envelope_rects, footprint_rects, req)
    assumptions.extend(_standard_assumptions(plot, req, concept, sg))
    if req.vastu:
        assumptions.append(Assumption(
            topic="vastu",
            statement="Vastu preferences were used as soft placement targets (kitchen to the "
                      "south-east, master bedroom to the south-west, prayer space to the "
                      "north-east, toilets to the north-west). Where the plot shape or the room "
                      "programme made an ideal zone impossible, the planner chose the closest "
                      "workable position. Vastu guidance is cultural preference, not an "
                      "engineering, safety or building-code requirement.",
            severity="info"))

    return GenerationResult(floors=floors, metrics=metrics, assumptions=assumptions,
                            notes=_dedupe(notes), warnings=_dedupe(warnings),
                            footprint=footprint_rects, envelope=envelope_rects,
                            site_polygon=site_poly)


def _fix_attached(cells: List[Tuple[Rect, Item]]) -> List[str]:
    """Make sure an attached bathroom actually touches its bedroom.

    If the partition put them apart, we swap the bathroom with a room that does
    touch the bedroom -- but only when both rooms still fit their new cells.
    """
    from .partition import shared_edge
    notes: List[str] = []
    by_item = {id(item): (rect, item) for rect, item in cells}
    for idx, (rect, item) in enumerate(cells):
        parent = item.meta.get("attached_item")
        if parent is None or id(parent) not in by_item:
            continue
        parent_rect = by_item[id(parent)][0]
        if shared_edge(rect, parent_rect):
            continue
        for jdx, (other_rect, other_item) in enumerate(cells):
            if jdx == idx or other_item.meta.get("attached_item") is not None:
                continue
            if other_item.fixed is not None:
                continue
            if not shared_edge(other_rect, parent_rect):
                continue
            # would both rooms still work after swapping?
            if not _room_fits_cell(item, other_rect) or not _room_fits_cell(other_item, rect):
                continue
            cells[idx] = (other_rect, item)
            cells[jdx] = (rect, other_item)
            by_item[id(item)] = cells[idx]
            by_item[id(other_item)] = cells[jdx]
            notes.append(f"Swapped {item.name} with {other_item.name} so the bathroom opens "
                         f"directly off its bedroom.")
            break
        else:
            notes.append(f"{item.name} could not be placed next to {parent.name or 'its bedroom'} "
                         f"in this layout; it will be entered from another space.")
    return notes


def _room_fits_cell(item: Item, rect: Rect) -> bool:
    area = rect_area(rect)
    if area < item.min_area * 0.85 or area > item.max_area * 1.8:
        return False
    if min(rect[2], rect[3]) < item.min_dim * 0.85:
        return False
    return True


def _front_place(road: str, fp: Rect, t: float, u: float, t_ext: float, u_ext: float) -> Rect:
    """Convert frontage coordinates to a plan rectangle.

    ``t`` runs along the road frontage from its start (west for a south road, east
    for a north road, south for an east road, north for a west road); ``u`` runs
    into the plot away from the road edge.
    """
    fx, fy, fw, fd = fp
    if road == "S":
        return (fx + t, fy + u, t_ext, u_ext)
    if road == "N":
        return (fx + fw - t - t_ext, fy + fd - u - u_ext, t_ext, u_ext)
    if road == "E":
        return (fx + fw - u - u_ext, fy + t, u_ext, t_ext)
    return (fx + u, fy + fd - t - t_ext, u_ext, t_ext)


def front_zone_rects(footprint: Rect, road: str, stair_size: Optional[Tuple[float, float]],
                     lobby_depth: float, parking_kind: str) -> Dict[str, Optional[Rect]]:
    """Reserved front-of-house geometry: parking bay, entrance lobby and stair core.

    The pieces are laid out in frontage coordinates so they can never overlap:
    the parking bay takes the start of the frontage, the entrance lobby sits on the
    road edge at the far end, and the stair core sits directly behind the lobby.
    Anything that does not fit is dropped (returns ``None``) so the caller can tell
    the user honestly instead of stacking rooms on top of each other.
    """
    fx, fy, fw, fd = footprint
    along = fw if road in ("N", "S") else fd
    into = fd if road in ("N", "S") else fw
    out: Dict[str, Optional[Rect]] = {"stairs": None, "lobby": None, "parking": None}

    stair_along = stair_into = 0.0
    if stair_size:
        stair_along, stair_into = stair_size[0], stair_size[1]
    lobby_along = min(2.2, along * 0.45) if lobby_depth > 0 else 0.0
    lobby_into = min(lobby_depth, into * 0.5) if lobby_depth > 0 else 0.0

    gap = 0.3

    # The stair is essential (it is how you reach the floor above); the lobby is a
    # nice-to-have. Shrink or drop the lobby rather than stacking them.
    stair_fits = (stair_along > 0 and stair_along <= along + 1e-6
                  and stair_into <= into + 1e-6)
    if stair_along > 0 and not stair_fits:
        stair_along = min(stair_along, along)
        stair_into = min(stair_into, into)
        stair_fits = stair_along >= 1.2 and stair_into >= 2.0
    if lobby_along > 0:
        room_for_lobby = into - (stair_into if stair_fits else 0.0)
        if room_for_lobby < 1.4 or lobby_along > along + 1e-6:
            lobby_along = lobby_into = 0.0
        else:
            lobby_into = min(lobby_into, room_for_lobby)
            lobby_along = min(lobby_along, along)

    col_along = max(stair_along if stair_fits else 0.0, lobby_along)
    col_t = max(0.0, along - col_along)

    if stair_fits:
        u0 = lobby_into if lobby_along > 0 else 0.0
        out["stairs"] = snap_rect(_front_place(road, footprint, col_t, u0, stair_along, stair_into))
    if lobby_along > 0 and lobby_into > 0:
        out["lobby"] = snap_rect(_front_place(road, footprint, col_t, 0.0, lobby_along, lobby_into))

    # --- parking bay at the start of the frontage ----------------------------
    pw, pd = PARKING_DIMS.get(parking_kind, (0.0, 0.0))
    if pw > 0 and pd > 0:
        pw = min(pw, along * 0.6)
        pd = min(pd, into * 0.65)
        used_t = col_t if (out["stairs"] is not None or out["lobby"] is not None) else along
        room_along = used_t - gap if used_t < along else along
        car = "car" in parking_kind
        if pw >= (2.45 if car else 1.4) and pd >= (4.4 if car else 2.4) and room_along >= pw:
            out["parking"] = snap_rect(_front_place(road, footprint, 0.0, 0.0, pw, pd))
    return out


def _rect_in(rects: List[Rect], target: Rect, tol: float = 0.05) -> bool:
    return any(abs(r[0] - target[0]) < tol and abs(r[1] - target[1]) < tol and
               abs(r[2] - target[2]) < tol and abs(r[3] - target[3]) < tol for r in rects)


def _terrace_band(footprint_rects: List[Rect], road: str, area: float) -> Optional[Rect]:
    if not footprint_rects or area <= 1.0:
        return None
    fx, fy, fw, fd = footprint_rects[0]
    depth = min(fd * 0.42, area / max(fw, 0.1))
    if depth < 1.4:
        return None
    if road == "S":
        return (fx, r3(fy + fd - depth), fw, r3(depth))
    if road == "N":
        return (fx, fy, fw, r3(depth))
    if road == "E":
        width = min(fw * 0.42, area / max(fd, 0.1))
        return (fx, fy, r3(width), fd)
    width = min(fw * 0.42, area / max(fd, 0.1))
    return (r3(fx + fw - width), fy, r3(width), fd)


def _best_balcony_host(floor: Floor, road: str) -> Optional[Room]:
    candidates = [r for r in floor.rooms
                  if r.type in ("living", "master_bedroom", "bedroom") and not r.is_outdoor]
    if not candidates:
        return None

    def key(room: Room) -> float:
        if road == "S":
            return room.y
        if road == "N":
            return -(room.y + room.d)
        if road == "E":
            return -(room.x + room.w)
        return room.x

    return sorted(candidates, key=key)[0]


def _outdoor_openings(floors: List[Floor], plot: Plot) -> List[str]:
    """Connect gardens / parking / porches to the rest of the plot."""
    from .openings import add_opening, find_offset, DOOR_WIDTHS
    from ..geometry import neighbour_on, OPPOSITE_EDGE
    notes: List[str] = []
    for floor in floors:
        used: Dict[Tuple[str, str], List[Tuple[float, float]]] = {}
        for op in floor.openings:
            room = floor.room(op.room_id)
            if room is None:
                continue
            base = room.x if op.edge in ("N", "S") else room.y
            used.setdefault((room.id, op.edge), []).append(
                (base + op.offset - op.width / 2, base + op.offset + op.width / 2))
        for outdoor in [r for r in floor.rooms if r.is_outdoor and r.type in ("garden", "porch", "courtyard", "parking")]:
            for other in floor.rooms:
                if other.id == outdoor.id or other.is_outdoor:
                    continue
                hit = neighbour_on(other, outdoor)
                if not hit:
                    continue
                edge, lo, hi = hit
                width = min(1.2, (hi - lo) - 0.6)
                if width < 0.8:
                    continue
                base = other.x if edge in ("N", "S") else other.y
                offset = find_offset(other, edge, width, used, prefer=(lo + hi) / 2 - base)  # type: ignore[arg-type]
                if offset is None:
                    continue
                add_opening(floor, other, edge, offset, width, "door", outdoor.id, used)  # type: ignore[arg-type]
                break
    return notes


def compute_metrics(plot: Plot, floors: List[Floor], envelope_rects: List[Rect],
                    footprint_rects: List[Rect], req: Requirements) -> DesignMetrics:
    env_area = sum(rect_area(r) for r in envelope_rects)
    fp_area = sum(rect_area(r) for r in footprint_rects)
    ground = floors[0] if floors else None
    ground_built = sum(r.area for r in ground.rooms if not r.is_outdoor) if ground else 0.0
    total_built = sum(f.built_up_area for f in floors if not f.is_terrace)
    circulation_types = {"corridor", "lobby", "stairs"}
    circulation = sum(r.area for f in floors for r in f.rooms if r.type in circulation_types)
    open_area = max(0.0, plot.area - fp_area)
    parking_area = sum(r.area for f in floors for r in f.rooms if r.type == "parking")
    return DesignMetrics(
        plot_area_m2=round(plot.area, 2),
        buildable_area_m2=round(env_area, 2),
        ground_coverage_pct=round(100 * ground_built / plot.area, 1) if plot.area else 0.0,
        total_built_up_m2=round(total_built, 2),
        total_built_up_sqft=round(total_built * 10.7639104, 1),
        floor_areas_m2={f.name: round(f.built_up_area, 2) for f in floors},
        room_count=sum(len(f.rooms) for f in floors),
        bedroom_count=sum(1 for f in floors for r in f.rooms if r.type in ("bedroom", "master_bedroom", "servant_room")),
        bathroom_count=sum(1 for f in floors for r in f.rooms if r.type in ("bathroom", "toilet")),
        circulation_pct=round(100 * circulation / total_built, 1) if total_built else 0.0,
        open_space_m2=round(open_area, 2),
        open_space_pct=round(100 * open_area / plot.area, 1) if plot.area else 0.0,
        parking_area_m2=round(parking_area, 2),
    )


def _standard_assumptions(plot: Plot, req: Requirements, concept: Concept, sg: Dict[str, float]) -> List[Assumption]:
    return [
        Assumption(topic="dimensions",
                   statement="Room dimensions are measured to wall centre lines. Clear (carpet) dimensions "
                             "are smaller by the wall thickness: 230 mm for external walls and 115 mm for "
                             "partitions.",
                   severity="info"),
        Assumption(topic="floor-height",
                   statement=f"Floor to floor height assumed as {FLOOR_HEIGHT:.2f} m with a 150 mm slab.",
                   severity="info"),
        Assumption(topic="stairs",
                   statement=(f"Stair designed as a dog-legged pair of flights: {int(sg['risers'])} risers of "
                              f"{sg['rise'] * 1000:.0f} mm and {sg['tread'] * 1000:.0f} mm treads. Stair "
                              f"geometry must be checked against your local building code and confirmed by a "
                              f"structural engineer."),
                   severity="warning"),
        Assumption(topic="structure",
                   statement="Walls are shown as load bearing masonry for visualisation. Column positions, "
                             "beam sizes, foundation depth and soil bearing capacity are NOT designed here.",
                   severity="critical"),
        Assumption(topic="status",
                   statement="This is a preliminary design aid generated by a rule based planning engine. "
                             "It is not a construction drawing and must be reviewed by a licensed architect "
                             "and structural engineer before any work starts.",
                   severity="critical"),
        Assumption(topic="regulations",
                   statement="Setbacks, ground coverage and floor area ratio depend on your municipal "
                             "byelaws and road width. Nothing here verifies compliance.",
                   severity="warning"),
    ]


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
