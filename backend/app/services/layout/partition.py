"""Space partitioning primitives used by the layout engine.

The planner is a *cost driven guillotine (binary space) partition*:

* every split is axis aligned and cuts a rectangle fully into two rectangles, so
  the resulting rooms always tile the footprint exactly (no gaps, no overlaps);
* candidate splits are scored against each room's area targets, minimum
  dimensions, aspect ratio limits and a compass target point (which is how
  optional Vastu / zoning preferences are honoured);
* everything is deterministic -- the same input always produces the same plan,
  which is what makes it testable and what stops a language model from having to
  invent geometry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Rect = Tuple[float, float, float, float]  # x, y, w, d  (x -> East, y -> North)

GRID = 0.05  # 5 cm snapping grid


def r3(v: float) -> float:
    return round(v + 1e-9, 3)


def rect_area(r: Rect) -> float:
    return max(0.0, r[2]) * max(0.0, r[3])


def rect_center(r: Rect) -> Tuple[float, float]:
    return (r[0] + r[2] / 2.0, r[1] + r[3] / 2.0)


def snap(v: float, grid: float = GRID) -> float:
    return r3(round(v / grid) * grid)


def monotonic_snap(values: Sequence[float], grid: float = GRID) -> Dict[float, float]:
    """Snap a set of coordinates to the grid *without* reordering or merging them.

    Snapping every rectangle independently lets neighbouring rooms drift apart by
    up to one grid step, which shows up as hairline overlaps in the plan. Snapping
    the shared coordinate list keeps the tiling exact.
    """
    mapping: Dict[float, float] = {}
    prev_v: Optional[float] = None
    prev_s: Optional[float] = None
    for v in sorted({round(float(x), 6) for x in values}):
        sv = round(round(v / grid) * grid, 4)
        if prev_s is not None and sv <= prev_s + 1e-9:
            # Coordinates closer together than one grid step collapse onto the same
            # line. Merging (rather than pushing) keeps cells flush with reserved
            # geometry, which is what prevents hairline overlaps.
            sv = prev_s
        mapping[v] = r3(sv)
        prev_v, prev_s = v, sv
    return mapping


def snap_cells(cells: List[Tuple[Rect, "Item"]], extra: Sequence[Rect] = ()) -> List[Tuple[Rect, "Item"]]:
    xs: List[float] = []
    ys: List[float] = []
    for rect, _ in cells:
        xs += [rect[0], rect[0] + rect[2]]
        ys += [rect[1], rect[1] + rect[3]]
    for rect in extra:
        xs += [rect[0], rect[0] + rect[2]]
        ys += [rect[1], rect[1] + rect[3]]
    mx = monotonic_snap(xs)
    my = monotonic_snap(ys)
    out: List[Tuple[Rect, "Item"]] = []
    for rect, item in cells:
        x0 = mx[round(rect[0], 6)]
        x1 = mx[round(rect[0] + rect[2], 6)]
        y0 = my[round(rect[1], 6)]
        y1 = my[round(rect[1] + rect[3], 6)]
        out.append(((x0, y0, r3(x1 - x0), r3(y1 - y0)), item))
    return out


def snap_rect_inward(r: Rect, grid: float = GRID) -> Rect:
    """Snap to the grid but always *inside* the original rectangle.

    Outer boundaries must never be rounded outwards: a footprint snapped from
    8.144 m to 8.15 m quietly breaks a 1.00 m setback. Inner geometry may shrink by
    up to one grid step, which is harmless.
    """
    x0, y0, w, d = r
    x1 = math.floor((x0 + w) / grid - 1e-9) * grid
    y1 = math.floor((y0 + d) / grid - 1e-9) * grid
    xa = math.ceil(x0 / grid - 1e-9) * grid
    ya = math.ceil(y0 / grid - 1e-9) * grid
    if xa >= x1:
        xa = x0
    if ya >= y1:
        ya = y0
    return (r3(xa), r3(ya), r3(max(grid, x1 - xa)), r3(max(grid, y1 - ya)))


def snap_rect(r: Rect, grid: float = GRID) -> Rect:
    x, y = snap(r[0], grid), snap(r[1], grid)
    x2, y2 = snap(r[0] + r[2], grid), snap(r[1] + r[3], grid)
    return (x, y, r3(max(grid, x2 - x)), r3(max(grid, y2 - y)))


def rects_overlap(a: Rect, b: Rect, tol: float = 1e-6) -> bool:
    """True when the two rects overlap by more than ``tol`` (negative tol = require
    that much overlap before reporting a clash)."""
    return not (a[0] + a[2] <= b[0] + tol or b[0] + b[2] <= a[0] + tol or
                a[1] + a[3] <= b[1] + tol or b[1] + b[3] <= a[1] + tol)


def intersection_area(a: Rect, b: Rect) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy


def fits_inside(inner: Rect, outer: Rect, tol: float = 0.02) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol and
            inner[0] + inner[2] <= outer[0] + outer[2] + tol and
            inner[1] + inner[3] <= outer[1] + outer[3] + tol)


@dataclass
class Item:
    """A room request handed to the partitioner."""

    type: str
    target: float                      # desired area, m²
    min_area: float
    max_area: float
    min_dim: float
    max_aspect: float
    tx: float                          # compass target point (absolute plan coords)
    ty: float
    weight: float = 1.0
    name: Optional[str] = None
    fixed: Optional[Rect] = None       # must land here (e.g. stair above ground stair)
    outdoor: bool = False
    tag: str = ""
    meta: dict = field(default_factory=dict)


def split_rect(rect: Rect, axis: str, frac: float) -> Tuple[Rect, Rect]:
    x, y, w, d = rect
    frac = min(max(frac, 0.05), 0.95)
    if axis == "x":
        cut = r3(x + w * frac)
        return (x, y, r3(cut - x), d), (cut, y, r3(x + w - cut), d)
    cut = r3(y + d * frac)
    return (x, y, w, r3(cut - y)), (x, cut, w, r3(y + d - cut))


def cell_cost(rect: Rect, item: Item, diag: float) -> float:
    """Penalty for giving ``item`` the rectangle ``rect``."""
    w, d = rect[2], rect[3]
    area = w * d
    pen = 0.0

    if area < item.min_area - 1e-6:
        deficit = (item.min_area - area) / max(item.min_area, 1.0)
        pen += 400.0 * deficit + 25.0

    ratio = area / max(item.target, 0.1)
    if ratio > 1.0:
        pen += 9.0 * min(ratio - 1.0, 2.5)
    else:
        pen += 22.0 * (1.0 - ratio)

    if w < item.min_dim - 1e-6:
        pen += 260.0 * (item.min_dim - w)
    if d < item.min_dim - 1e-6:
        pen += 260.0 * (item.min_dim - d)

    short = max(min(w, d), 1e-6)
    aspect = max(w, d) / short
    if aspect > item.max_aspect:
        pen += 34.0 * (aspect - item.max_aspect)

    cx, cy = rect_center(rect)
    dist = math.hypot(cx - item.tx, cy - item.ty) / max(diag, 1e-6)
    pen += item.weight * 13.0 * dist

    if item.fixed is not None:
        inter = intersection_area(rect, item.fixed)
        union = rect_area(rect) + rect_area(item.fixed) - inter
        iou = inter / union if union > 0 else 0.0
        pen += 900.0 * (1.0 - iou)

    return pen


def infeasibility(rect: Rect, items: Sequence[Item]) -> float:
    """Hard geometric feasibility penalty.

    Any guillotine partition of ``rect`` gives every leaf a cell whose *shorter*
    side is at most ``min(rect.w, rect.d)``. So if that short side is smaller than
    a room's minimum dimension, no split of this rectangle can ever satisfy it --
    which is exactly how 1.7 m wide bedrooms happen. Reject those splits loudly.
    """
    if not items:
        return 0.0
    short = min(rect[2], rect[3])
    pen = 0.0
    for item in items:
        if short < item.min_dim - 1e-6:
            pen += 420.0 * (item.min_dim - short)
    return pen


def estimate_group_cost(rect: Rect, items: Sequence[Item], diag: float) -> float:
    """One-level look-ahead: how well could ``items`` share ``rect``?"""
    if not items:
        return 0.0
    if len(items) == 1:
        return cell_cost(rect, items[0], diag)

    xs = [i.tx for i in items]
    ys = [i.ty for i in items]
    spread_x = (max(xs) - min(xs)) / max(rect[2], 1e-6)
    spread_y = (max(ys) - min(ys)) / max(rect[3], 1e-6)
    axis = "x" if spread_x >= spread_y else "y"
    ordered = sorted(items, key=lambda i: (i.tx if axis == "x" else i.ty))

    total = sum(max(i.target, 0.1) for i in ordered)
    cost = infeasibility(rect, items)
    cursor = 0.0
    remaining = rect
    for idx, item in enumerate(ordered):
        if idx == len(ordered) - 1:
            piece = remaining
        else:
            frac = (max(item.target, 0.1) / total) / max(1.0 - cursor, 1e-6)
            frac = min(max(frac, 0.08), 0.92)
            piece, remaining = split_rect(remaining, axis, frac)
            cursor += max(item.target, 0.1) / total
        cost += cell_cost(piece, item, diag)
    return cost


def partition(rect: Rect, items: List[Item], diag: float, depth: int = 0) -> List[Tuple[Rect, Item]]:
    """Recursively split ``rect`` and return [(room_rect, item), ...]."""
    if not items:
        return []
    if len(items) == 1:
        return [(rect, items[0])]
    if depth > 24:  # pathological guard
        cells = _fallback_strip(rect, items)
        return cells

    best: Optional[Tuple[float, str, int, float]] = None
    for axis in ("x", "y"):
        ordered = sorted(items, key=lambda i: (i.tx if axis == "x" else i.ty, i.target))
        total = sum(max(i.target, 0.1) for i in items)
        for k in range(1, len(items)):
            left, right = ordered[:k], ordered[k:]
            fracs = {sum(max(i.target, 0.1) for i in left) / total}
            # align candidate cuts with reserved rectangles (stair core, parking bay,
            # lobby) so a two split sequence can isolate them exactly
            for it in items:
                if it.fixed is None:
                    continue
                span = rect[2] if axis == "x" else rect[3]
                origin = rect[0] if axis == "x" else rect[1]
                for edge in (it.fixed[0], it.fixed[0] + it.fixed[2]) if axis == "x" \
                        else (it.fixed[1], it.fixed[1] + it.fixed[3]):
                    f = (edge - origin) / span if span else 0.0
                    if 0.08 <= f <= 0.92:
                        fracs.add(round(f, 4))
            for frac in fracs:
                r1, r2 = split_rect(rect, axis, frac)
                cost = (estimate_group_cost(r1, left, diag) + estimate_group_cost(r2, right, diag)
                        + infeasibility(r1, left) + infeasibility(r2, right)
                        + 0.35 * abs(frac - 0.5) * 10.0)
                if best is None or cost < best[0]:
                    best = (cost, axis, k, frac)

    _, axis, k, frac = best  # type: ignore[misc]
    ordered = sorted(items, key=lambda i: (i.tx if axis == "x" else i.ty, i.target))
    left, right = ordered[:k], ordered[k:]
    r1, r2 = split_rect(rect, axis, frac)

    out = partition(r1, left, diag, depth + 1)
    out += partition(r2, right, diag, depth + 1)
    return out


def _fallback_strip(rect: Rect, items: Sequence[Item]) -> List[Tuple[Rect, Item]]:
    """Deterministic proportional strip when recursion depth explodes."""
    axis = "x" if rect[2] >= rect[3] else "y"
    total = sum(max(i.target, 0.1) for i in items)
    out: List[Tuple[Rect, Item]] = []
    remaining = rect
    for idx, item in enumerate(items):
        if idx == len(items) - 1:
            out.append((remaining, item))
            break
        frac = max(item.target, 0.1) / total
        piece, remaining = split_rect(remaining, axis, frac)
        out.append((piece, item))
    return out


def carve(rect: Rect, hole: Rect) -> List[Rect]:
    """Remove ``hole`` from ``rect`` returning up to 4 rectangles that tile the rest."""
    if not rects_overlap(rect, hole):
        return [rect]
    hx0, hy0 = max(rect[0], hole[0]), max(rect[1], hole[1])
    hx1 = min(rect[0] + rect[2], hole[0] + hole[2])
    hy1 = min(rect[1] + rect[3], hole[1] + hole[3])
    out: List[Rect] = []
    # left / right / bottom / top bands around the hole
    if hx0 - rect[0] > 0.01:
        out.append((rect[0], rect[1], r3(hx0 - rect[0]), rect[3]))
    if (rect[0] + rect[2]) - hx1 > 0.01:
        out.append((hx1, rect[1], r3(rect[0] + rect[2] - hx1), rect[3]))
    if hy0 - rect[1] > 0.01:
        out.append((hx0, rect[1], r3(hx1 - hx0), r3(hy0 - rect[1])))
    if (rect[1] + rect[3]) - hy1 > 0.01:
        out.append((hx0, hy1, r3(hx1 - hx0), r3(rect[1] + rect[3] - hy1)))
    return [r for r in out if rect_area(r) > 0.05]


def repair_min_dims(cells: List[Tuple[Rect, Item]], tol: float = 0.02) -> List[str]:
    """Nudge shared boundaries so rooms respect their minimum dimension.

    A boundary is only moved when it is shared along the **full** edge of both
    rooms and no third room touches that stretch of wall -- otherwise moving it
    would create gaps or overlaps in the plan. Returns human readable notes.
    """
    notes: List[str] = []
    rects = [c[0] for c in cells]
    items = [c[1] for c in cells]

    for i in range(len(rects)):
        if items[i].fixed is not None:
            continue  # reserved geometry (stair core, parking bay) is never resized
        for _attempt in range(3):
            rect, item = rects[i], items[i]
            need_x = item.min_dim - rect[2]
            need_y = item.min_dim - rect[3]
            if need_x <= tol and need_y <= tol:
                break
            for axis, need in (("x", need_x), ("y", need_y)):
                if need <= tol:
                    continue
                donor = _find_donor(rects, items, i, axis, need)
                if donor is None:
                    notes.append(
                        f"{item.name or item.type} could not reach the recommended minimum "
                        f"dimension of {item.min_dim:.2f} m in this layout."
                    )
                    continue
                j, shift = donor
                rects[i], rects[j] = _shift_boundary(rects[i], rects[j], axis, shift)
                notes.append(
                    f"Widened {item.name or item.type} by {shift:.2f} m "
                    f"(taken from {items[j].name or items[j].type})."
                )
    notes.extend(_balance_areas(rects, items))
    for idx in range(len(cells)):
        cells[idx] = (rects[idx], items[idx])
    return _dedupe_notes(notes)


def _balance_areas(rects: List[Rect], items: List[Item]) -> List[str]:
    """Move surplus space from an oversized room to an undersized neighbour.

    Only clean, fully shared boundaries are moved, so the plan keeps tiling the
    footprint exactly (no gaps, no overlaps).
    """
    notes: List[str] = []
    for _round in range(3):
        moved = False
        for i, item in enumerate(items):
            if item.fixed is not None:
                continue
            area = rect_area(rects[i])
            if area <= item.target * 1.02:
                continue
            surplus = area - item.target
            for j, other in enumerate(items):
                if j == i or other.fixed is not None:
                    continue
                other_area = rect_area(rects[j])
                if other_area >= other.target * 0.98:
                    continue
                deficit = other.target - other_area
                for axis in ("x", "y"):
                    need = math.sqrt(max(0.1, (rects[j][3] if axis == "x" else rects[j][2])
                                         * (deficit + 0.1)))
                    need = min(need, surplus * 0.9)
                    span = rects[j][3] if axis == "x" else rects[j][2]
                    if need < 0.12 or need > span * 0.6:
                        continue
                    slack = (rects[i][2] if axis == "x" else rects[i][3]) - item.min_dim
                    if need > slack - 0.05:
                        continue
                    if not _boundary_is_clean(rects, j, i, axis):
                        continue
                    rects[j], rects[i] = _shift_boundary(rects[j], rects[i], axis, r3(need))
                    notes.append(f"Rebalanced the wall between {other.name or other.type} and "
                                 f"{item.name or item.type} by {need:.2f} m.")
                    moved = True
                    break
                if moved:
                    break
            if moved:
                break
        if not moved:
            break
    return _dedupe_notes(notes)


def _dedupe_notes(notes: List[str]) -> List[str]:
    seen = set()
    out = []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _boundary_is_clean(rects: List[Rect], i: int, j: int, axis: str) -> bool:
    a, b = rects[i], rects[j]
    if axis == "x":
        if abs((b[0] + b[2]) - a[0]) < 0.06:
            line, grow = a[0], -1.0
        elif abs(b[0] - (a[0] + a[2])) < 0.06:
            line, grow = a[0] + a[2], 1.0
        else:
            return False
        lo, hi = max(a[1], b[1]), min(a[1] + a[3], b[1] + b[3])
        if hi - lo < min(a[3], b[3]) - 0.06:
            return False
        for k, other in enumerate(rects):
            if k in (i, j):
                continue
            if abs(other[0] - line) < 0.06 or abs(other[0] + other[2] - line) < 0.06:
                o_lo, o_hi = max(other[1], lo - 0.06), min(other[1] + other[3], hi + 0.06)
                if o_hi - o_lo > 0.05:
                    return False
        return True

    if abs((b[1] + b[3]) - a[1]) < 0.06:
        line = a[1]
    elif abs(b[1] - (a[1] + a[3])) < 0.06:
        line = a[1] + a[3]
    else:
        return False
    lo, hi = max(a[0], b[0]), min(a[0] + a[2], b[0] + b[2])
    if hi - lo < min(a[2], b[2]) - 0.06:
        return False
    for k, other in enumerate(rects):
        if k in (i, j):
            continue
        if abs(other[1] - line) < 0.06 or abs(other[1] + other[3] - line) < 0.06:
            o_lo, o_hi = max(other[0], lo - 0.06), min(other[0] + other[2], hi + 0.06)
            if o_hi - o_lo > 0.05:
                return False
    return True


def _find_donor(rects: List[Rect], items: List[Item], i: int, axis: str, need: float):
    rect = rects[i]
    best = None
    for j, other in enumerate(rects):
        if j == i or items[j].fixed is not None:
            continue
        if axis == "x":
            overlap = min(rect[1] + rect[3], other[1] + other[3]) - max(rect[1], other[1])
            if overlap <= 0.4:
                continue
            slack = other[2] - items[j].min_dim
        else:
            overlap = min(rect[0] + rect[2], other[0] + other[2]) - max(rect[0], other[0])
            if overlap <= 0.4:
                continue
            slack = other[3] - items[j].min_dim
        if slack < need + 0.05:
            continue
        span = other[3] if axis == "x" else other[2]
        if rect_area(other) - items[j].min_area < need * span * 0.75:
            continue
        if not _boundary_is_clean(rects, i, j, axis):
            continue
        score = slack - (items[j].target - rect_area(other)) * 0.05
        if best is None or score > best[0]:
            best = (score, j, min(need + 0.1, slack - 0.05))
    if best is None:
        return None
    return best[1], r3(best[2])


def _shift_boundary(a: Rect, b: Rect, axis: str, shift: float) -> Tuple[Rect, Rect]:
    """Move the shared boundary so that ``a`` grows by ``shift`` and ``b`` shrinks."""
    shift = max(0.0, shift)
    if axis == "x":
        if b[0] + b[2] <= a[0] + 1e-6:      # b lies west of a -> grow a westwards
            return ((r3(a[0] - shift), a[1], r3(a[2] + shift), a[3]),
                    (b[0], b[1], r3(b[2] - shift), b[3]))
        return ((a[0], a[1], r3(a[2] + shift), a[3]),
                (r3(b[0] + shift), b[1], r3(b[2] - shift), b[3]))
    if b[1] + b[3] <= a[1] + 1e-6:          # b lies south of a -> grow a southwards
        return ((a[0], r3(a[1] - shift), a[2], r3(a[3] + shift)),
                (b[0], b[1], b[2], r3(b[3] - shift)))
    return ((a[0], a[1], a[2], r3(a[3] + shift)),
            (b[0], r3(b[1] + shift), b[2], r3(b[3] - shift)))


def shared_edge(a: Rect, b: Rect, tol: float = 0.08) -> Optional[Tuple[str, float, float, float]]:
    """Return ('x'|'y', wall_line, overlap_lo, overlap_hi) when two rooms share a wall."""
    for line, other_line in ((a[0] + a[2], b[0]), (b[0] + b[2], a[0])):
        if abs(line - other_line) <= tol:
            lo = max(a[1], b[1])
            hi = min(a[1] + a[3], b[1] + b[3])
            if hi - lo > 0.25:
                return ("x", r3(line), r3(lo), r3(hi))
    for line, other_line in ((a[1] + a[3], b[1]), (b[1] + b[3], a[1])):
        if abs(line - other_line) <= tol:
            lo = max(a[0], b[0])
            hi = min(a[0] + a[2], b[0] + b[2])
            if hi - lo > 0.25:
                return ("y", r3(line), r3(lo), r3(hi))
    return None
