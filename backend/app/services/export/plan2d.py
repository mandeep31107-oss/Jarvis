"""2D plan rendering.

One geometry pass, two backends: SVG (vector, for export and crisp zoom) and PNG
(raster, for thumbnails and PDF pages). Everything drawn comes from the project's
own structured geometry -- rooms, derived walls, openings, stairs and furniture --
so the export always matches the model.

Plans are labelled as visualisations, not construction drawings: they carry a title
block with the scale, the version, the assumptions and the professional-review
notice.
"""

from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...schemas import Floor, Opening, ProjectData, Room
from ..geometry import (EXTERIOR_THICKNESS, INTERIOR_THICKNESS, derive_walls, room_rect)
from ..units import m2_to_sqft

Point = Tuple[float, float]

ROOM_FILL = {
    "living": "#e8eef6", "dining": "#eaf1ec", "kitchen": "#fdf1e3", "bedroom": "#eef0f7",
    "master_bedroom": "#e9ecf8", "bathroom": "#e4f1f4", "toilet": "#e4f1f4",
    "utility": "#f1eee7", "store": "#f0eee9", "study": "#f2eef7", "puja": "#fdf3e0",
    "corridor": "#f3f3f1", "lobby": "#f3f1ec", "stairs": "#eceae5", "balcony": "#e9f4ea",
    "terrace": "#eef5ea", "garden": "#e6f2e2", "courtyard": "#e6f2e2", "parking": "#eeecea",
    "porch": "#f0ece4", "servant_room": "#f1eee9", "shop": "#f6efe6", "open_to_below": "#f7f7f5",
}
DEFAULT_FILL = "#f4f4f2"
TITLE_BAND_M = 3.3
OUTLINE = "#2f3a45"
WALL_COLOR = "#33404d"
DIM_COLOR = "#6b7784"
FURNITURE_COLOR = "#8a94a0"


@dataclass
class PlanOptions:
    floor_id: Optional[str] = None
    all_floors: bool = False
    show_grid: bool = True
    show_dimensions: bool = True
    show_furniture: bool = True
    show_openings: bool = True
    show_labels: bool = True
    show_setbacks: bool = True
    show_site: bool = True
    hide_walls: bool = False
    highlight_room_id: Optional[str] = None
    title: str = ""
    include_disclaimer: bool = True
    monochrome: bool = False
    margin_m: float = 1.6


@dataclass
class Canvas:
    """Minimal drawing surface shared by the SVG and PNG backends."""

    width: float
    height: float
    scale: float = 40.0          # pixels (or SVG units) per metre
    ox: float = 0.0              # origin offset
    oy: float = 0.0
    items: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    def px(self, x: float) -> float:
        return (x - self.ox) * self.scale

    def py(self, y: float) -> float:
        # plan space is +y North; screen space is +y down
        return self.height - (y - self.oy) * self.scale

    def pt(self, p: Point) -> Point:
        return (self.px(p[0]), self.py(p[1]))

    # primitives -----------------------------------------------------------
    def rect(self, x: float, y: float, w: float, h: float, *, fill: str = "none",
             stroke: str = "", stroke_width: float = 1.0, opacity: float = 1.0,
             dash: Optional[str] = None) -> None:
        self.items.append(("rect", {"x": self.px(x), "y": self.py(y + h),
                                    "w": w * self.scale, "h": h * self.scale, "fill": fill,
                                    "stroke": stroke, "stroke_width": stroke_width,
                                    "opacity": opacity, "dash": dash}))

    def line(self, a: Point, b: Point, *, stroke: str = OUTLINE, stroke_width: float = 1.0,
             dash: Optional[str] = None, opacity: float = 1.0) -> None:
        x1, y1 = self.pt(a)
        x2, y2 = self.pt(b)
        self.items.append(("line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "stroke": stroke,
                                    "stroke_width": stroke_width, "dash": dash,
                                    "opacity": opacity}))

    def polyline(self, points: Sequence[Point], *, stroke: str = OUTLINE,
                 stroke_width: float = 1.0, fill: str = "none", dash: Optional[str] = None,
                 opacity: float = 1.0) -> None:
        pts = [self.pt(p) for p in points]
        self.items.append(("polyline", {"points": pts, "stroke": stroke, "fill": fill,
                                        "stroke_width": stroke_width, "dash": dash,
                                        "opacity": opacity}))

    def circle(self, centre: Point, radius: float, *, fill: str = "none", stroke: str = OUTLINE,
               stroke_width: float = 1.0) -> None:
        cx, cy = self.pt(centre)
        self.items.append(("circle", {"cx": cx, "cy": cy, "r": radius * self.scale, "fill": fill,
                                      "stroke": stroke, "stroke_width": stroke_width}))

    def arc(self, centre: Point, radius: float, start_deg: float, end_deg: float, *,
            stroke: str = OUTLINE, stroke_width: float = 1.0) -> None:
        self.items.append(("arc", {"centre": self.pt(centre), "r": radius * self.scale,
                                   "start": start_deg, "end": end_deg, "stroke": stroke,
                                   "stroke_width": stroke_width}))

    def text(self, at: Point, value: str, *, size: float = 11.0, fill: str = "#26313c",
             anchor: str = "middle", weight: str = "normal", rotate: float = 0.0,
             baseline: str = "middle") -> None:
        x, y = self.pt(at)
        self.items.append(("text", {"x": x, "y": y, "value": value, "size": size, "fill": fill,
                                    "anchor": anchor, "weight": weight, "rotate": rotate,
                                    "baseline": baseline}))


# ---------------------------------------------------------------------------
# geometry -> canvas
# ---------------------------------------------------------------------------

def _bounds(data: ProjectData, floors: Sequence[Floor], opts: PlanOptions) -> Tuple[float, float, float, float]:
    xs = [0.0, data.plot.width]
    ys = [0.0, data.plot.depth]
    for floor in floors:
        for room in floor.rooms:
            r = room_rect(room)
            xs += [r[0], r[0] + r[2]]
            ys += [r[1], r[1] + r[3]]
    m = opts.margin_m
    band = TITLE_BAND_M if not opts.all_floors else 0.6
    return min(xs) - m, min(ys) - m - band, max(xs) + m, max(ys) + m


def _title_block(canvas: Canvas, data: ProjectData, floor: Optional[Floor], opts: PlanOptions,
                 x0: float, y0: float, x1: float, y1: float) -> None:
    label = opts.title or data.name
    sub = floor.name if floor else "All floors"
    canvas.text((x0 + 0.4, y1 - 0.5), label, size=17, anchor="start", weight="bold")
    canvas.text((x0 + 0.4, y1 - 1.05), sub, size=12, anchor="start", fill="#4a5764")
    plot = data.plot
    metrics = data.metrics
    info = (f"Plot {plot.width * 3.28084:,.1f} x {plot.depth * 3.28084:,.1f} ft "
            f"({plot.area:,.1f} m\u00b2)  |  road {plot.road_side}  |  built-up "
            f"{metrics.total_built_up_m2:,.1f} m\u00b2 ({metrics.total_built_up_sqft:,.0f} sq ft)")
    canvas.text((x0 + 0.4, y0 + 1.95), info, size=10.5, anchor="start", fill=DIM_COLOR)
    if opts.include_disclaimer:
        canvas.text((x0 + 0.4, y0 + 1.48),
                    "Preliminary design visualisation -- not a construction drawing. Dimensions are "
                    "to wall centre lines.", size=9.5, anchor="start", fill="#8a5a2b")
        canvas.text((x0 + 0.4, y0 + 1.06),
                    "Requires review by a licensed architect and structural engineer before any "
                    "work starts.", size=9.5, anchor="start", fill="#8a5a2b")
        canvas.text((x0 + 0.4, y0 + 0.64),
                    f"Generated by the built-in layout engine | concept: "
                    f"{data.active_concept or 'n/a'} | units: metres (ft shown for reference)",
                    size=9.0, anchor="start", fill="#9aa4ae")


def _north_arrow(canvas: Canvas, at: Point) -> None:
    x, y = at
    canvas.polyline([(x, y), (x - 0.28, y - 0.75), (x, y - 0.5), (x + 0.28, y - 0.75), (x, y)],
                    fill="#33404d", stroke="#33404d", stroke_width=1.0)
    canvas.text((x, y + 0.32), "N", size=12, weight="bold")


def _scale_bar(canvas: Canvas, at: Point, metres: float = 5.0) -> None:
    x, y = at
    segments = 5
    step = metres / segments
    for i in range(segments):
        canvas.rect(x + i * step, y - 0.09, step, 0.18,
                    fill="#33404d" if i % 2 == 0 else "#ffffff", stroke="#33404d",
                    stroke_width=0.8)
    canvas.text((x, y - 0.42), "0", size=9.5, fill=DIM_COLOR)
    canvas.text((x + metres, y - 0.42), f"{metres:g} m", size=9.5, fill=DIM_COLOR)
    canvas.text((x + metres / 2, y + 0.38), "grid squares are 1 m", size=9.5, fill=DIM_COLOR)


def _draw_plot(canvas: Canvas, data: ProjectData, opts: PlanOptions) -> None:
    plot = data.plot
    if opts.show_setbacks:
        sb = plot.setbacks
        canvas.rect(sb.west, sb.south, plot.width - sb.west - sb.east,
                    plot.depth - sb.south - sb.north, fill="none", stroke="#c8a76a",
                    stroke_width=1.2, dash="7 5")
    if len(plot.boundary) >= 3:
        canvas.polyline([(p.x, p.y) for p in plot.boundary] + [(plot.boundary[0].x, plot.boundary[0].y)],
                        stroke=OUTLINE, stroke_width=2.4, fill="#fbfbfa")
    else:
        canvas.rect(0, 0, plot.width, plot.depth, fill="#fbfbfa", stroke=OUTLINE, stroke_width=2.4)
    if opts.show_grid:
        step = 1.0
        x = 0.0
        while x <= plot.width + 1e-6:
            canvas.line((x, 0), (x, plot.depth), stroke="#e2e6ea", stroke_width=0.5)
            x += step
        y = 0.0
        while y <= plot.depth + 1e-6:
            canvas.line((0, y), (plot.width, y), stroke="#e2e6ea", stroke_width=0.5)
            y += step
    # road indication
    road = plot.road_side
    if road == "S":
        canvas.rect(0, -0.95, plot.width, 0.6, fill="#dfe3e7", stroke="none")
        canvas.text((plot.width / 2, -0.65), "ROAD", size=10, fill="#6b7784")
    elif road == "N":
        canvas.rect(0, plot.depth + 0.35, plot.width, 0.6, fill="#dfe3e7", stroke="none")
        canvas.text((plot.width / 2, plot.depth + 0.65), "ROAD", size=10, fill="#6b7784")
    elif road == "E":
        canvas.rect(plot.width + 0.35, 0, 0.6, plot.depth, fill="#dfe3e7", stroke="none")
        canvas.text((plot.width + 0.65, plot.depth / 2), "ROAD", size=10, fill="#6b7784",
                    rotate=-90)
    else:
        canvas.rect(-0.95, 0, 0.6, plot.depth, fill="#dfe3e7", stroke="none")
        canvas.text((-0.65, plot.depth / 2), "ROAD", size=10, fill="#6b7784", rotate=-90)


def _fill_for(room: Room, monochrome: bool) -> str:
    if monochrome:
        return "#ffffff" if not room.is_outdoor else "#f2f2f2"
    return ROOM_FILL.get(room.type, DEFAULT_FILL)


def _draw_room(canvas: Canvas, room: Room, opts: PlanOptions, data: ProjectData) -> None:
    r = room_rect(room)
    fill = _fill_for(room, opts.monochrome)
    canvas.rect(r[0], r[1], r[2], r[3], fill=fill,
                stroke="#b9c2cb" if room.is_outdoor else "none",
                stroke_width=1.0, dash="6 4" if room.is_outdoor else None)
    if opts.highlight_room_id == room.id:
        canvas.rect(r[0], r[1], r[2], r[3], fill="none", stroke="#3f7fd6", stroke_width=3.0)
    if not opts.show_labels:
        return
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    size = 12.0 if min(r[2], r[3]) > 2.2 else 10.0
    if room.area < 6.0:
        canvas.text((cx, cy + 0.14), room.name, size=size - 1, weight="bold", fill="#26313c")
        canvas.text((cx, cy - 0.24), f"{room.area:,.1f} m\u00b2", size=9.0, fill=DIM_COLOR)
        return
    canvas.text((cx, cy + 0.3), room.name, size=size, weight="bold", fill="#26313c")
    dims = f"{room.w * 3.28084:.1f}'-{room.d * 3.28084:.1f}' ({room.w:.2f}x{room.d:.2f} m)"
    canvas.text((cx, cy - 0.06), dims, size=9.5, fill=DIM_COLOR)
    area = f"{room.area:,.2f} m\u00b2 / {m2_to_sqft(room.area):,.0f} sq ft"
    canvas.text((cx, cy - 0.42), area, size=9.5, fill=DIM_COLOR)


def _draw_walls(canvas: Canvas, floor: Floor, opts: Optional[PlanOptions] = None) -> None:
    """Walls are drawn as real thicknesses, not centre lines."""
    hidden = bool(opts and getattr(opts, "hide_walls", False))
    for wall in derive_walls(floor.rooms):
        x, y, w, d = wall.box()
        if hidden:
            canvas.rect(x, y, w, d, fill="none", stroke="#c3ccd4", stroke_width=0.8,
                        dash="4 4")
            continue
        canvas.rect(x, y, w, d, fill=WALL_COLOR if wall.kind == "exterior" else "#5b6a78",
                    stroke="none")


def _draw_openings(canvas: Canvas, floor: Floor, opts: PlanOptions) -> None:
    if not opts.show_openings:
        return
    rooms = {r.id: r for r in floor.rooms}
    for op in floor.openings:
        room = rooms.get(op.room_id)
        if room is None:
            continue
        r = room_rect(room)
        offset = op.clamped_offset(room)
        horizontal = op.edge in ("N", "S")
        if horizontal:
            x0 = r[0] + offset
            x1 = x0 + op.width
            y = r[1] if op.edge == "S" else r[1] + r[3]
            a, b = (x0, y), (x1, y)
        else:
            y0 = r[1] + offset
            y1 = y0 + op.width
            x = r[0] if op.edge == "W" else r[0] + r[2]
            a, b = (x, y0), (x, y1)
        if op.kind == "window":
            canvas.line(a, b, stroke="#ffffff", stroke_width=4.0)
            canvas.line(a, b, stroke="#3f7fd6", stroke_width=1.6)
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if horizontal:
                canvas.line((mid[0], mid[1] - 0.09), (mid[0], mid[1] + 0.09), stroke="#3f7fd6",
                            stroke_width=1.0)
            else:
                canvas.line((mid[0] - 0.09, mid[1]), (mid[0] + 0.09, mid[1]), stroke="#3f7fd6",
                            stroke_width=1.0)
        elif op.kind in ("door", "sliding"):
            canvas.line(a, b, stroke="#ffffff", stroke_width=4.5)
            hinge = a
            if op.kind == "sliding":
                canvas.line(a, b, stroke="#7a5a3c", stroke_width=2.2, dash="4 3")
                continue
            canvas.line(hinge, b, stroke="#7a5a3c", stroke_width=2.0)
            inward = 1 if op.edge in ("S", "W") else -1
            if horizontal:
                canvas.arc(hinge, op.width, 0 if op.edge == "S" else 180,
                           90 if op.edge == "S" else 270, stroke="#9aa4ae", stroke_width=1.0)
            else:
                canvas.arc(hinge, op.width, 270 if op.edge == "W" else 90,
                           0 if op.edge == "W" else 180, stroke="#9aa4ae", stroke_width=1.0)
        else:
            canvas.line(a, b, stroke="#ffffff", stroke_width=3.0)
            canvas.line(a, b, stroke="#8a94a0", stroke_width=1.2, dash="3 3")


def _draw_stairs(canvas: Canvas, floor: Floor) -> None:
    for stair in floor.stairs:
        x, y, w, d = stair.x, stair.y, stair.w, stair.d
        canvas.rect(x, y, w, d, fill="#eceae5", stroke="#8a94a0", stroke_width=1.0)
        flights = max(1, stair.flights)
        per_flight = max(2, int(round(stair.risers / flights)))
        tread = stair.tread_m or 0.27
        flight_width = (w - 0.2) / flights
        for f in range(flights):
            fx = x + 0.1 + f * flight_width
            for t in range(per_flight):
                ty = y + 0.15 + t * tread
                if ty + 0.05 > y + d - 0.15:
                    break
                canvas.line((fx, ty), (fx + flight_width - 0.05, ty), stroke="#98a2ad",
                            stroke_width=0.9)
        arrow_x = x + w / 2
        canvas.line((arrow_x, y + 0.3), (arrow_x, y + d - 0.4), stroke="#5d6874",
                    stroke_width=1.4)
        canvas.polyline([(arrow_x - 0.16, y + d - 0.62), (arrow_x, y + d - 0.35),
                         (arrow_x + 0.16, y + d - 0.62)], stroke="#5d6874", stroke_width=1.4)
        canvas.text((arrow_x + 0.35, y + d / 2), "UP" if stair.goes_up else "DOWN", size=9.5,
                    anchor="start", fill="#5d6874", rotate=-90)


def _draw_furniture(canvas: Canvas, floor: Floor, opts: PlanOptions) -> None:
    if not opts.show_furniture:
        return
    for item in floor.furniture:
        if item.z and item.z >= 1.6:
            continue
        x = item.x - item.w / 2
        y = item.y - item.d / 2
        canvas.rect(x, y, item.w, item.d, fill="none", stroke=FURNITURE_COLOR,
                    stroke_width=1.0, opacity=0.9)
        if item.kind in ("bed", "sofa", "dining_table", "study_table", "wardrobe"):
            canvas.line((x, y), (x + item.w, y + item.d), stroke=FURNITURE_COLOR,
                        stroke_width=0.6, opacity=0.6)
        room_area = 99.0
        if min(item.w, item.d) * canvas.scale > 30 and (item.label or item.kind):
            canvas.text((item.x, item.y), (item.label or item.kind).replace("_", " ")[:14],
                        size=8.0, fill="#7b858f")


def _draw_dimensions(canvas: Canvas, floor: Floor, opts: PlanOptions, data: ProjectData) -> None:
    if not opts.show_dimensions:
        return
    plot = data.plot
    y_line = -opts.margin_m * 0.35
    canvas.line((0, y_line), (plot.width, y_line), stroke=DIM_COLOR, stroke_width=0.9)
    for x in (0, plot.width):
        canvas.line((x, y_line - 0.12), (x, y_line + 0.12), stroke=DIM_COLOR, stroke_width=0.9)
    canvas.text((plot.width / 2, y_line - 0.34),
                f"{plot.width * 3.28084:.1f} ft ({plot.width:.2f} m)", size=10, fill=DIM_COLOR)
    x_line = -opts.margin_m * 0.35
    canvas.line((x_line, 0), (x_line, plot.depth), stroke=DIM_COLOR, stroke_width=0.9)
    for y in (0, plot.depth):
        canvas.line((x_line - 0.12, y), (x_line + 0.12, y), stroke=DIM_COLOR, stroke_width=0.9)
    canvas.text((x_line - 0.3, plot.depth / 2), f"{plot.depth * 3.28084:.1f} ft ({plot.depth:.2f} m)",
                size=10, fill=DIM_COLOR, rotate=-90)
    # per-room overall dimensions along the outer edge of the floor
    rooms = [r for r in floor.rooms if not r.is_outdoor] if floor else []
    if not rooms:
        return
    x0 = min(r.x for r in rooms)
    x1 = max(r.x + r.w for r in rooms)
    y0 = min(r.y for r in rooms)
    y1 = max(r.y + r.d for r in rooms)
    canvas.line((x0, y1 + 0.35), (x1, y1 + 0.35), stroke="#9aa4ae", stroke_width=0.8)
    canvas.text(((x0 + x1) / 2, y1 + 0.62),
                f"building {x1 - x0:.2f} m ({(x1 - x0) * 3.28084:.1f} ft)", size=9.5,
                fill=DIM_COLOR)
    canvas.line((x1 + 0.35, y0), (x1 + 0.35, y1), stroke="#9aa4ae", stroke_width=0.8)
    canvas.text((x1 + 0.62, (y0 + y1) / 2),
                f"{y1 - y0:.2f} m ({(y1 - y0) * 3.28084:.1f} ft)", size=9.5, fill=DIM_COLOR,
                rotate=-90)


def render_floor(data: ProjectData, floor: Floor, opts: PlanOptions, scale: float = 40.0) -> Canvas:
    x0, y0, x1, y1 = _bounds(data, [floor], opts)
    width_px = (x1 - x0) * scale + 40
    height_px = (y1 - y0) * scale + 130
    canvas = Canvas(width=width_px, height=height_px, scale=scale, ox=x0, oy=y0)
    _draw_plot(canvas, data, opts)
    if opts.show_site:
        for room in floor.rooms:
            if room.is_outdoor:
                _draw_room(canvas, room, opts, data)
    for room in floor.rooms:
        if not room.is_outdoor:
            _draw_room(canvas, room, opts, data)
    _draw_walls(canvas, floor, opts)
    _draw_openings(canvas, floor, opts)
    _draw_stairs(canvas, floor)
    _draw_furniture(canvas, floor, opts)
    _draw_dimensions(canvas, floor, opts, data)
    _north_arrow(canvas, (x1 - 0.9, y1 - 1.1))
    _scale_bar(canvas, (x0 + 0.5, y0 + 2.55), metres=min(5.0, max(2.0, (x1 - x0) / 3)))
    _title_block(canvas, data, floor, opts, x0, y0 - 0.2, x1, y1 + 0.4)
    return canvas


def render_all(data: ProjectData, opts: PlanOptions, scale: float = 34.0) -> Canvas:
    floors = [f for f in data.floors if f.rooms]
    x0, y0, x1, y1 = _bounds(data, floors, opts)
    columns = max(1, min(len(floors), 3))
    rows = math.ceil(len(floors) / columns)
    cell_w = (x1 - x0) * scale + 30
    cell_h = (y1 - y0) * scale + 60
    canvas = Canvas(width=cell_w * columns + 40, height=cell_h * rows + 150, scale=scale,
                    ox=x0, oy=y0)
    _title_block(canvas, data, None, opts, x0, y0 - 1.6, x1, y1 + 0.4)
    for index, floor in enumerate(floors):
        col = index % columns
        row = index // columns
        dx = col * (cell_w / scale)
        dy = -row * (cell_h / scale)
        sub = Canvas(width=canvas.width, height=canvas.height, scale=scale, ox=x0 - dx,
                     oy=y0 - dy)
        _draw_plot(sub, data, PlanOptions(**{**opts.__dict__, "show_grid": False,
                                             "show_furniture": False}))
        for room in floor.rooms:
            _draw_room(sub, room, opts, data)
        _draw_walls(sub, floor, opts)
        _draw_openings(sub, floor, opts)
        _draw_stairs(sub, floor)
        sub.text((x0 + (x1 - x0) / 2, y1 + 0.2), floor.name, size=13, weight="bold")
        canvas.items.extend(sub.items)
    return canvas


# ---------------------------------------------------------------------------
# backends
# ---------------------------------------------------------------------------

def to_svg(canvas: Canvas) -> str:
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas.width:.0f}" '
        f'height="{canvas.height:.0f}" viewBox="0 0 {canvas.width:.0f} {canvas.height:.0f}" '
        f'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{canvas.width:.0f}" height="{canvas.height:.0f}" fill="#ffffff"/>',
    ]
    for kind, attrs in canvas.items:
        if kind == "rect":
            dash = f' stroke-dasharray="{attrs["dash"]}"' if attrs.get("dash") else ""
            stroke = f' stroke="{attrs["stroke"]}" stroke-width="{attrs["stroke_width"]:.2f}"' \
                if attrs.get("stroke") else ""
            parts.append(f'<rect x="{attrs["x"]:.2f}" y="{attrs["y"]:.2f}" width="{attrs["w"]:.2f}" '
                         f'height="{attrs["h"]:.2f}" fill="{attrs["fill"]}"{stroke}{dash} '
                         f'opacity="{attrs["opacity"]:.2f}"/>')
        elif kind == "line":
            dash = f' stroke-dasharray="{attrs["dash"]}"' if attrs.get("dash") else ""
            parts.append(f'<line x1="{attrs["x1"]:.2f}" y1="{attrs["y1"]:.2f}" '
                         f'x2="{attrs["x2"]:.2f}" y2="{attrs["y2"]:.2f}" '
                         f'stroke="{attrs["stroke"]}" stroke-width="{attrs["stroke_width"]:.2f}" '
                         f'opacity="{attrs["opacity"]:.2f}"{dash}/>')
        elif kind == "polyline":
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in attrs["points"])
            dash = f' stroke-dasharray="{attrs["dash"]}"' if attrs.get("dash") else ""
            parts.append(f'<polyline points="{pts}" fill="{attrs["fill"]}" '
                         f'stroke="{attrs["stroke"]}" stroke-width="{attrs["stroke_width"]:.2f}" '
                         f'opacity="{attrs["opacity"]:.2f}" stroke-linejoin="round"{dash}/>')
        elif kind == "circle":
            parts.append(f'<circle cx="{attrs["cx"]:.2f}" cy="{attrs["cy"]:.2f}" '
                         f'r="{attrs["r"]:.2f}" fill="{attrs["fill"]}" stroke="{attrs["stroke"]}" '
                         f'stroke-width="{attrs["stroke_width"]:.2f}"/>')
        elif kind == "arc":
            cx, cy = attrs["centre"]
            r = attrs["r"]
            a0 = math.radians(-attrs["start"])
            a1 = math.radians(-attrs["end"])
            sx, sy = cx + r * math.cos(a0), cy + r * math.sin(a0)
            ex, ey = cx + r * math.cos(a1), cy + r * math.sin(a1)
            large = 1 if abs(attrs["end"] - attrs["start"]) > 180 else 0
            parts.append(f'<path d="M {sx:.2f} {sy:.2f} A {r:.2f} {r:.2f} 0 {large} 1 '
                         f'{ex:.2f} {ey:.2f}" fill="none" stroke="{attrs["stroke"]}" '
                         f'stroke-width="{attrs["stroke_width"]:.2f}"/>')
        elif kind == "text":
            anchor = {"start": "start", "middle": "middle", "end": "end"}[attrs["anchor"]]
            baseline = {"middle": "central", "top": "hanging", "bottom": "auto"}[attrs["baseline"]]
            transform = f' transform="rotate({attrs["rotate"]:.1f} {attrs["x"]:.2f} ' \
                        f'{attrs["y"]:.2f})"' if attrs.get("rotate") else ""
            value = (str(attrs["value"]).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))
            weight = f' font-weight="{attrs["weight"]}"' if attrs["weight"] != "normal" else ""
            parts.append(f'<text x="{attrs["x"]:.2f}" y="{attrs["y"]:.2f}" '
                         f'font-size="{attrs["size"]:.1f}" fill="{attrs["fill"]}" '
                         f'text-anchor="{anchor}" dominant-baseline="{baseline}"{weight}'
                         f'{transform}>{value}</text>')
    parts.append("</svg>")
    return "".join(parts)


def to_png(canvas: Canvas, background: str = "#ffffff") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    scale = 2  # supersample then downscale for crisp text
    img = Image.new("RGB", (int(canvas.width * scale), int(canvas.height * scale)), background)
    draw = ImageDraw.Draw(img)
    fonts: Dict[Tuple[float, str], Any] = {}

    def font_for(size: float, weight: str):
        key = (round(size * scale), weight)
        if key not in fonts:
            bold = weight == "bold"
            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold
                else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            ]
            chosen = next((path for path in candidates if _exists(path)), None)
            try:
                fonts[key] = ImageFont.truetype(chosen, int(size * scale)) if chosen \
                    else ImageFont.load_default()
            except Exception:
                fonts[key] = ImageFont.load_default()
        return fonts[key]

    for kind, a in canvas.items:
        if kind == "rect":
            box = [a["x"] * scale, a["y"] * scale, (a["x"] + a["w"]) * scale,
                   (a["y"] + a["h"]) * scale]
            if a["fill"] and a["fill"] != "none":
                draw.rectangle(box, fill=a["fill"])
            stroke = a["stroke"] if a["stroke"] not in ("", "none", None) else None
            if stroke:
                width = max(1, int(a["stroke_width"] * scale))
                if a.get("dash"):
                    _dashed_rect(draw, box, stroke, width)
                else:
                    draw.rectangle(box, outline=stroke, width=width)
        elif kind == "line":
            width = max(1, int(a["stroke_width"] * scale))
            p1 = (a["x1"] * scale, a["y1"] * scale)
            p2 = (a["x2"] * scale, a["y2"] * scale)
            if a.get("dash"):
                _dashed_line(draw, p1, p2, a["stroke"], width)
            else:
                draw.line([p1, p2], fill=a["stroke"], width=width)
        elif kind == "polyline":
            pts = [(x * scale, y * scale) for x, y in a["points"]]
            if a["fill"] and a["fill"] != "none":
                draw.polygon(pts, fill=a["fill"])
            if a["stroke"] and a["stroke"] != "none":
                draw.line(pts + [pts[0]], fill=a["stroke"],
                          width=max(1, int(a["stroke_width"] * scale)), joint="curve")
        elif kind == "circle":
            r = a["r"] * scale
            box = [a["cx"] * scale - r, a["cy"] * scale - r, a["cx"] * scale + r,
                   a["cy"] * scale + r]
            draw.ellipse(box, fill=a["fill"] if a["fill"] not in ("none", "") else None,
                         outline=a["stroke"] if a["stroke"] not in ("none", "") else None,
                         width=max(1, int(a["stroke_width"] * scale)))
        elif kind == "arc":
            cx, cy = a["centre"]
            r = a["r"] * scale
            box = [cx * scale - r, cy * scale - r, cx * scale + r, cy * scale + r]
            draw.arc(box, start=-a["end"], end=-a["start"], fill=a["stroke"],
                     width=max(1, int(a["stroke_width"] * scale)))
        elif kind == "text":
            f = font_for(a["size"], a["weight"])
            anchor = {"start": "ls", "middle": "ms", "end": "rs"}[a["anchor"]]
            if a.get("rotate"):
                _rotated_text(img, draw, (a["x"] * scale, a["y"] * scale), str(a["value"]), f,
                              a["fill"], a["rotate"], anchor)
            else:
                draw.text((a["x"] * scale, a["y"] * scale), str(a["value"]), font=f,
                          fill=a["fill"], anchor=anchor)
    del draw
    img = img.resize((int(canvas.width), int(canvas.height)), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _exists(path: str) -> bool:
    return os.path.exists(path)


def _dashed_line(draw, p1, p2, color, width, dash=7, gap=5) -> None:
    x1, y1 = p1
    x2, y2 = p2
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 0:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    travelled = 0.0
    drawing = True
    while travelled < length:
        step = dash if drawing else gap
        end = min(travelled + step, length)
        if drawing:
            draw.line([(x1 + ux * travelled, y1 + uy * travelled),
                       (x1 + ux * end, y1 + uy * end)], fill=color, width=width)
        travelled = end
        drawing = not drawing


def _dashed_rect(draw, box, color, width) -> None:
    x0, y0, x1, y1 = box
    _dashed_line(draw, (x0, y0), (x1, y0), color, width)
    _dashed_line(draw, (x1, y0), (x1, y1), color, width)
    _dashed_line(draw, (x1, y1), (x0, y1), color, width)
    _dashed_line(draw, (x0, y1), (x0, y0), color, width)


def _rotated_text(img, draw, at, value, font, fill, degrees, anchor) -> None:
    from PIL import Image as PILImage, ImageDraw as PILDraw
    size = getattr(font, "size", 12)
    try:
        text_width = PILDraw.Draw(img).textlength(value, font=font)
    except Exception:
        text_width = len(value) * size * 0.62
    tmp = PILImage.new("RGBA", (int(text_width) + 24, int(size * 2.4) + 8), (0, 0, 0, 0))
    PILDraw.Draw(tmp).text((12, tmp.size[1] / 2), value, font=font, fill=fill, anchor="lm")
    rotated = tmp.rotate(-degrees, expand=True, resample=PILImage.BICUBIC)
    img.paste(rotated, (int(at[0] - rotated.size[0] / 2), int(at[1] - rotated.size[1] / 2)),
              rotated)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def select_floor(data: ProjectData, opts: PlanOptions) -> Optional[Floor]:
    if opts.floor_id:
        return data.floor(opts.floor_id)
    return data.floors[0] if data.floors else None


def plan_svg(data: ProjectData, **kwargs: Any) -> str:
    opts = PlanOptions(**kwargs)
    if opts.all_floors:
        return to_svg(render_all(data, opts))
    floor = select_floor(data, opts)
    if floor is None:
        return to_svg(Canvas(width=400, height=200))
    return to_svg(render_floor(data, floor, opts))


def plan_png(data: ProjectData, scale: float = 40.0, **kwargs: Any) -> bytes:
    opts = PlanOptions(**kwargs)
    if opts.all_floors:
        return to_png(render_all(data, opts, scale=scale))
    floor = select_floor(data, opts)
    if floor is None:
        return to_png(Canvas(width=400, height=200))
    return to_png(render_floor(data, floor, opts, scale=scale))


def floor_pngs(data: ProjectData, scale: float = 40.0, **kwargs: Any) -> List[Tuple[str, bytes]]:
    opts = PlanOptions(**kwargs)
    out: List[Tuple[str, bytes]] = []
    for floor in data.floors:
        if not floor.rooms:
            continue
        out.append((floor.name, to_png(render_floor(data, floor, opts, scale=scale))))
    return out
