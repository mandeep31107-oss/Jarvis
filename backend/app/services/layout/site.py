"""Site geometry: setbacks, polygon offsets and rect decomposition.

Rectangular and square plots use a simple inset envelope. L-shaped and irregular
plots are inset edge-by-edge and then decomposed into a small set of rectangles,
which lets the same guillotine planner produce genuinely L-shaped plans instead of
pretending the plot is a box.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from ...schemas import Point, Setbacks
from .partition import Rect, rect_area, r3

Pt = Tuple[float, float]


def polygon_area(points: Sequence[Pt]) -> float:
    n = len(points)
    if n < 3:
        return 0.0
    acc = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0


def ensure_ccw(points: Sequence[Pt]) -> List[Pt]:
    pts = [tuple(p) for p in points]
    acc = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        acc += x1 * y2 - x2 * y1
    if acc < 0:
        pts.reverse()
    return pts


def point_in_polygon(x: float, y: float, points: Sequence[Pt]) -> bool:
    inside = False
    n = len(points)
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def bbox(points: Sequence[Pt]) -> Rect:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return (r3(x0), r3(y0), r3(x1 - x0), r3(y1 - y0))


def offset_polygon(points: Sequence[Pt], setbacks: Setbacks) -> Tuple[List[Pt], str]:
    """Inset an axis-aligned polygon by the compass setbacks.

    Returns (polygon, mode) where mode is ``'edge'`` for a true per-edge inset or
    ``'uniform'`` when the boundary has diagonal edges and only a uniform inset was
    possible (reported to the user as an assumption).
    """
    pts = ensure_ccw(points)
    n = len(pts)
    if n < 3:
        return list(pts), "uniform"

    lines: List[Tuple[str, float, float]] = []  # (axis, line, inward_direction_sign)
    axis_aligned = True
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        if abs(dx) < 1e-9:      # vertical edge -> faces East or West
            # CCW: outward normal is (dy, -dx)/|..| = (dy,0) -> +y means East
            side = "E" if dy > 0 else "W"
            dist = setbacks.for_side(side)
            line = x1 + (dist if side == "W" else -dist)
            lines.append(("x", line, 1.0 if side == "W" else -1.0))
        elif abs(dy) < 1e-9:    # horizontal edge -> faces North or South
            side = "S" if dx > 0 else "N"
            dist = setbacks.for_side(side)
            line = y1 + (-dist if side == "N" else dist)
            lines.append(("y", line, -1.0 if side == "N" else 1.0))
        else:
            axis_aligned = False
            break

    if not axis_aligned:
        avg = (setbacks.north + setbacks.south + setbacks.east + setbacks.west) / 4.0
        cx = sum(p[0] for p in pts) / n
        cy = sum(p[1] for p in pts) / n
        area = polygon_area(pts)
        scale = max(0.05, ((area - avg * _perimeter(pts)) / area)) ** 0.5 if area > 0 else 0.9
        return [(r3(cx + (x - cx) * scale), r3(cy + (y - cy) * scale)) for x, y in pts], "uniform"

    out: List[Pt] = []
    for i in range(n):
        a_axis, a_line, _ = lines[i - 1]
        b_axis, b_line, _ = lines[i]
        if a_axis == b_axis:  # degenerate
            out.append(pts[i])
            continue
        x = a_line if a_axis == "x" else b_line
        y = a_line if a_axis == "y" else b_line
        out.append((r3(x), r3(y)))

    if polygon_area(out) <= 1.0:
        return list(pts), "rejected"
    return out, "edge"


def _perimeter(points: Sequence[Pt]) -> float:
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    return total


def largest_rect(mask: List[List[bool]], rows: int, cols: int) -> Tuple[int, int, int, int, int]:
    """Largest all-True rectangle in a boolean matrix -> (area, r0, c0, r1, c1)."""
    heights = [0] * cols
    best = (0, 0, 0, 0, 0)
    for r in range(rows):
        for c in range(cols):
            heights[c] = heights[c] + 1 if mask[r][c] else 0
        stack: List[int] = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= h:  # type: ignore[misc]
                sc, sh = stack.pop()  # type: ignore[misc]
                area = sh * (c - sc)
                if area > best[0]:
                    best = (area, r - sh + 1, sc, r, c - 1)
                start = sc
            stack.append((start, h))  # type: ignore[arg-type]
    return best


def decompose_polygon(points: Sequence[Pt], grid: float = 0.25, max_rects: int = 6,
                      min_rect_area: float = 3.0) -> List[Rect]:
    """Greedy decomposition of a polygon into a few axis-aligned rectangles."""
    x0, y0, w, h = bbox(points)
    if w <= 0 or h <= 0:
        return []
    cols = max(1, int(round(w / grid)))
    rows = max(1, int(round(h / grid)))
    cols = min(cols, 240)
    rows = min(rows, 240)
    cw, ch = w / cols, h / rows

    mask: List[List[bool]] = []
    for r in range(rows):
        row: List[bool] = []
        cy = y0 + (r + 0.5) * ch
        for c in range(cols):
            cx = x0 + (c + 0.5) * cw
            row.append(point_in_polygon(cx, cy, points))
        mask.append(row)

    rects: List[Rect] = []
    for _ in range(max_rects):
        area_cells, r0, c0, r1, c1 = largest_rect(mask, rows, cols)
        if area_cells <= 0:
            break
        rect = (r3(x0 + c0 * cw), r3(y0 + r0 * ch), r3((c1 - c0 + 1) * cw), r3((r1 - r0 + 1) * ch))
        if rect_area(rect) < min_rect_area:
            break
        rects.append(rect)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                mask[r][c] = False
        covered = sum(rect_area(r) for r in rects)
        if covered >= polygon_area(points) * 0.97:
            break
    return rects


def boundary_points_from_plot(width: float, depth: float, shape: str) -> List[Point]:
    """Default boundary polygons for the shapes we support without drawing."""
    if shape == "l_shape":
        cut_w, cut_d = width * 0.42, depth * 0.38
        return [
            Point(x=0, y=0),
            Point(x=width, y=0),
            Point(x=width, y=depth - cut_d),
            Point(x=width - cut_w, y=depth - cut_d),
            Point(x=width - cut_w, y=depth),
            Point(x=0, y=depth),
        ]
    return [Point(x=0, y=0), Point(x=width, y=0), Point(x=width, y=depth), Point(x=0, y=depth)]


def largest_rect_in_polygon(polygon: Sequence[Pt], strip: Rect, grid: float = 0.25) -> "Rect | None":
    """Largest axis aligned rectangle inside ``polygon`` and ``strip``.

    Used to find the parking / porch band on irregular plots, where the setback
    strip is not a simple rectangle.
    """
    x0, y0, w, h = strip
    if w <= 0.1 or h <= 0.1:
        return None
    cols = max(1, min(int(round(w / grid)), 160))
    rows = max(1, min(int(round(h / grid)), 160))
    cw, ch = w / cols, h / rows
    mask = [[point_in_polygon(x0 + (c + 0.5) * cw, y0 + (r + 0.5) * ch, polygon)
             for c in range(cols)] for r in range(rows)]
    area, r0, c0, r1, c1 = largest_rect(mask, rows, cols)
    if area <= 0:
        return None
    return (r3(x0 + c0 * cw), r3(y0 + r0 * ch), r3((c1 - c0 + 1) * cw), r3((r1 - r0 + 1) * ch))
