"""Design report generation (PDF) plus machine readable exports.

The report is drawn page by page with Pillow and assembled into a PDF. It always
ends with the professional-review and non-construction-drawing notices, and every
schedule states the units and the fact that dimensions are to wall centre lines.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from ...schemas import ProjectData, utcnow
from ..budget import estimate_budget
from ..units import m2_to_sqft
from ..validation import validation_result
from .pdf import A4, Pdf
from .plan2d import PlanOptions, render_floor, to_png

PAGE_W, PAGE_H = 1240, 1754          # ~150 dpi A4 portrait
MARGIN = 78
INK = (38, 49, 60)
SOFT = (107, 119, 132)
ACCENT = (63, 127, 214)
WARN = (138, 90, 43)
CRIT = (158, 51, 41)
RULE = (214, 220, 226)
ZEBRA = (246, 248, 250)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
             else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Page:
    def __init__(self, title: str, subtitle: str = "") -> None:
        self.img = Image.new("RGB", (PAGE_W, PAGE_H), "#ffffff")
        self.draw = ImageDraw.Draw(self.img)
        self.y = MARGIN
        self.title = title
        self.subtitle = subtitle
        self._header()

    def _header(self) -> None:
        self.draw.rectangle([0, 0, PAGE_W, 10], fill=ACCENT)
        self.draw.text((MARGIN, MARGIN - 6), self.title, font=_font(34, True), fill=INK)
        self.y = MARGIN + 52
        if self.subtitle:
            self.draw.text((MARGIN, self.y - 8), self.subtitle, font=_font(17), fill=SOFT)
            self.y += 26
        self.draw.line([MARGIN, self.y - 12, PAGE_W - MARGIN, self.y - 12], fill=RULE, width=2)

    def text(self, value: str, size: int = 17, color: Tuple[int, int, int] = INK,
             bold: bool = False, indent: int = 0, gap: int = 8) -> None:
        for line in _wrap(self.draw, value, _font(size, bold), PAGE_W - 2 * MARGIN - indent):
            self.draw.text((MARGIN + indent, self.y), line, font=_font(size, bold), fill=color)
            self.y += int(size * 1.45)
        self.y += gap

    def gap(self, value: int = 16) -> None:
        self.y += value

    def rule(self) -> None:
        self.draw.line([MARGIN, self.y, PAGE_W - MARGIN, self.y], fill=RULE, width=2)
        self.y += 18

    def table(self, header: List[str], rows: List[List[str]], widths: List[float],
              sizes: int = 15, zebra: bool = True) -> None:
        left = MARGIN
        total = PAGE_W - 2 * MARGIN
        cols = [int(w * total) for w in widths]
        row_h = int(sizes * 1.9)
        self.draw.rectangle([left, self.y, left + sum(cols), self.y + row_h], fill=(233, 239, 246))
        x = left
        for index, cell in enumerate(header):
            self.draw.text((x + 8, self.y + 6), cell, font=_font(sizes, True), fill=INK)
            x += cols[index]
        self.y += row_h
        self.draw.line([left, self.y, left + sum(cols), self.y], fill=RULE, width=1)
        for r_index, row in enumerate(rows):
            cell_heights = []
            wrapped: List[List[str]] = []
            for index, cell in enumerate(row):
                lines = _wrap(self.draw, str(cell), _font(sizes), cols[index] - 16)
                wrapped.append(lines)
                cell_heights.append(len(lines))
            h = max(1, max(cell_heights)) * int(sizes * 1.35) + 10
            if self.y + h > PAGE_H - MARGIN - 40:
                break
            if zebra and r_index % 2 == 1:
                self.draw.rectangle([left, self.y, left + sum(cols), self.y + h], fill=ZEBRA)
            x = left
            for index, lines in enumerate(wrapped):
                yy = self.y + 6
                for line in lines:
                    self.draw.text((x + 8, yy), line, font=_font(sizes), fill=INK)
                    yy += int(sizes * 1.35)
                x += cols[index]
            self.y += h
            self.draw.line([left, self.y, left + sum(cols), self.y], fill=(232, 236, 240), width=1)
        self.y += 18

    def image(self, blob: bytes, width: int = PAGE_W - 2 * MARGIN) -> None:
        img = Image.open(io.BytesIO(blob)).convert("RGB")
        ratio = width / img.width
        target_h = int(img.height * ratio)
        if self.y + target_h > PAGE_H - MARGIN:
            target_h = PAGE_H - MARGIN - self.y
            width = int(img.width * (target_h / img.height))
        img = img.resize((width, target_h), Image.LANCZOS)
        self.img.paste(img, (MARGIN, self.y))
        self.y += target_h + 18

    def footer(self, note: str) -> None:
        self.draw.line([MARGIN, PAGE_H - MARGIN - 26, PAGE_W - MARGIN, PAGE_H - MARGIN - 26],
                       fill=RULE, width=2)
        self.draw.text((MARGIN, PAGE_H - MARGIN - 16), note, font=_font(13), fill=SOFT)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    out: List[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            try:
                w = draw.textlength(candidate, font=font)
            except Exception:
                w = len(candidate) * 8
            if w > max_width and current:
                out.append(current)
                current = word
            else:
                current = candidate
        out.append(current)
    return out or [""]


DISCLAIMER = ("This report is a preliminary design aid produced by a rule-based planning engine. "
              "It is not a construction drawing, not a structural design and not evidence of code "
              "compliance. A licensed architect and structural engineer must review and take "
              "responsibility for anything built from it.")


def build_pages(data: ProjectData, include_plans: bool = True) -> List[Page]:
    pages: List[Page] = []
    metrics = data.metrics
    plot = data.plot
    req = data.requirements
    now = utcnow()

    # 1 cover -----------------------------------------------------------------
    page = Page(data.name or "Untitled House Project",
                f"Design report | generated {now:%d %b %Y %H:%M} UTC")
    page.text(f"{plot.city or 'Location not set'}{', ' + plot.state if plot.state else ''} "
              f"({plot.country or 'India'})", 20, SOFT)
    page.gap(6)
    page.text(f"Plot {plot.width * 3.28084:,.1f} ft x {plot.depth * 3.28084:,.1f} ft "
              f"({plot.width:.2f} m x {plot.depth:.2f} m) = {plot.area:,.1f} m\u00b2 "
              f"({m2_to_sqft(plot.area):,.0f} sq ft), {plot.shape.replace('_', ' ')}", 19)
    page.text(f"Road on the {plot.road_side} side | {req.floors} floor(s) | {req.bedrooms} "
              f"bedroom(s), {req.bathrooms} bathroom(s) | concept: "
              f"{(data.active_concept or 'n/a').replace('_', ' ')} | style: "
              f"{req.style.replace('_', ' ')}", 19)
    page.gap(4)
    page.rule()
    page.table(["Metric", "Value"], [
        ["Total built-up area", f"{metrics.total_built_up_m2:,.2f} m\u00b2 "
                                f"({metrics.total_built_up_sqft:,.0f} sq ft)"],
        ["Ground coverage", f"{metrics.ground_coverage_pct:.1f}% of the plot"],
        ["Buildable area after setbacks", f"{metrics.buildable_area_m2:,.2f} m\u00b2"],
        ["Open space", f"{metrics.open_space_m2:,.2f} m\u00b2 ({metrics.open_space_pct:.1f}%)"],
        ["Circulation share", f"{metrics.circulation_pct:.1f}% of built-up area"],
        ["Rooms (indoor + outdoor)", f"{metrics.room_count}"],
        ["Floors modelled", ", ".join(f.name for f in data.floors) or "none"],
        ["Parking", f"{metrics.parking_area_m2:,.2f} m\u00b2"],
    ], [0.42, 0.58], sizes=16)
    page.gap(8)
    page.text("What this document is and is not", 20, CRIT, bold=True)
    page.text(DISCLAIMER, 16, WARN)
    page.text("Every dimension is measured to wall centre lines. Clear (carpet) dimensions are "
              "smaller by the wall thickness: 230 mm external, 115 mm internal.", 15, SOFT)
    page.footer("AI Home Designer -- preliminary design aid. Not a construction document.")
    pages.append(page)

    # 2 brief, plot and assumptions -------------------------------------------
    page = Page("Design brief, plot and assumptions")
    page.text("Requirements as entered", 20, INK, bold=True)
    brief_rows = [
        ["Floors", str(req.floors)], ["Bedrooms", str(req.bedrooms)],
        ["Bathrooms (incl. attached)", f"{req.bathrooms} ({req.attached_baths} attached)"],
        ["Kitchen", req.kitchen_type], ["Living room", "yes" if req.wants_living else "no"],
        ["Dining", "yes" if req.wants_dining else "no"],
        ["Parking", req.wants_parking.replace("_", " ")],
        ["Balcony / terrace", f"{'yes' if req.wants_balcony else 'no'} / "
                              f"{'yes' if req.wants_terrace else 'no'}"],
        ["Garden / courtyard", f"{'yes' if req.wants_garden else 'no'} / "
                               f"{'yes' if req.wants_courtyard else 'no'}"],
        ["Prayer room / study / store", f"{'yes' if req.wants_pooja else 'no'} / "
                                        f"{'yes' if req.wants_study else 'no'} / "
                                        f"{'yes' if req.wants_store else 'no'}"],
        ["Utility / servant room / shop", f"{'yes' if req.wants_utility else 'no'} / "
                                          f"{'yes' if req.wants_servant_room else 'no'} / "
                                          f"{'yes' if req.wants_shop else 'no'}"],
        ["Family size", str(req.family_size)],
        ["Accessibility needs", "yes" if req.accessibility else "no"],
        ["Vastu guidance", "on (cultural preference only)" if req.vastu else "off"],
        ["Future expansion", req.future_expansion or "not stated"],
        ["Budget target", f"{req.currency} {req.budget_target:,.0f}" if req.budget_target
                          else "not stated"],
    ]
    page.table(["Item", "Value"], brief_rows, [0.45, 0.55], sizes=15)
    page.text("Setbacks used (these drive everything else)", 20, INK, bold=True)
    sb = plot.setbacks
    page.table(["Side", "Setback (m)", "Source"], [
        ["North", f"{sb.north:.2f}", sb.source], ["South", f"{sb.south:.2f}", sb.source],
        ["East", f"{sb.east:.2f}", sb.source], ["West", f"{sb.west:.2f}", sb.source],
    ], [0.25, 0.3, 0.45], sizes=15)
    if not sb.verified:
        page.text("Setbacks are assumptions, not verified byelaw values. Confirm them with your "
                  "local planning authority before designing to these limits.", 15, WARN)
    page.gap(6)
    page.text("Assumptions carried by this design", 20, INK, bold=True)
    for assumption in data.assumptions:
        color = {"info": SOFT, "warning": WARN, "critical": CRIT}[assumption.severity]
        page.text(f"[{assumption.severity}] {assumption.topic}: {assumption.statement}", 15, color)
    page.footer("AI Home Designer -- preliminary design aid. Not a construction document.")
    pages.append(page)

    # 3 per-floor pages ---------------------------------------------------------
    if include_plans:
        for floor in data.floors:
            if not floor.rooms:
                continue
            page = Page(floor.name, f"Floor plan and room schedule")
            png = to_png(render_floor(data, floor, PlanOptions(show_furniture=True,
                                                                show_dimensions=True,
                                                                show_setbacks=False,
                                                                include_disclaimer=False),
                                      scale=34.0))
            page.image(png)
            rows = []
            for room in floor.rooms:
                rows.append([room.name, room.type.replace("_", " "),
                             f"{room.w:.2f} x {room.d:.2f}",
                             f"{room.area:,.2f} ({m2_to_sqft(room.area):,.0f})",
                             "yes" if room.is_outdoor else "no"])
            page.table(["Room", "Type", "Size (m)", "Area m\u00b2 (sq ft)", "Outdoor"], rows,
                       [0.30, 0.20, 0.17, 0.23, 0.10], sizes=14)
            openings = [op for op in floor.openings]
            if openings:
                page.text("Doors and windows on this floor", 18, INK, bold=True)
                rows = []
                for op in openings[:26]:
                    room = floor.room(op.room_id)
                    rows.append([room.name if room else "?", op.kind, op.edge,
                                 f"{op.width * 1000:.0f} mm",
                                 f"{op.height * 1000:.0f} mm sill {op.sill * 1000:.0f} mm",
                                 op.connects_to or ""])
                page.table(["Room", "Kind", "Wall", "Width", "Height / sill", "Connects to"], rows,
                           [0.26, 0.12, 0.08, 0.13, 0.25, 0.16], sizes=13)
            page.footer("AI Home Designer -- preliminary design aid. Not a construction document.")
            pages.append(page)

    # 4 schedules ---------------------------------------------------------------
    page = Page("Material, finish and furniture schedules")
    m = data.materials
    page.text("Global finishes", 20, INK, bold=True)
    page.table(["Surface", "Choice"], [
        ["Exterior wall", f"{m.exterior_wall} (accent {m.exterior_accent})"],
        ["Interior wall", m.interior_wall], ["Feature wall", m.feature_wall],
        ["Flooring", f"{m.floor_material} in {m.floor_color}"],
        ["Bathroom tile", m.bathroom_tile],
        ["Kitchen", f"cabinets {m.kitchen_cabinet}, counter {m.kitchen_counter}"],
        ["Ceiling", m.ceiling_style], ["Roof", f"{m.roof_type} in {m.roof_color}"],
        ["Doors / windows", f"{m.door_material} / frame {m.window_frame}"],
        ["Facade", m.facade_style],
        ["Site", f"boundary wall {'yes' if m.boundary_wall else 'no'}, landscaping "
                 f"{'yes' if m.landscaping else 'no'}, exterior lighting "
                 f"{'yes' if m.exterior_lighting else 'no'}"],
    ], [0.30, 0.70], sizes=15)
    overrides = [(f.name, r) for f in data.floors for r in f.rooms
                 if r.wall_color or r.floor_material or r.color]
    if overrides:
        page.text("Room-level overrides", 18, INK, bold=True)
        page.table(["Floor", "Room", "Wall", "Floor"],
                   [[f, r.name, r.wall_color or "-", f"{r.floor_material or '-'} "
                     f"{r.color or ''}".strip() or "-"] for f, r in overrides],
                   [0.22, 0.30, 0.22, 0.26], sizes=14)
    page.text("Furniture and fixtures (for costing and procurement, not a purchase order)",
              20, INK, bold=True)
    counts: Dict[Tuple[str, str], int] = {}
    for floor in data.floors:
        for item in floor.furniture:
            key = (item.label or item.kind.replace("_", " ").title(), item.kind)
            counts[key] = counts.get(key, 0) + 1
    rows = [[label, kind, str(qty), f"{_furniture_size(data, kind)}"]
            for (label, kind), qty in sorted(counts.items())]
    page.table(["Item", "Type", "Qty", "Nominal size (m)"], rows[:48],
               [0.38, 0.22, 0.10, 0.30], sizes=14)
    page.footer("AI Home Designer -- preliminary design aid. Not a construction document.")
    pages.append(page)

    # 5 budget -------------------------------------------------------------------
    page = Page("Indicative budget estimate")
    try:
        est = estimate_budget(data)
    except Exception as exc:
        est = None
        page.text(f"The budget estimate could not be produced: {exc}", 16, CRIT)
    if est is not None:
        page.text(f"Finish level {est.finish_level} | city tier {est.city_tier} | built-up "
                  f"{est.built_up_m2:,.2f} m\u00b2", 17, SOFT)
        page.text(f"Indicative total: {est.currency} {est.total_low:,.0f} to "
                  f"{est.total_high:,.0f} (mid {est.total_mid:,.0f}) = "
                  f"{est.rate_per_sqft_low:,.0f}-{est.rate_per_sqft_high:,.0f} per sq ft", 19, INK,
                  bold=True)
        page.table(["Category", "Qty", "Rate low-high", "Amount low", "Amount high", "Verified"],
                   [[l.label, f"{l.quantity:,.1f} {l.unit}",
                     f"{l.rate_low:,.0f}-{l.rate_high:,.0f}", f"{l.amount_low:,.0f}",
                     f"{l.amount_high:,.0f}", "yes" if l.verified else "no"] for l in est.lines],
                   [0.30, 0.16, 0.18, 0.12, 0.12, 0.12], sizes=13)
        page.text("Estimate assumptions", 18, INK, bold=True)
        for line in est.assumptions:
            page.text(f"- {line}", 14, SOFT)
        for line in est.warnings:
            page.text(f"! {line}", 14, WARN)
        page.text(est.disclaimer, 15, CRIT)
    page.footer("AI Home Designer -- parametric estimate, not a quotation.")
    pages.append(page)

    # 6 validation and safety ------------------------------------------------------
    page = Page("Automated checks, warnings and what still needs a professional")
    result = validation_result(data)
    page.text(f"{result.counts.get('error', 0)} blocking issue(s), "
              f"{result.counts.get('warning', 0)} warning(s), "
              f"{result.counts.get('info', 0)} note(s). Automated checks only -- they do not "
              f"certify safety or compliance.", 17, INK)
    for issue in result.issues[:40]:
        color = {"error": CRIT, "warning": WARN, "info": SOFT}[issue.severity]
        page.text(f"[{issue.severity}] {issue.message}"
                  + (f" Suggested fix: {issue.suggested_fix}" if issue.suggested_fix else ""),
                  15, color)
    page.gap(6)
    page.text("Always needed before building", 20, INK, bold=True)
    for line in [
        "Structural design: foundations, columns, beams, slab spans and seismic detailing by a "
        "licensed structural engineer.",
        "Soil investigation and water table information from a geotechnical report.",
        "Municipal sanction: setbacks, coverage, FAR, height and parking as per your local byelaws.",
        "Electrical and plumbing design, including earthing, load calculation and drainage slopes.",
        "Fire safety: escape routes, stair widths and refuge areas as per the applicable code.",
    ]:
        page.text(f"- {line}", 15, INK)
    page.text(DISCLAIMER, 15, WARN)
    page.footer("AI Home Designer -- preliminary design aid. Not a construction document.")
    pages.append(page)
    return pages


def _furniture_size(data: ProjectData, kind: str) -> str:
    for floor in data.floors:
        for item in floor.furniture:
            if item.kind == kind:
                return f"{item.w:.2f} x {item.d:.2f} x {item.height:.2f} h"
    return "-"


def build_report_pdf(data: ProjectData, include_plans: bool = True) -> bytes:
    pdf = Pdf(title=f"{data.name} design report")
    for page in build_pages(data, include_plans=include_plans):
        page.footer("AI Home Designer -- preliminary design aid. Not a construction document.")
        pdf.add_image_page(page.img, A4)
    return pdf.save()


def build_plans_pdf(data: ProjectData) -> bytes:
    """One page per floor, plan only."""
    pdf = Pdf(title=f"{data.name} floor plans")
    for floor in data.floors:
        if not floor.rooms:
            continue
        png = to_png(render_floor(data, floor, PlanOptions(), scale=40.0))
        pdf.add_png_page(png, A4)
    return pdf.save()


# ---------------------------------------------------------------------------
# machine readable exports
# ---------------------------------------------------------------------------

def material_list_csv(data: ProjectData) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["scope", "surface", "choice", "note"])
    m = data.materials
    writer.writerow(["exterior", "wall", m.exterior_wall, "global finish"])
    writer.writerow(["exterior", "accent", m.exterior_accent, "global finish"])
    writer.writerow(["exterior", "facade", m.facade_style, "global finish"])
    writer.writerow(["exterior", "roof", f"{m.roof_type} {m.roof_color}", "global finish"])
    writer.writerow(["interior", "wall", m.interior_wall, "global finish"])
    writer.writerow(["interior", "feature wall", m.feature_wall, "global finish"])
    writer.writerow(["interior", "floor", f"{m.floor_material} {m.floor_color}", "global finish"])
    writer.writerow(["interior", "bathroom tile", m.bathroom_tile, "global finish"])
    writer.writerow(["interior", "kitchen cabinet", m.kitchen_cabinet, "global finish"])
    writer.writerow(["interior", "kitchen counter", m.kitchen_counter, "global finish"])
    for floor in data.floors:
        for room in floor.rooms:
            if room.wall_color or room.floor_material or room.color:
                writer.writerow(["room", room.name,
                                 f"wall={room.wall_color or '-'} floor={room.floor_material or '-'} "
                                 f"{room.color or ''}".strip(), floor.name])
    return buffer.getvalue()


def furniture_list_csv(data: ProjectData) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["floor", "room", "item", "kind", "x_m", "y_m", "w_m", "d_m", "height_m",
                     "base_z_m", "rotation_deg", "material", "colour"])
    for floor in data.floors:
        for item in floor.furniture:
            room = floor.room(item.room_id)
            writer.writerow([floor.name, room.name if room else "", item.label or item.kind,
                             item.kind, f"{item.x:.3f}", f"{item.y:.3f}", f"{item.w:.3f}",
                             f"{item.d:.3f}", f"{item.height:.2f}", f"{item.z:.2f}",
                             f"{item.rotation:.1f}", item.material, item.color])
    return buffer.getvalue()


def room_schedule_csv(data: ProjectData) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["floor", "room_id", "name", "type", "x_m", "y_m", "width_m", "depth_m",
                     "area_m2", "area_sqft", "outdoor", "wet", "ceiling_m"])
    for floor in data.floors:
        for room in floor.rooms:
            writer.writerow([floor.name, room.id, room.name, room.type, f"{room.x:.3f}",
                             f"{room.y:.3f}", f"{room.w:.3f}", f"{room.d:.3f}",
                             f"{room.area:.3f}", f"{m2_to_sqft(room.area):.1f}",
                             room.is_outdoor, room.is_wet, f"{room.ceiling_height:.2f}"])
    return buffer.getvalue()


def budget_csv(data: ProjectData) -> str:
    est = estimate_budget(data)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["category", "label", "quantity", "unit", "rate_low", "rate_high",
                     "amount_low", "amount_high", "amount_mid", "labour_share_pct", "verified"])
    for line in est.lines:
        writer.writerow([line.category, line.label, line.quantity, line.unit, line.rate_low,
                         line.rate_high, line.amount_low, line.amount_high, line.amount_mid,
                         line.labour_share_pct, line.verified])
    writer.writerow([])
    writer.writerow(["total_low", est.total_low])
    writer.writerow(["total_high", est.total_high])
    writer.writerow(["total_mid", est.total_mid])
    writer.writerow(["currency", est.currency])
    writer.writerow(["disclaimer", est.disclaimer])
    return buffer.getvalue()


def documented_json(data: ProjectData, extra: Optional[Dict[str, Any]] = None) -> str:
    """The full project document with an export envelope for humans and tools."""
    payload = {
        "export": {
            "generator": "AI Home Designer",
            "schema_version": data.schema_version,
            "exported_at": utcnow().isoformat(),
            "units": {"length": "metres", "area": "square metres", "plan_origin": "south-west "
                      "corner of the plot", "axes": "+x East, +y North",
                      "room_xy": "south-west corner of the room rectangle",
                      "furniture_xy": "centre of the item footprint",
                      "openings": "anchored to room_id + edge + offset from the edge start"},
            "coordinate_system": "metres, plan view, north = +y",
            "not_a_construction_document": True,
            "disclaimer": DISCLAIMER,
        },
        "project": data.model_dump(mode="json"),
    }
    if extra:
        payload["export"].update(extra)
    return json.dumps(payload, indent=2, default=str)
