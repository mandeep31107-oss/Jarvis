"""Interior & exterior design assistant data.

Style palettes, material option lists and the helpers that apply them. Applying a
style or a material changes *only* appearance data (colours, material keys,
furniture finishes) -- never geometry -- so the 3D scene updates instantly without
regenerating the plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import MaterialSet, ProjectData, Room

# ---------------------------------------------------------------------------
# option catalogues (surfaced verbatim in the design panels)
# ---------------------------------------------------------------------------

FLOORING_OPTIONS: List[Dict[str, Any]] = [
    {"key": "tile", "label": "Vitrified / ceramic tile", "color": "#cbb89a", "cost": "economical",
     "note": "Hard wearing, easy to clean, cool underfoot. Widest choice of sizes and prints."},
    {"key": "marble", "label": "Marble", "color": "#efeae1", "cost": "premium",
     "note": "Polished, seamless look. Needs sealing and skilled laying; can be slippery when wet."},
    {"key": "granite", "label": "Granite", "color": "#b9b2a6", "cost": "premium",
     "note": "Very hard wearing, good for entries and heavy traffic areas."},
    {"key": "wood", "label": "Wood / engineered wood", "color": "#8a5a34", "cost": "premium",
     "note": "Warm underfoot. Avoid in wet areas; engineered boards suit Indian humidity better "
             "than solid timber."},
    {"key": "kota_stone", "label": "Kota stone", "color": "#9aa08a", "cost": "economical",
     "note": "Traditional Indian stone, cool and durable, good for verandahs and parking."},
    {"key": "concrete", "label": "Polished concrete / IPS", "color": "#9c9c98", "cost": "economical",
     "note": "Seamless industrial look; needs a good sealer and control joints."},
]

WALL_FINISH_OPTIONS: List[Dict[str, Any]] = [
    {"key": "paint_matte", "label": "Matte emulsion paint", "cost": "economical",
     "note": "Most common interior finish; easy to touch up."},
    {"key": "paint_satin", "label": "Satin / semi-gloss paint", "cost": "standard",
     "note": "Washable, good for corridors, kitchens and children's rooms."},
    {"key": "texture", "label": "Textured finish", "cost": "standard",
     "note": "Hides undulations in plaster; use on one feature wall rather than everywhere."},
    {"key": "wallpaper", "label": "Wallpaper", "cost": "standard",
     "note": "Not recommended in humid rooms or on external walls that may damp."},
    {"key": "tile_clad", "label": "Tile / stone cladding", "cost": "premium",
     "note": "Durable in wet and high-traffic zones."},
    {"key": "wood_panel", "label": "Wood panelling", "cost": "premium",
     "note": "Adds warmth and acoustic softness; keep away from wet areas."},
]

CEILING_OPTIONS: List[Dict[str, Any]] = [
    {"key": "plain", "label": "Plain plastered ceiling", "cost": "economical",
     "note": "Simplest and cheapest; easiest to maintain."},
    {"key": "cove", "label": "Cove / perimeter false ceiling", "cost": "standard",
     "note": "Hides indirect lighting and wiring. Reduces clear height by 100-150 mm."},
    {"key": "grid", "label": "Grid / modular ceiling", "cost": "standard",
     "note": "Easy access for services; reads as office-like in a home."},
    {"key": "beam", "label": "Exposed beams", "cost": "premium",
     "note": "Only honest if the structure actually has exposed beams -- otherwise it is a "
             "decorative addition that needs fixing to the slab."},
]

ROOF_OPTIONS: List[Dict[str, Any]] = [
    {"key": "flat", "label": "Flat RCC terrace roof", "cost": "standard",
     "note": "Usable terrace. Needs slope to drains and a proper waterproofing layer."},
    {"key": "gable", "label": "Gable (pitched) roof", "cost": "premium",
     "note": "Sheds monsoon rain quickly; allows an attic and better heat insulation."},
    {"key": "hip", "label": "Hip roof", "cost": "premium",
     "note": "All sides slope; more stable in wind, more complex to build."},
    {"key": "shed", "label": "Shed / mono-pitch", "cost": "standard",
     "note": "Simple single slope, good for extensions and clerestory light."},
    {"key": "terrace", "label": "Accessible terrace with parapet", "cost": "standard",
     "note": "Parapet height 1.05-1.2 m is the usual safety minimum -- verify locally."},
]

FACADE_OPTIONS: List[Dict[str, Any]] = [
    {"key": "plaster_paint", "label": "Plaster + paint", "cost": "economical",
     "note": "Most common Indian facade. Repaint every 4-7 years depending on exposure."},
    {"key": "brick_exposed", "label": "Exposed brick", "cost": "standard",
     "note": "Needs good quality brick and a water-repellent seal in heavy monsoon areas."},
    {"key": "stone_clad", "label": "Stone cladding", "cost": "premium",
     "note": "Durable and cool; adds significant dead load -- a structural check is required."},
    {"key": "concrete_exposed", "label": "Exposed concrete", "cost": "premium",
     "note": "Needs disciplined formwork and curing; stains if detailing is poor."},
    {"key": "jali_screen", "label": "Jali / perforated screen", "cost": "standard",
     "note": "Traditional Indian sun-breaker; cuts glare and gives privacy while letting air "
             "through."},
    {"key": "composite_panel", "label": "Composite / ACP panels", "cost": "premium",
     "note": "Fast to install. Check the fire rating of the core material before specifying."},
    {"key": "tile_clad", "label": "Facade tile", "cost": "standard",
     "note": "Low maintenance; ensure mechanical fixing or good adhesive on high walls."},
]

FURNITURE_STYLES: List[Dict[str, Any]] = [
    {"key": "modern", "label": "Modern", "upholstery": "#5c6b7a", "wood": "#7d5a3c",
     "note": "Clean lines, low profiles, neutral upholstery with one accent colour."},
    {"key": "contemporary", "label": "Contemporary", "upholstery": "#7a7f87", "wood": "#6b4a30",
     "note": "Current-market look: rounded edges, mixed materials, muted tones."},
    {"key": "minimalist", "label": "Minimalist", "upholstery": "#d8d5cf", "wood": "#a98a63",
     "note": "Bare surfaces, hidden storage, very few pieces per room."},
    {"key": "traditional_indian", "label": "Traditional Indian", "upholstery": "#8c3b2e",
     "wood": "#5a3320", "note": "Carved timber, jaali screens, rich fabrics, low seating."},
    {"key": "scandinavian", "label": "Scandinavian", "upholstery": "#cfd8dc", "wood": "#c9a27a",
     "note": "Light wood, pale textiles, high legs, maximum daylight."},
    {"key": "industrial", "label": "Industrial", "upholstery": "#6d5c4a", "wood": "#4a4a4a",
     "note": "Black metal frames, raw timber, exposed fixing."},
    {"key": "luxury", "label": "Luxury", "upholstery": "#3f4a5a", "wood": "#3b2a1c",
     "note": "Deep tones, marble and brass accents, generous proportions."},
    {"key": "rustic", "label": "Rustic", "upholstery": "#8a7a5f", "wood": "#6b4a2f",
     "note": "Rough-sawn wood, natural fibres, warm earthy colours."},
    {"key": "mediterranean", "label": "Mediterranean", "upholstery": "#e0d5c1", "wood": "#8a6a4f",
     "note": "Whitewash, terracotta, arched openings, blue accents."},
    {"key": "japanese", "label": "Japanese", "upholstery": "#d6d2c4", "wood": "#8b6a4b",
     "note": "Low furniture, sliding screens, tatami-like mats, restrained palette."},
    {"key": "budget_modern", "label": "Budget-friendly modern", "upholstery": "#7f8a94",
     "wood": "#8a6a4f", "note": "Ready-made flat-pack furniture in durable laminates."},
]

LIGHTING_OPTIONS: List[Dict[str, Any]] = [
    {"key": "warm", "label": "Warm white (2700-3000 K)", "color": "#ffd9a0",
     "note": "Cosy; suits living rooms and bedrooms."},
    {"key": "neutral", "label": "Neutral white (4000 K)", "color": "#fff4e0",
     "note": "Good general-purpose choice for kitchens and bathrooms."},
    {"key": "cool", "label": "Cool daylight (5000-6500 K)", "color": "#eaf4ff",
     "note": "Common in India; best for study and task areas, can feel clinical in bedrooms."},
    {"key": "accent", "label": "Accent / cove lighting", "color": "#c9d8ff",
     "note": "Decorative layer; needs a false ceiling or a cove detail."},
]

CURTAIN_OPTIONS: List[Dict[str, Any]] = [
    {"key": "sheer", "label": "Sheer / voile", "color": "#f2ede4",
     "note": "Softens glare while keeping the view."},
    {"key": "cotton", "label": "Cotton / linen blend", "color": "#d9d2c5",
     "note": "Everyday choice, breathable in humid climates."},
    {"key": "blackout", "label": "Blackout", "color": "#5b5f66",
     "note": "Blocks heat and light; good for bedrooms facing west."},
    {"key": "blinds", "label": "Roller / venetian blinds", "color": "#b9b3a6",
     "note": "Compact, easy to clean, good for kitchens and studies."},
]


# ---------------------------------------------------------------------------
# styles
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StylePalette:
    key: str
    label: str
    description: str
    materials: Dict[str, Any]
    furniture_style: str
    cost_impact: str
    tips: Tuple[str, ...] = ()
    best_for: Tuple[str, ...] = ()


STYLES: Dict[str, StylePalette] = {
    p.key: p for p in (
        StylePalette("modern", "Modern", "Straight lines, large glazed openings, neutral palette "
                     "with one strong accent.",
                     {"exterior_wall": "#e6e3dd", "exterior_accent": "#3a4a5a",
                      "interior_wall": "#f6f4ef", "feature_wall": "#4a6fa5",
                      "floor_material": "tile", "floor_color": "#cfc7b8",
                      "kitchen_cabinet": "#3f4a55", "kitchen_counter": "#2f3437",
                      "ceiling_style": "cove", "roof_type": "flat", "roof_color": "#6f6a63",
                      "door_material": "panel", "window_frame": "#33383d",
                      "facade_style": "plaster_paint", "curtain_color": "#dcd6ca"},
                     "modern", "standard",
                     ("Keep the accent colour to one wall per room or the space reads busy.",
                      "Large glazed areas face a real heat problem in most of India -- pair them "
                      "with shades or a jali screen."),
                     ("Urban plots", "Small families", "Rental-friendly finishes")),
        StylePalette("contemporary", "Contemporary", "Current market look: soft curves, mixed "
                     "textures, muted warm greys.",
                     {"exterior_wall": "#ded7cb", "exterior_accent": "#7a6a58",
                      "interior_wall": "#f2efe8", "feature_wall": "#8a7f6d",
                      "floor_material": "tile", "floor_color": "#d8cfc0",
                      "kitchen_cabinet": "#6b5a48", "kitchen_counter": "#e2ddd4",
                      "ceiling_style": "cove", "roof_type": "flat", "roof_color": "#7c6a5c",
                      "door_material": "wood", "window_frame": "#4a4f55",
                      "facade_style": "tile_clad", "curtain_color": "#e3dcd0"},
                     "contemporary", "standard",
                     ("Mix two or three textures rather than five -- the style depends on restraint.",),
                     ("Resale value", "Family homes")),
        StylePalette("minimalist", "Minimalist", "Bare surfaces, hidden storage, very few objects "
                     "on show.",
                     {"exterior_wall": "#f0eeea", "exterior_accent": "#b9b3a8",
                      "interior_wall": "#faf8f5", "feature_wall": "#dcd8d0",
                      "floor_material": "concrete", "floor_color": "#cfcbc4",
                      "kitchen_cabinet": "#e8e5df", "kitchen_counter": "#3b3f42",
                      "ceiling_style": "plain", "roof_type": "flat", "roof_color": "#8d8880",
                      "door_material": "panel", "window_frame": "#2f3338",
                      "facade_style": "plaster_paint", "curtain_color": "#eeeae3"},
                     "minimalist", "standard",
                     ("Minimalism needs storage to survive real life -- plan wardrobes and a store "
                      "room generously.",
                      "Dust shows on pale floors; a matte sealer helps."),
                     ("Small plots", "Couples", "Hot climates")),
        StylePalette("traditional_indian", "Traditional Indian", "Carved timber, jaali screens, "
                     "courtyard planning, warm earth colours.",
                     {"exterior_wall": "#e3d3b8", "exterior_accent": "#8c3b2e",
                      "interior_wall": "#f6ecda", "feature_wall": "#8c3b2e",
                      "floor_material": "kota_stone", "floor_color": "#9aa08a",
                      "kitchen_cabinet": "#5a3320", "kitchen_counter": "#3a342c",
                      "ceiling_style": "beam", "roof_type": "gable", "roof_color": "#8b4a3a",
                      "door_material": "wood", "window_frame": "#5a3320",
                      "facade_style": "jali_screen", "curtain_color": "#d8b98c"},
                     "traditional_indian", "premium",
                     ("Exposed timber needs termite treatment and periodic oiling in humid areas.",
                      "A jaali screen on the west and south facades cuts glare without blocking air.",
                      "Sloped roofs must be detailed for driving monsoon rain, not just looks."),
                     ("Joint families", "Hot-dry and composite climates", "Cultural preferences")),
        StylePalette("scandinavian", "Scandinavian", "Pale wood, white walls, maximum daylight and "
                     "simple textiles.",
                     {"exterior_wall": "#f4f2ee", "exterior_accent": "#c9a27a",
                      "interior_wall": "#fbfaf7", "feature_wall": "#cfd8dc",
                      "floor_material": "wood", "floor_color": "#dcc3a4",
                      "kitchen_cabinet": "#f0ece4", "kitchen_counter": "#c9a27a",
                      "ceiling_style": "plain", "roof_type": "gable", "roof_color": "#8a8f94",
                      "door_material": "wood", "window_frame": "#dcd6cc",
                      "facade_style": "plaster_paint", "curtain_color": "#f0ebe2"},
                     "scandinavian", "standard",
                     ("Light wood floors and pale walls are unforgiving of dust and stains.",
                      "The style assumes long dark winters -- in bright Indian light, add shading "
                      "so rooms do not glare."),
                     ("Cold and temperate climates", "Compact homes")),
        StylePalette("industrial", "Industrial", "Black metal, raw concrete, exposed services.",
                     {"exterior_wall": "#9c9c98", "exterior_accent": "#2f3338",
                      "interior_wall": "#e3e1dc", "feature_wall": "#6d6a64",
                      "floor_material": "concrete", "floor_color": "#94918b",
                      "kitchen_cabinet": "#3a3f45", "kitchen_counter": "#22262a",
                      "ceiling_style": "beam", "roof_type": "shed", "roof_color": "#4a4f55",
                      "door_material": "metal", "window_frame": "#22262a",
                      "facade_style": "concrete_exposed", "curtain_color": "#b9b3a6"},
                     "industrial", "standard",
                     ("Exposed concrete and metal need good detailing or they stain and rust.",
                      "Hard surfaces make rooms noisy -- add rugs and soft furnishings."),
                     ("Workshops / live-work", "Urban lofts", "Young households")),
        StylePalette("luxury", "Luxury", "Deep tones, marble, brass and generous proportions.",
                     {"exterior_wall": "#e8e2d6", "exterior_accent": "#2f3a45",
                      "interior_wall": "#f4f0e8", "feature_wall": "#3f4a5a",
                      "floor_material": "marble", "floor_color": "#efeae1",
                      "kitchen_cabinet": "#2f3a45", "kitchen_counter": "#e8e3d9",
                      "ceiling_style": "cove", "roof_type": "flat", "roof_color": "#5d5750",
                      "door_material": "panel", "window_frame": "#2a2e33",
                      "facade_style": "stone_clad", "curtain_color": "#d8cdb8"},
                     "luxury", "premium",
                     ("Stone cladding adds real dead load -- a structural engineer must confirm the "
                      "wall and foundation can carry it.",
                      "Polished marble is slippery when wet; use a honed finish in bathrooms."),
                     ("Larger plots", "High budgets")),
        StylePalette("rustic", "Rustic", "Rough timber, natural stone, warm earthy colours.",
                     {"exterior_wall": "#d9c8ad", "exterior_accent": "#6b4a2f",
                      "interior_wall": "#f3ead9", "feature_wall": "#8a7a5f",
                      "floor_material": "kota_stone", "floor_color": "#a79a82",
                      "kitchen_cabinet": "#6b4a2f", "kitchen_counter": "#8a7a5f",
                      "ceiling_style": "beam", "roof_type": "gable", "roof_color": "#7a5a42",
                      "door_material": "wood", "window_frame": "#6b4a2f",
                      "facade_style": "brick_exposed", "curtain_color": "#d6c6a8"},
                     "rustic", "standard",
                     ("Rough textures collect dust; specify washable sealers.",
                      "Exposed brick needs a water-repellent treatment in heavy monsoon regions."),
                     ("Village and semi-urban plots", "Homestays")),
        StylePalette("mediterranean", "Mediterranean", "Whitewashed walls, terracotta, arched "
                     "openings, blue accents.",
                     {"exterior_wall": "#f7f4ec", "exterior_accent": "#c46a4a",
                      "interior_wall": "#fbf8f2", "feature_wall": "#4a7fa5",
                      "floor_material": "tile", "floor_color": "#d9b48f",
                      "kitchen_cabinet": "#f0ece4", "kitchen_counter": "#c46a4a",
                      "ceiling_style": "beam", "roof_type": "hip", "roof_color": "#b4674a",
                      "door_material": "wood", "window_frame": "#f0ece4",
                      "facade_style": "plaster_paint", "curtain_color": "#efe6d6"},
                     "mediterranean", "premium",
                     ("Arched openings are structural shapes, not just decoration -- they need real "
                      "support.",
                      "White facades show dirt quickly near unpaved roads."),
                     ("Coastal areas", "Hot-humid climates")),
        StylePalette("japanese", "Japanese", "Low furniture, sliding screens, natural timber and a "
                     "restrained palette.",
                     {"exterior_wall": "#e9e4d8", "exterior_accent": "#8b6a4b",
                      "interior_wall": "#f7f4ec", "feature_wall": "#a89b82",
                      "floor_material": "wood", "floor_color": "#cbb08c",
                      "kitchen_cabinet": "#d8cfc0", "kitchen_counter": "#5a5148",
                      "ceiling_style": "grid", "roof_type": "hip", "roof_color": "#5d5750",
                      "door_material": "panel", "window_frame": "#8b6a4b",
                      "facade_style": "jali_screen", "curtain_color": "#e6dfd2"},
                     "japanese", "standard",
                     ("Sliding screens need a track detail in the floor or ceiling -- plan it early.",
                      "Low furniture works best with generous floor area; it can crowd a small room."),
                     ("Compact plots", "Quiet streets")),
        StylePalette("budget_modern", "Budget-friendly modern", "Durable ready-made finishes that "
                     "look current without premium materials.",
                     {"exterior_wall": "#e8e5de", "exterior_accent": "#7f8a94",
                      "interior_wall": "#f7f5f1", "feature_wall": "#7f8a94",
                      "floor_material": "tile", "floor_color": "#cfc7b8",
                      "kitchen_cabinet": "#8a6a4f", "kitchen_counter": "#3b3f42",
                      "ceiling_style": "plain", "roof_type": "flat", "roof_color": "#7c7670",
                      "door_material": "panel", "window_frame": "#4a4f55",
                      "facade_style": "plaster_paint", "curtain_color": "#e0dbd2"},
                     "budget_modern", "economical",
                     ("Spend on flooring and plumbing quality, save on decorative ceilings and "
                      "panelling.",
                      "Choose tile sizes that are locally stocked to avoid wastage and delays."),
                     ("First homes", "Tight budgets", "Rental units")),
    )
}

STYLE_ORDER = ["modern", "contemporary", "minimalist", "traditional_indian", "scandinavian",
               "industrial", "luxury", "rustic", "mediterranean", "japanese", "budget_modern"]


def style_catalogue() -> List[Dict[str, Any]]:
    out = []
    for key in STYLE_ORDER:
        p = STYLES[key]
        out.append({"key": p.key, "label": p.label, "description": p.description,
                    "cost_impact": p.cost_impact, "tips": list(p.tips),
                    "best_for": list(p.best_for), "materials": dict(p.materials),
                    "furniture_style": p.furniture_style})
    return out


def option_catalogue() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "flooring": FLOORING_OPTIONS,
        "wall_finish": WALL_FINISH_OPTIONS,
        "ceiling": CEILING_OPTIONS,
        "roof": ROOF_OPTIONS,
        "facade": FACADE_OPTIONS,
        "furniture_styles": FURNITURE_STYLES,
        "lighting": LIGHTING_OPTIONS,
        "curtains": CURTAIN_OPTIONS,
    }


# ---------------------------------------------------------------------------
# applying styles / materials
# ---------------------------------------------------------------------------

MATERIAL_FIELDS = set(MaterialSet.model_fields.keys())


def apply_style(data: ProjectData, style_key: str) -> Tuple[MaterialSet, List[str]]:
    """Return a new MaterialSet for the style plus notes. Geometry is untouched."""
    palette = STYLES.get(style_key)
    if palette is None:
        return data.materials, [f"Unknown style '{style_key}'."]
    materials = data.materials.model_copy(update=dict(palette.materials))
    notes = [f"Applied the {palette.label} palette: {palette.description}"]
    notes += list(palette.tips)
    if palette.cost_impact == "premium":
        notes.append("This style typically sits at the premium end of the cost band -- re-run the "
                     "budget estimate to see the effect.")
    if data.plot.climate == "hot_humid" and style_key in ("scandinavian", "industrial"):
        notes.append("Warning: pale wood and raw metal age badly in hot-humid coastal air unless "
                     "they are properly sealed.")
    if data.plot.climate == "hot_dry" and palette.materials.get("floor_material") == "marble":
        notes.append("Polished marble stays cool in hot-dry weather but shows dust; a honed finish "
                     "is more forgiving.")
    return materials, notes


def apply_material_change(data: ProjectData, field: str, value: Any) -> Tuple[MaterialSet, List[str]]:
    """Change a single material/colour without touching anything else."""
    if field not in MATERIAL_FIELDS:
        return data.materials, [f"'{field}' is not a material property."]
    current = getattr(data.materials, field)
    if isinstance(current, bool) and not isinstance(value, bool):
        value = str(value).lower() in ("1", "true", "yes", "on")
    elif isinstance(current, str) and current.startswith("#") and isinstance(value, str) \
            and not value.startswith("#"):
        return data.materials, [f"'{value}' is not a colour. Use a hex value like #cbb89a."]
    updates = {field: value}
    materials = data.materials.model_copy(update=updates)
    label = field.replace("_", " ")
    return materials, [f"Updated {label} to {value}. Only the appearance changed -- no geometry "
                       f"was regenerated."]


def apply_room_finish(data: ProjectData, room: Room, wall_color: Optional[str] = None,
                      floor_material: Optional[str] = None,
                      floor_color: Optional[str] = None) -> Tuple[Room, List[str]]:
    """Per-room finish override (used by the interior panel's room scope)."""
    notes: List[str] = []
    updates: Dict[str, Any] = {}
    if wall_color:
        updates["wall_color"] = wall_color
    if floor_material:
        if floor_material not in {o["key"] for o in FLOORING_OPTIONS}:
            notes.append(f"'{floor_material}' is not a known flooring type.")
        else:
            updates["floor_material"] = floor_material
    if floor_color:
        updates["floor_color"] = floor_color
    if updates:
        room = room.model_copy(update=updates)
        notes.append(f"{room.name}: " + ", ".join(f"{k.replace('_', ' ')} = {v}"
                                                  for k, v in updates.items()))
    return room, notes


def apply_furniture_style(data: ProjectData, style_key: str) -> Tuple[ProjectData, List[str]]:
    """Recolour / re-material the furniture only. No geometry is rebuilt."""
    style = next((s for s in FURNITURE_STYLES if s["key"] == style_key), None)
    if style is None:
        return data, [f"Unknown furniture style '{style_key}'."]
    soft = {"sofa", "armchair", "bed", "chair", "cushion", "ottoman", "dining_chair", "bench"}
    wood = {"wardrobe", "dining_table", "study_table", "tv_unit", "shelf", "bed", "side_table",
            "cabinet", "shoe_rack", "dressing_table", "counter"}
    changed = 0
    for floor in data.floors:
        for item in floor.furniture:
            if item.kind in soft:
                item.material = style["label"].lower()
                item.color = style["upholstery"]
                changed += 1
            elif item.kind in wood:
                item.material = "wood"
                item.color = style["wood"]
                changed += 1
    notes = [f"Applied {style['label']} finishes to {changed} furniture item(s). "
             f"{style['note']}"]
    return data, notes


def room_finish(data: ProjectData, room: Room) -> Dict[str, Any]:
    """Resolve the effective finish for one room (override -> global)."""
    m = data.materials
    wet = room.is_wet or room.type in ("bathroom", "toilet", "utility")
    return {
        "room_id": room.id,
        "floor_material": room.floor_material or ("tile" if wet else m.floor_material),
        "floor_color": m.bathroom_tile if wet and not room.floor_color
        else (room.floor_color or m.floor_color),
        "wall_color": room.wall_color or (m.bathroom_tile if wet else m.interior_wall),
        "feature_wall": m.feature_wall,
        "ceiling_style": m.ceiling_style,
        "outdoor": room.is_outdoor,
    }


def design_alternatives(data: ProjectData, count: int = 3) -> List[Dict[str, Any]]:
    """Three alternative looks derived from the current one -- no rebuild needed."""
    current = next((k for k, v in STYLES.items()
                    if v.materials.get("floor_material") == data.materials.floor_material), None)
    pool = [k for k in STYLE_ORDER if k != current]
    out = []
    for key in pool[:count]:
        palette = STYLES[key]
        out.append({"key": key, "label": palette.label, "description": palette.description,
                    "cost_impact": palette.cost_impact, "preview": dict(palette.materials),
                    "tips": list(palette.tips)})
    return out


def climate_advice(data: ProjectData) -> List[str]:
    """Honest, generic guidance tied to the declared climate -- not code advice."""
    c = data.plot.climate
    advice: Dict[str, List[str]] = {
        "hot_dry": ["Deep overhangs or a jali screen on the west and south facades cut the worst "
                    "of the afternoon heat.",
                    "Thick masonry with small west-facing openings keeps interiors cool at night.",
                    "Courtyards work well here: they shade themselves and drive stack ventilation."],
        "hot_humid": ["Cross ventilation matters more than thermal mass -- keep opposite openings "
                      "on every habitable room.",
                      "Raise habitable floors above the monsoon water line and slope all outdoor "
                      "surfaces to drains.",
                      "Avoid wall-to-wall carpet and untreated timber; both fail in sustained humidity."],
        "temperate": ["Moderate glazing on the south side gives useful winter sun without summer "
                      "overheating.",
                      "A shaded verandah extends the usable season."],
        "cold": ["Minimise north-facing glazing and add an insulated roof -- most heat is lost "
                 "through it.",
                 "A south-facing sun room or double-glazed windows pay back in comfort.",
                 "Watch for condensation on cold surfaces; provide trickle ventilation."],
        "composite": ["Design for both extremes: shading on west/south, and cross ventilation in "
                      "the humid months.",
                      "A roof insulation layer is the single most effective comfort upgrade."],
    }
    tips = advice.get(c, advice["composite"])
    tips.append("These are generic comfort suggestions for the climate you selected, not a "
                "substitute for local code requirements or an energy model.")
    return tips
