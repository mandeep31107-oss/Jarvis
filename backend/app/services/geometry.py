"""Derived wall geometry + neighbour analysis.

Rooms are rectangles; walls are *derived* from them so that the 2D editor, the 3D
builder and the exports can never disagree. The same rules are implemented in
``frontend/src/lib/walls.ts`` for interactive editing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from ..schemas import Edge, Floor, Room
from .layout.partition import Rect, r3

EXTERIOR_THICKNESS = 0.23   # m (typical 9" masonry, indicative only)
INTERIOR_THICKNESS = 0.115  # m (typical 4.5" partition, indicative only)


@dataclass
class WallSegment:
    id: str
    axis: str          # 'x' (runs North-South) or 'y' (runs East-West)
    line: float        # coordinate of the wall centre line
    lo: float
    hi: float
    thickness: float
    kind: str          # 'exterior' | 'interior'
    rooms: List[str]
    openings: List[str]

    @property
    def length(self) -> float:
        return max(0.0, self.hi - self.lo)

    def box(self) -> Rect:
        t = self.thickness
        if self.axis == "y":
            return (self.lo, r3(self.line - t / 2), r3(self.hi - self.lo), t)
        return (r3(self.line - t / 2), self.lo, t, r3(self.hi - self.lo))


def room_rect(room: Room) -> Rect:
    return (room.x, room.y, room.w, room.d)


def edge_line(room: Room, edge: Edge) -> Tuple[str, float, float, float]:
    """(axis, line, lo, hi) for one edge of a room."""
    if edge == "N":
        return ("y", r3(room.y + room.d), room.x, r3(room.x + room.w))
    if edge == "S":
        return ("y", room.y, room.x, r3(room.x + room.w))
    if edge == "E":
        return ("x", r3(room.x + room.w), room.y, r3(room.y + room.d))
    return ("x", room.x, room.y, r3(room.y + room.d))


def edge_of(room: Room, edge: Edge) -> str:
    return edge


def neighbour_on(room: Room, other: Room, tol: float = 0.08) -> Optional[Tuple[Edge, float, float]]:
    """If ``other`` touches ``room`` return (edge_of_room, overlap_lo, overlap_hi)."""
    a, b = room_rect(room), room_rect(other)
    # other is north of room
    if abs((a[1] + a[3]) - b[1]) <= tol:
        lo, hi = max(a[0], b[0]), min(a[0] + a[2], b[0] + b[2])
        if hi - lo > 0.05:
            return ("N", r3(lo), r3(hi))
    if abs(a[1] - (b[1] + b[3])) <= tol:
        lo, hi = max(a[0], b[0]), min(a[0] + a[2], b[0] + b[2])
        if hi - lo > 0.05:
            return ("S", r3(lo), r3(hi))
    if abs((a[0] + a[2]) - b[0]) <= tol:
        lo, hi = max(a[1], b[1]), min(a[1] + a[3], b[1] + b[3])
        if hi - lo > 0.05:
            return ("E", r3(lo), r3(hi))
    if abs(a[0] - (b[0] + b[2])) <= tol:
        lo, hi = max(a[1], b[1]), min(a[1] + a[3], b[1] + b[3])
        if hi - lo > 0.05:
            return ("W", r3(lo), r3(hi))
    return None


def adjacency(rooms: Iterable[Room], tol: float = 0.08) -> Dict[str, List[Tuple[str, Edge, float, float]]]:
    """room_id -> [(other_id, edge_on_room, overlap_lo, overlap_hi)]."""
    out: Dict[str, List[Tuple[str, Edge, float, float]]] = {r.id: [] for r in rooms}
    rooms = list(rooms)
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            hit = neighbour_on(a, b, tol)
            if hit:
                edge, lo, hi = hit
                out[a.id].append((b.id, edge, lo, hi))
                out[b.id].append((a.id, OPPOSITE_EDGE[edge], lo, hi))
    return out


OPPOSITE_EDGE: Dict[Edge, Edge] = {"N": "S", "S": "N", "E": "W", "W": "E"}


def derive_walls(rooms: Iterable[Room], exterior: float = EXTERIOR_THICKNESS,
                 interior: float = INTERIOR_THICKNESS,
                 opening_ids: Optional[Dict[str, List[str]]] = None) -> List[WallSegment]:
    """Merge collinear room edges into wall segments.

    A stretch of wall is *interior* only where rooms exist on **both** sides of the
    wall line; anywhere else it is an exterior wall (thicker, and eligible for
    windows). This is what makes shared walls correct for neighbouring rooms whose
    south walls both sit on the plot boundary.
    """
    rooms = list(rooms)
    groups: Dict[Tuple[str, float], Dict[str, List[Tuple[float, float, str]]]] = {}

    for room in rooms:
        for edge in ("N", "S", "E", "W"):
            axis, line, lo, hi = edge_line(room, edge)  # type: ignore[arg-type]
            key = (axis, round(line, 3))
            group = groups.setdefault(key, {"a": [], "b": []})
            # side 'a' = room is below/left of the wall line, 'b' = above/right
            side = "a" if edge in ("N", "E") else "b"
            group[side].append((lo, hi, room.id))

    walls: List[WallSegment] = []
    for (axis, line), sides in sorted(groups.items()):
        cuts = sorted({c for spans in sides.values() for span in spans for c in (span[0], span[1])})
        for c0, c1 in zip(cuts, cuts[1:]):
            if c1 - c0 < 0.02:
                continue
            eps = 0.02
            a_rooms = [rid for lo, hi, rid in sides["a"] if lo <= c0 + eps and hi >= c1 - eps]
            b_rooms = [rid for lo, hi, rid in sides["b"] if lo <= c0 + eps and hi >= c1 - eps]
            room_ids = list(dict.fromkeys(a_rooms + b_rooms))
            if not room_ids:
                continue
            kind = "interior" if (a_rooms and b_rooms) else "exterior"
            thickness = interior if kind == "interior" else exterior
            wid = f"wall_{axis}_{round(line, 2)}_{round(c0, 2)}"
            ops: List[str] = []
            for rid in room_ids:
                ops.extend((opening_ids or {}).get(rid, []))
            walls.append(WallSegment(id=wid, axis=axis, line=line, lo=r3(c0), hi=r3(c1),
                                     thickness=thickness, kind=kind, rooms=room_ids, openings=ops))
    return walls


def walls_payload(floor: Floor) -> List[dict]:
    opening_ids: Dict[str, List[str]] = {}
    for op in floor.openings:
        opening_ids.setdefault(op.room_id, []).append(op.id)
    walls = derive_walls(floor.rooms, opening_ids=opening_ids)
    out = []
    for w in walls:
        box = w.box()
        out.append({
            "id": w.id, "axis": w.axis, "line": w.line, "lo": w.lo, "hi": w.hi,
            "thickness": w.thickness, "kind": w.kind, "rooms": w.rooms,
            "x": box[0], "y": box[1], "w": box[2], "d": box[3],
        })
    return out


def exterior_edges(room: Room, rooms: List[Room], outdoor_counts_as_exterior: bool = True,
                   tol: float = 0.08) -> List[Tuple[Edge, float, float]]:
    """Edges of ``room`` that face outdoors (no indoor neighbour)."""
    result: List[Tuple[Edge, float, float]] = []
    for edge in ("N", "S", "E", "W"):
        blocked = False
        for other in rooms:
            if other.id == room.id:
                continue
            hit = neighbour_on(room, other, tol)
            if not hit or hit[0] != edge:
                continue
            if not other.is_outdoor or not outdoor_counts_as_exterior:
                blocked = True
                break
        if not blocked:
            axis, line, lo, hi = edge_line(room, edge)  # type: ignore[arg-type]
            result.append((edge, lo, hi))  # type: ignore[arg-type]
    return result


def edge_length(room: Room, edge: Edge) -> float:
    return room.w if edge in ("N", "S") else room.d


def edge_start(room: Room, edge: Edge) -> Tuple[float, float]:
    """World coordinate where offset=0 sits for the given edge."""
    if edge == "S":
        return (room.x, room.y)
    if edge == "N":
        return (room.x, room.y + room.d)
    if edge == "W":
        return (room.x, room.y)
    return (room.x + room.w, room.y)


def opening_world(room: Room, op) -> Tuple[float, float, float, str]:
    """(centre_x, centre_y, wall_line, axis) for an opening on a room edge."""
    offset = op.clamped_offset(room)
    if op.edge in ("N", "S"):
        y = room.y + room.d if op.edge == "N" else room.y
        return (room.x + offset, y, y, "y")
    x = room.x + room.w if op.edge == "E" else room.x
    return (x, room.y + offset, x, "x")
