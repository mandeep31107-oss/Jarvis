"""The built-in local provider.

No API key, no network, no pretending. Everything here is computed from the
project's own structured data (rooms, areas, openings, budget) or measured directly
from an uploaded image with Pillow. Answers are labelled as rule-based so the user
always knows which engine produced them.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Tuple

from ..units import m2_to_sqft
from .base import (DISCLAIMER, AIError, ImageAdapter, LLMAdapter, PRIVACY_NOTES,
                   SETUP_INSTRUCTIONS, ProviderInfo, VisionAdapter)

PROVIDER_KEY = "local"

# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------


def _fmt_area(m2: float) -> str:
    return f"{m2:,.1f} m\u00b2 ({m2_to_sqft(m2):,.0f} sq ft)"


def _rooms_by_floor(facts: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for floor in facts.get("floors", []):
        out.setdefault(floor.get("name", "Floor"), [])
    for room in facts.get("rooms", []):
        name = next((f.get("name", "Floor") for f in facts.get("floors", [])
                     if f.get("id") == room.get("floor")), "Floor")
        out.setdefault(name, []).append(room)
    return out


class LocalLLM(LLMAdapter):
    name = PROVIDER_KEY

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key=PROVIDER_KEY, label="Built-in local assistant",
            capabilities=["explain_design", "answer_questions", "style_advice",
                          "budget_commentary", "command_parsing", "image_inspection"],
            requires_key=False, configured=True,
            model="deterministic rule engine (on-device)",
            setup=SETUP_INSTRUCTIONS[PROVIDER_KEY], privacy=PRIVACY_NOTES[PROVIDER_KEY],
            offline=True)

    async def available(self) -> bool:
        return True

    async def complete(self, *, task: str, facts: Dict[str, Any], question: str = "",
                       system: str = "", json_mode: bool = False,
                       max_tokens: int = 900) -> str:
        handler = {
            "explain_design": self._explain_design,
            "explain_concept": self._explain_concept,
            "answer": self._answer,
            "style_advice": self._style_advice,
            "budget_commentary": self._budget_commentary,
            "compare": self._compare,
            "summary": self._summary,
            "assumptions": self._assumptions,
        }.get(task, self._answer)
        text = handler(facts, question)
        return text

    # -- individual tasks ---------------------------------------------------
    def _summary(self, facts: Dict[str, Any], question: str = "") -> str:
        metrics = facts.get("metrics", {})
        plot = facts.get("plot", {})
        req = facts.get("requirements", {})
        rooms = facts.get("rooms", [])
        lines = [
            f"{facts.get('name', 'This project')} sits on a "
            f"{plot.get('width_m', 0) * 3.28084:,.0f} x {plot.get('depth_m', 0) * 3.28084:,.0f} ft "
            f"({_fmt_area(plot.get('area_m2', 0))}) {plot.get('shape', 'rectangular')} plot with the "
            f"road on the {plot.get('road_side', 'S')} side.",
            f"Requirements: {req.get('floors', 1)} floor(s), {req.get('bedrooms', 0)} bedroom(s), "
            f"{req.get('bathrooms', 0)} bathroom(s), parking '{req.get('wants_parking', 'none')}'.",
            f"The current concept is '{facts.get('active_concept', 'n/a')}' with "
            f"{len(rooms)} rooms and {_fmt_area(metrics.get('total_built_up_m2', 0))} of built-up "
            f"area ({metrics.get('ground_coverage_pct', 0):.0f}% ground coverage, "
            f"{metrics.get('open_space_pct', 0):.0f}% open space).",
        ]
        return "\n".join(lines)

    def _explain_design(self, facts: Dict[str, Any], question: str = "") -> str:
        metrics = facts.get("metrics", {})
        plot = facts.get("plot", {})
        grouped = _rooms_by_floor(facts)
        parts: List[str] = [self._summary(facts, question), ""]
        for floor_name, rooms in grouped.items():
            if not rooms:
                continue
            indoor = [r for r in rooms if not r.get("outdoor")]
            area = sum(r.get("area_m2", 0) for r in indoor)
            parts.append(f"{floor_name} -- {_fmt_area(area)} across {len(indoor)} room(s):")
            for room in sorted(indoor, key=lambda r: -r.get("area_m2", 0)):
                parts.append(f"  - {room.get('name')}: {_fmt_area(room.get('area_m2', 0))}, "
                             f"{room.get('w', 0):.2f} m x {room.get('d', 0):.2f} m")
        parts.append("")
        parts.append(f"Circulation (corridors, lobby, stairs) is "
                     f"{metrics.get('circulation_pct', 0):.0f}% of the built-up area; open space is "
                     f"{_fmt_area(metrics.get('open_space_m2', 0))} "
                     f"({metrics.get('open_space_pct', 0):.0f}% of the plot).")
        if facts.get("assumptions"):
            parts.append("")
            parts.append("Assumptions this design is built on:")
            for assumption in facts["assumptions"][:12]:
                if isinstance(assumption, dict):
                    parts.append(f"  - [{assumption.get('severity', 'info')}] "
                                 f"{assumption.get('statement', '')}")
                else:
                    parts.append(f"  - {assumption}")
        parts.append("")
        parts.append(DISCLAIMER)
        return "\n".join(parts)

    def _explain_concept(self, facts: Dict[str, Any], question: str = "") -> str:
        lines = ["Concept comparison for this plot and brief:"]
        for concept in facts.get("concepts", []):
            active = " (active)" if concept.get("key") == facts.get("active_concept") else ""
            lines.append(
                f"  - {concept.get('label')}{active}: "
                f"{_fmt_area(concept.get('built_up_m2', 0))} built-up, "
                f"{concept.get('coverage_pct', 0):.0f}% coverage, "
                f"{concept.get('open_space_pct', 0):.0f}% open space")
        trade = facts.get("concept_trade_offs") or {}
        for key, items in trade.items():
            if items:
                lines.append(f"  {key.replace('_', ' ')}: " + "; ".join(items))
        lines.append("")
        lines.append("Switching concepts regenerates the geometry; changing materials or furniture "
                     "does not.")
        return "\n".join(lines)

    def _style_advice(self, facts: Dict[str, Any], question: str = "") -> str:
        style = facts.get("style", "modern")
        climate = facts.get("plot", {}).get("climate", "composite")
        lines = [f"Style '{style}' in a {climate.replace('_', '-')} climate:",
                 "  - Keep one accent surface per room; more than that reads busy at this scale.",
                 "  - Choose flooring first, then wall colour: floors dominate how a room feels.",
                 "  - In the 3D viewer, change one material at a time so you can judge each change."]
        if climate == "hot_humid":
            lines += ["  - Avoid wall-to-wall carpet and untreated timber; both fail in sustained "
                      "humidity.",
                      "  - Light, breathable curtains and cross ventilation beat heavy drapes."]
        if climate == "hot_dry":
            lines += ["  - Pale external walls and shaded west openings cut afternoon heat gain.",
                      "  - Stone or tile floors stay cooler than wood."]
        if climate == "cold":
            lines += ["  - Warm colours and textiles matter more here than in mild climates.",
                      "  - Roof insulation is the single biggest comfort upgrade."]
        lines.append("")
        lines.append("These are aesthetic and comfort suggestions, not code requirements.")
        return "\n".join(lines)

    def _budget_commentary(self, facts: Dict[str, Any], question: str = "") -> str:
        budget = facts.get("budget") or {}
        if not budget:
            return ("No estimate yet. Open the Budget Planner and generate one -- it needs the "
                    "built-up area from the current design.")
        lines = [
            f"Indicative range: {budget.get('currency', 'INR')} "
            f"{budget.get('total_low', 0):,.0f} to {budget.get('total_high', 0):,.0f} "
            f"({budget.get('per_sqft_low', 0):,.0f}-{budget.get('per_sqft_high', 0):,.0f} per sq ft) "
            f"for {_fmt_area(facts.get('metrics', {}).get('total_built_up_m2', 0))} of built-up area.",
            "The spread between the low and high figure is mostly finish quality and local labour "
            "rates, not the geometry.",
            "Biggest levers, in order: built-up area, finish level, then kitchen and joinery scope.",
            "Enter your own contractor rates in the budget panel to replace the indicative bands -- "
            "the estimate then reflects your market instead of a generic one.",
            "",
            "This is a planning estimate, not a quotation.",
        ]
        return "\n".join(lines)

    def _compare(self, facts: Dict[str, Any], question: str = "") -> str:
        alternatives = facts.get("alternatives") or []
        if not alternatives:
            return "There is nothing to compare yet -- generate at least two concepts first."
        lines = ["Comparison (cheapest first):"]
        for alt in alternatives:
            lines.append(f"  - {alt.get('label')}: {_fmt_area(alt.get('built_up_m2', 0))}, "
                         f"{alt.get('bedrooms', 0)} bed / {alt.get('bathrooms', 0)} bath, "
                         f"{alt.get('circulation_pct', 0):.0f}% circulation, "
                         f"{alt.get('usable_area_m2', 0):,.1f} m\u00b2 usable, "
                         f"cost {alt.get('total_low', 0):,.0f}-{alt.get('total_high', 0):,.0f}")
            for note in alt.get("notes", []):
                lines.append(f"      {note}")
        return "\n".join(lines)

    def _assumptions(self, facts: Dict[str, Any], question: str = "") -> str:
        items = facts.get("assumptions") or []
        if not items:
            return "No assumptions recorded for this design."
        lines = ["Assumptions and unknowns for this design:"]
        for item in items:
            if isinstance(item, dict):
                lines.append(f"  - [{item.get('severity', 'info')}] {item.get('topic', '')}: "
                             f"{item.get('statement', '')}")
            else:
                lines.append(f"  - {item}")
        lines.append("")
        lines.append(DISCLAIMER)
        return "\n".join(lines)

    # -- question answering -------------------------------------------------
    def _answer(self, facts: Dict[str, Any], question: str) -> str:
        q = (question or "").lower()
        rooms = facts.get("rooms", [])
        metrics = facts.get("metrics", {})
        plot = facts.get("plot", {})
        req = facts.get("requirements", {})

        if not q.strip():
            return self._explain_design(facts, question)

        if any(w in q for w in ("how big", "total area", "built up", "built-up", "area of the house",
                               "square feet", "sqft", "square metre")):
            return (f"Built-up area is {_fmt_area(metrics.get('total_built_up_m2', 0))} across "
                    f"{req.get('floors', 1)} floor(s). The plot is {_fmt_area(plot.get('area_m2', 0))} "
                    f"and {metrics.get('open_space_pct', 0):.0f}% of it is left open.")

        if "area" in q and any(name_in_q(q, r.get("name", "")) for r in rooms):
            room = next(r for r in rooms if name_in_q(q, r.get("name", "")))
            return (f"{room.get('name')} is {_fmt_area(room.get('area_m2', 0))} "
                    f"({room.get('w', 0):.2f} m x {room.get('d', 0):.2f} m) on "
                    f"{floor_name_of(facts, room)}.")

        if any(w in q for w in ("how many rooms", "room list", "which rooms", "list the rooms")):
            grouped = _rooms_by_floor(facts)
            lines = [f"There are {len(rooms)} rooms in the model:"]
            for floor_name, items in grouped.items():
                if items:
                    lines.append(f"  {floor_name}: " + ", ".join(r.get("name", "?") for r in items))
            return "\n".join(lines)

        if any(w in q for w in ("cost", "budget", "price", "expensive", "afford")):
            return self._budget_commentary(facts, question)

        if any(w in q for w in ("setback", "boundary line", "how much land", "coverage", "far")):
            sb = plot.get("setbacks", {})
            return (f"Setbacks assumed: N {sb.get('north', 0):.2f} m, S {sb.get('south', 0):.2f} m, "
                    f"E {sb.get('east', 0):.2f} m, W {sb.get('west', 0):.2f} m. Ground coverage is "
                    f"{metrics.get('ground_coverage_pct', 0):.0f}% of the plot. These are "
                    f"assumptions, not verified byelaw values -- confirm them with your local "
                    f"authority before designing to the limit.")

        if any(w in q for w in ("stair", "staircase", "stairs")):
            stairs = [r for r in rooms if r.get("type") == "stairs"]
            if not stairs:
                return "This design is single-storey, so no stair is needed."
            return (f"There are {len(stairs)} stair cores, all stacked vertically so the slab "
                    f"openings line up. Stair geometry (risers, treads, headroom) must be checked "
                    f"against your local building code and confirmed by a structural engineer.")

        if any(w in q for w in ("vastu", "vaastu")):
            if not req.get("vastu"):
                return ("Vastu guidance is switched off for this project. Turn it on in the setup "
                        "wizard and regenerate: the planner then treats kitchen (south-east), "
                        "master bedroom (south-west), prayer space (north-east) and toilets "
                        "(north-west) as soft placement preferences. It is cultural guidance, not "
                        "an engineering or legal requirement.")
            return ("Vastu preferences were applied as soft targets. Where the plot shape or the "
                    "room programme made an ideal zone impossible, the planner picked the closest "
                    "workable position. Engineering and byelaw requirements always take priority "
                    "over Vastu.")

        if any(w in q for w in ("ventilat", "air flow", "cross ventilation", "breeze")):
            return ("Cross ventilation needs openings on two different walls of a room. The "
                    "validation panel lists every habitable room that has no external opening -- "
                    "those are the ones to fix first.")

        if any(w in q for w in ("park", "car", "garage")):
            parking = [r for r in rooms if r.get("type") == "parking"]
            if not parking:
                return ("No parking space is in the model. Increase the front setback, plan a "
                        "stilt floor, or park in the open setback if your byelaws allow it.")
            total = sum(r.get("area_m2", 0) for r in parking)
            return (f"Parking: {', '.join(r.get('name', '') for r in parking)} totalling "
                    f"{_fmt_area(total)} ({'covered' if any(not r.get('outdoor') for r in parking) else 'open'}).")

        if any(w in q for w in ("expand", "extension", "future", "add a floor", "later")):
            return ("Expansion is a structural question, not a layout one: adding a floor later "
                    "needs foundations and columns designed for it from the start. Tell a "
                    "structural engineer your expansion plan before you build. The layout engine "
                    "can show you what an extra floor would look like now, but that does not make "
                    "the existing structure ready for it.")

        if any(w in q for w in ("safe", "structural", "load bearing", "column", "beam",
                               "foundation", "earthquake", "seismic")):
            return ("This tool does not design structure. It places rooms and shows walls as "
                    "load-bearing masonry for visualisation only. Column positions, beam sizes, "
                    "foundation depth, soil bearing capacity and seismic detailing must all come "
                    "from a licensed structural engineer. Never build from these drawings.")

        if any(w in q for w in ("legal", "approval", "permission", "code", "bylaw", "byelaw",
                               "compliance", "noc")):
            return ("I cannot verify legal compliance. Setbacks, ground coverage, FAR and height "
                    "limits come from your municipal byelaws and depend on road width and plot "
                    "size. The validation panel flags where this design exceeds typical limits, "
                    "but the authoritative answer must come from your local planning authority.")

        if any(w in q for w in ("orientation", "sun", "north", "east facing", "west facing",
                               "daylight", "light")):
            return (f"The road is on the {plot.get('road_side', 'S')} side. In the northern "
                    f"hemisphere, south and east openings bring useful winter sun and morning "
                    f"light; west openings bring the harshest afternoon heat. The validation panel "
                    f"lists rooms with little or no glazing.")

        return ("I can answer questions about this design's areas, rooms, stairs, setbacks, "
                "parking, orientation, budget, Vastu handling and what needs a professional. "
                "For anything else, switch to a hosted AI provider in Settings -- the built-in "
                "assistant only knows the facts in your project.\n\n" + self._summary(facts))


def name_in_q(question: str, name: str) -> bool:
    key = name.lower().replace(" room", "").strip()
    return bool(key) and key in question


def floor_name_of(facts: Dict[str, Any], room: Dict[str, Any]) -> str:
    for floor in facts.get("floors", []):
        if floor.get("id") == room.get("floor"):
            return floor.get("name", "a floor")
    return "a floor"


# ---------------------------------------------------------------------------
# vision
# ---------------------------------------------------------------------------

class LocalVision(VisionAdapter):
    name = PROVIDER_KEY

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key=PROVIDER_KEY, label="Built-in image inspection",
            capabilities=["measure", "palette", "plan_detection", "wall_tracing"],
            requires_key=False, configured=True,
            model="Pillow-based image analysis (on-device)",
            setup=SETUP_INSTRUCTIONS[PROVIDER_KEY], privacy=PRIVACY_NOTES[PROVIDER_KEY],
            offline=True)

    async def available(self) -> bool:
        return True

    async def analyse(self, *, path: str, room_type: str, goals: List[str], note: str = "",
                      mime_type: str = "image/jpeg") -> Dict[str, Any]:
        try:
            from PIL import Image, ImageFilter, ImageStat
        except ImportError as exc:  # pragma: no cover
            raise AIError(f"Image analysis needs Pillow, which is not installed ({exc}).",
                          provider=PROVIDER_KEY)
        if not os.path.exists(path):
            raise AIError("The uploaded file is no longer on the server.", provider=PROVIDER_KEY)

        with Image.open(path) as img:
            img.load()
            width, height = img.size
            rgb = img.convert("RGB")
            small = rgb.resize((160, int(max(1, 160 * height / width))) if width else (160, 120))
            stat = ImageStat.Stat(small)
            means = stat.mean
            stddev = stat.stddev
            grey = small.convert("L")
            edges = grey.filter(ImageFilter.FIND_EDGES)
            edge_stat = ImageStat.Stat(edges)
            edge_bins = getattr(edge_stat, "h", []) or []
            edge_density = sum(edge_bins[128:256]) / max(1, grey.size[0] * grey.size[1])
            palette = _dominant_palette(small, 5)
            bright = sum(means) / 3.0
            contrast = sum(stddev) / 3.0
            plan_like = _looks_like_plan(grey, bright, contrast, edge_density)
            walls = _trace_orthogonal_runs(grey) if plan_like else []

        findings: List[Dict[str, Any]] = []
        findings.append({"kind": "measurement", "label": "Image size",
                         "value": f"{width} x {height} px",
                         "detail": "Pixel dimensions only. A photograph carries no reliable scale."})
        findings.append({"kind": "lighting", "label": "Brightness",
                         "value": f"{bright:.0f}/255",
                         "detail": _brightness_comment(bright)})
        findings.append({"kind": "lighting", "label": "Contrast", "value": f"{contrast:.0f}/255",
                         "detail": "Low contrast usually means flat, diffuse light; high contrast "
                                   "means strong directional light or harsh shadows."})
        findings.append({"kind": "flooring", "label": "Dominant colours",
                         "value": ", ".join(f"{c} ({p:.0%})" for c, p in palette),
                         "detail": "Rounded to the nearest named material tone."})
        findings.append({"kind": "layout", "label": "Image type",
                         "value": "Drawn floor plan / sketch" if plan_like else "Photograph or render",
                         "detail": ("Detected from brightness, contrast and straight-edge density."
                                    if plan_like else
                                    "No dominant orthogonal line structure was found.")})
        if room_type and room_type != "other":
            findings.append({"kind": "layout", "label": "Space you tagged",
                             "value": room_type.replace("_", " "),
                             "detail": "Used to focus the redesign suggestions."})

        if plan_like and walls:
            findings.append({"kind": "walls", "label": "Line structure",
                             "value": f"{len(walls)} orthogonal run(s) traced",
                             "detail": "Positions are relative to the image, not real world. Give "
                                       "one known dimension and the app can scale them."})
        findings.append({"kind": "honesty", "label": "What cannot be recovered",
                         "value": "Exact dimensions, wall thicknesses, heights",
                         "detail": "A photo or scan has no scale, no camera calibration and no "
                                   "depth. Anything measured from it is approximate and must be "
                                   "verified on site with a tape measure or laser disto."})

        suggestions = _suggestions_for(room_type, goals, bright, contrast, palette, plan_like)
        return {
            "provider": PROVIDER_KEY,
            "model": "builtin-image-inspection",
            "width": width, "height": height,
            "plan_like": plan_like,
            "brightness": round(bright, 1), "contrast": round(contrast, 1),
            "edge_density": round(edge_density, 4),
            "palette": [{"hex": c, "share": round(p, 3)} for c, p in palette],
            "relative_walls": walls[:40],
            "findings": findings,
            "suggestions": suggestions,
            "structural_note": ("Structural elements visible in this image (load-bearing walls, "
                                "columns, beams, lintels) are preserved by default. The app only "
                                "changes them if you explicitly ask for a structural alteration, "
                                "and every such change is flagged for professional review."),
            "cosmetic_vs_structural": {
                "cosmetic": ["paint and wall colour", "flooring finish", "curtains and soft "
                             "furnishings", "lighting fixtures", "furniture layout",
                             "cabinet fronts and handles"],
                "structural": ["removing or moving a wall", "widening an opening",
                               "changing a column or beam", "altering the roof structure",
                               "excavation or foundation work"],
            },
            "disclaimer": ("Analysis produced locally from pixel statistics. It is a description, "
                           "not a survey. Confirm every dimension on site."),
        }


def _brightness_comment(bright: float) -> str:
    if bright < 70:
        return "Dark image: this space is under-lit. More or larger openings, lighter surfaces and " \
               "layered artificial lighting would help."
    if bright < 130:
        return "Moderate light. Typical of a room with one window and mid-tone finishes."
    if bright < 200:
        return "Bright, evenly lit space."
    return "Very bright or over-exposed; detail may be lost in highlights."


def _dominant_palette(img, count: int = 5) -> List[Tuple[str, float]]:
    try:
        quantised = img.quantize(colors=count)
    except Exception:
        quantised = img
    palette_img = quantised.convert("RGB")
    pixels = list(palette_img.getdata())
    total = max(1, len(pixels))
    buckets: Dict[Tuple[int, int, int], int] = {}
    for px in pixels:
        key = (px[0] // 24 * 24, px[1] // 24 * 24, px[2] // 24 * 24)
        buckets[key] = buckets.get(key, 0) + 1
    named = [("#{:02x}{:02x}{:02x}".format(*k), v / total, _colour_name(k)) for k, v in
             sorted(buckets.items(), key=lambda kv: -kv[1])]
    return [(f"{name} {hexcode}", share) for hexcode, share, name in named[:count]]


def _colour_name(rgb: Tuple[int, int, int]) -> str:
    r, g, b = rgb
    mx, mn = max(rgb), min(rgb)
    light = (mx + mn) / 2 / 255
    sat = 0 if mx == mn else (mx - mn) / (255 * (2 - mx / 255 - mn / 255) if (mx + mn) > 255
                                          else (mx + mn) / 255 or 1)
    if sat < 0.12:
        if light > 0.85:
            return "white/off-white"
        if light > 0.6:
            return "light grey"
        if light > 0.35:
            return "mid grey"
        if light > 0.15:
            return "dark grey/charcoal"
        return "near black"
    if r > g > b and sat < 0.45 and light > 0.4:
        return "beige/cream"
    if r > g and b < g and light < 0.55:
        return "wood brown"
    if r > 120 and g < 90 and b < 90:
        return "terracotta/red"
    if g > r and g > b:
        return "green"
    if b > r and b > g:
        return "blue"
    if r > 150 and g > 120 and b < 100:
        return "warm yellow/ochre"
    return "mixed tone"


def _looks_like_plan(grey, bright: float, contrast: float, edge_density: float) -> bool:
    hist = grey.histogram()
    total = max(1, sum(hist))
    light_share = sum(hist[190:]) / total
    dark_share = sum(hist[:70]) / total
    return light_share > 0.45 and dark_share > 0.03 and contrast > 35


def _trace_orthogonal_runs(grey, min_len: int = 24, threshold: int = 90) -> List[Dict[str, Any]]:
    """Trace long dark horizontal/vertical runs and return them in image-relative units.

    Positions are fractions of the image width/height (0..1). No real-world scale is
    claimed -- that is exactly what a photograph or scan cannot provide.
    """
    width, height = grey.size
    px = grey.load()
    runs: List[Dict[str, Any]] = []

    def dark(x: int, y: int) -> bool:
        return px[x, y] < threshold

    for y in range(0, height, 2):
        x = 0
        while x < width:
            if dark(x, y):
                start = x
                while x < width and dark(x, y):
                    x += 1
                if x - start >= min_len:
                    runs.append({"orientation": "horizontal",
                                 "x0": round(start / width, 4), "x1": round(x / width, 4),
                                 "y": round(y / height, 4),
                                 "length_px": x - start})
            else:
                x += 1
    for x in range(0, width, 2):
        y = 0
        while y < height:
            if dark(x, y):
                start = y
                while y < height and dark(x, y):
                    y += 1
                if y - start >= min_len:
                    runs.append({"orientation": "vertical",
                                 "y0": round(start / height, 4), "y1": round(y / height, 4),
                                 "x": round(x / width, 4),
                                 "length_px": y - start})
            else:
                y += 1
    runs.sort(key=lambda r: -r.get("length_px", 0))
    return runs


def _suggestions_for(room_type: str, goals: List[str], bright: float, contrast: float,
                     palette: List[Tuple[str, float]], plan_like: bool) -> List[str]:
    out: List[str] = []
    if bright < 90:
        out.append("The space reads dark. Lighter wall and floor tones plus an extra light layer "
                   "would lift it without any structural change.")
    if bright > 205:
        out.append("The space is very bright or over-exposed. Shading, curtains or a screen would "
                   "reduce glare -- in hot climates this also cuts heat gain.")
    if contrast < 25:
        out.append("Everything is a similar tone, so the room lacks definition. One accent wall or "
                   "a contrasting floor boundary would help.")
    dominant = palette[0][1] if palette else 0
    if dominant > 0.5:
        out.append(f"The image is dominated by {palette[0][0]}. A secondary material (wood, stone "
                   f"or a textile) would add warmth.")
    for goal in goals:
        g = goal.lower()
        if "storage" in g:
            out.append("For more storage: full-height wardrobes, under-stair storage and a loft "
                       "over the kitchen platform are the three cheapest gains.")
        if "light" in g or "bright" in g:
            out.append("For more daylight: enlarge windows on the north/east side, add a skylight "
                       "over a corridor, or use a glazed internal partition.")
        if "ventilat" in g:
            out.append("For ventilation: you need openings on two different walls. A ventilator "
                       "above a door helps when that is not possible.")
        if "modern" in g or "update" in g or "renovat" in g:
            out.append("For a modern update without structural work: change flooring, paint, "
                       "lighting and hardware first -- they carry most of the visual change.")
        if "space" in g or "bigger" in g or "spacious" in g:
            out.append("To make it feel larger: fewer, bigger pieces of furniture, a continuous "
                       "floor finish, and curtains hung close to the ceiling.")
        if "cost" in g or "budget" in g or "cheap" in g:
            out.append("On a budget: keep the layout, replace finishes. Paint and lighting give "
                       "the most change per rupee.")
    if plan_like:
        out.append("This looks like a drawn plan. Trace the walls in the 2D editor, give one known "
                   "dimension, and the app will rebuild it as a live model you can edit.")
    else:
        out.append("This is a photograph or render. Exact dimensions cannot be recovered from it -- "
                   "measure on site if you need real numbers.")
    if not out:
        out.append("No specific issues detected from the image statistics. Tell the assistant what "
                   "you want to change and it will propose an edit you can preview first.")
    return out


# ---------------------------------------------------------------------------
# image generation (not available locally)
# ---------------------------------------------------------------------------

class LocalImage(ImageAdapter):
    name = PROVIDER_KEY

    def __init__(self) -> None:
        self.info = ProviderInfo(
            key="none", label="No image generation provider",
            capabilities=[], requires_key=True, configured=False,
            model="", setup=SETUP_INSTRUCTIONS["none"], privacy=PRIVACY_NOTES["none"])

    async def available(self) -> bool:
        return False

    async def generate(self, *, prompt: str, style: str,
                       reference_paths: Optional[List[str]] = None,
                       size: str = "1024x1024") -> Dict[str, Any]:
        raise AIError(
            "Photorealistic image generation needs a hosted provider (OpenAI, Google Gemini or "
            "Stability). Add a key in Settings -> AI providers. Meanwhile the 3D viewer renders "
            "your actual model geometry interactively -- that is a real picture of your design, "
            "not a generated illustration.",
            provider="none", setup_required=True)
