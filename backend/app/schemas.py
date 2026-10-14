"""The shared house data model.

This module is the single source of truth used by:

* the REST API (projects, versions, exports)
* the AI planning engine (layout generation, natural-language patches)
* the validation engine
* the frontend 2D editor and the 3D geometry builder (mirrored in
  ``frontend/src/types/project.ts``)

Geometry conventions
--------------------
* Canonical unit is the **metre**. The UI converts to feet/inches/yards on display.
* Plan space is compass aligned: ``+x = East``, ``+y = North``.
* A room is an axis-aligned rectangle ``(x, y, w, d)`` where ``(x, y)`` is the
  **south-west** corner, ``w`` runs East and ``d`` runs North.
* Walls are *derived* from room rectangles plus the plot boundary (see
  ``services/geometry.py``). Openings (doors/windows) are anchored to a room edge
  so they survive wall recomputation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

LengthUnit = Literal["ft", "in", "m", "cm", "yd"]
AreaUnit = Literal["sqft", "sqm", "sqyd", "gaj", "acre", "cent"]
Edge = Literal["N", "S", "E", "W"]
Compass = Literal["N", "S", "E", "W"]
PlotShape = Literal["rectangular", "square", "l_shape", "irregular", "custom"]
FinishLevel = Literal["economical", "standard", "premium"]
ConceptKey = Literal["space_efficient", "budget_conscious", "family_comfort", "modern_contemporary", "traditional_indian"]

RoomType = Literal[
    "living",
    "dining",
    "kitchen",
    "utility",
    "bedroom",
    "master_bedroom",
    "bathroom",
    "toilet",
    "study",
    "puja",
    "store",
    "stairs",
    "lobby",
    "corridor",
    "balcony",
    "terrace",
    "garden",
    "courtyard",
    "parking",
    "porch",
    "servant_room",
    "shop",
    "open_to_below",
]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Point(BaseModel):
    x: float
    y: float


class Setbacks(BaseModel):
    """Distance to leave clear on each plot edge before building (compass based)."""

    north: float = 1.2
    south: float = 2.5
    east: float = 1.0
    west: float = 1.0
    source: str = "default-assumption"
    verified: bool = False
    note: str = "Defaults are indicative. Confirm with your local building authority."

    def for_side(self, side: Compass) -> float:
        return {"N": self.north, "S": self.south, "E": self.east, "W": self.west}[side]

    def opposite(self, side: Compass) -> float:
        return {"N": self.south, "S": self.north, "E": self.west, "W": self.east}[side]

    @field_validator("north", "south", "east", "west")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        return max(0.0, float(v))


class Plot(BaseModel):
    width: float = Field(..., gt=0, description="East-West extent of the plot, metres")
    depth: float = Field(..., gt=0, description="North-South extent of the plot, metres")
    input_width: Optional[float] = None
    input_depth: Optional[float] = None
    input_unit: LengthUnit = "ft"
    shape: PlotShape = "rectangular"
    boundary: List[Point] = Field(default_factory=list, description="Optional polygon for irregular/custom plots")
    road_side: Compass = "S"
    road_width_m: Optional[float] = None
    setbacks: Setbacks = Field(default_factory=Setbacks)
    country: str = "India"
    state: str = ""
    city: str = ""
    city_tier: Literal["metro", "tier2", "tier3", "rural"] = "tier2"
    climate: Literal["hot_dry", "hot_humid", "temperate", "cold", "composite"] = "composite"

    @property
    def area(self) -> float:
        """Plot area in m² (polygon-aware)."""
        if self.boundary and len(self.boundary) >= 3:
            return polygon_area([(p.x, p.y) for p in self.boundary])
        return self.width * self.depth

    def buildable_rect(self) -> tuple[float, float, float, float]:
        """Envelope after setbacks -> (x, y, w, d) in plan space (+x East, +y North)."""
        sb = self.setbacks
        x0, y0 = sb.west, sb.south
        x1, y1 = self.width - sb.east, self.depth - sb.north
        return (x0, y0, max(0.5, x1 - x0), max(0.5, y1 - y0))

    def apply_setbacks(self, front: float, rear: float, side: float) -> None:
        """Map user friendly front/rear/side setbacks onto compass edges."""
        sb = self.setbacks
        side = max(0.0, side)
        if self.road_side == "S":
            sb.south, sb.north, sb.east, sb.west = front, rear, side, side
        elif self.road_side == "N":
            sb.north, sb.south, sb.east, sb.west = front, rear, side, side
        elif self.road_side == "E":
            sb.east, sb.west, sb.north, sb.south = front, rear, side, side
        else:
            sb.west, sb.east, sb.north, sb.south = front, rear, side, side


class Requirements(BaseModel):
    floors: int = Field(1, ge=1, le=5)
    bedrooms: int = Field(2, ge=0, le=8)
    bathrooms: int = Field(1, ge=0, le=8)
    attached_baths: int = Field(1, ge=0, le=4)
    kitchen_type: Literal["open", "closed", "modular", "platform_only"] = "closed"
    wants_living: bool = True
    wants_dining: bool = True
    wants_pooja: bool = False
    wants_study: bool = False
    wants_store: bool = True
    wants_utility: bool = True
    wants_parking: Literal["none", "two_wheeler", "one_car", "two_car"] = "none"
    wants_balcony: bool = True
    wants_terrace: bool = True
    wants_garden: bool = False
    wants_courtyard: bool = False
    wants_servant_room: bool = False
    wants_shop: bool = False
    family_size: int = 4
    accessibility: bool = False
    style: str = "modern"
    vastu: bool = False
    budget_target: Optional[float] = Field(None, description="Target budget in the project's currency")
    currency: str = "INR"
    finish_level: FinishLevel = "standard"
    preferred_materials: List[str] = Field(default_factory=list)
    preferred_colors: List[str] = Field(default_factory=list)
    future_expansion: str = ""
    extra_notes: str = ""
    concept: ConceptKey = "family_comfort"


class Opening(BaseModel):
    """A door / window anchored to a room edge."""

    id: str = Field(default_factory=lambda: new_id("op"))
    kind: Literal["door", "window", "sliding", "pass_through", "vent"] = "door"
    room_id: str
    floor_id: str = ""
    edge: Edge
    offset: float = Field(..., ge=0, description="Distance from the edge's start (W->E for N/S, S->N for E/W)")
    width: float = 0.9
    height: float = 2.1
    sill: float = 0.0
    connects_to: Optional[str] = Field(None, description="Neighbouring room id, 'exterior', or None")
    style: str = "flush"
    material: str = "wood"

    def clamped_offset(self, room: "Room") -> float:
        length = room.w if self.edge in ("N", "S") else room.d
        margin = min(self.width / 2 + 0.15, max(0.1, length / 2 - 0.05))
        return min(max(self.offset, margin), max(margin, length - margin))


class FurnitureItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fn"))
    kind: str
    room_id: str
    floor_id: str = ""
    x: float
    y: float
    w: float
    d: float
    height: float = 0.8
    z: float = Field(0.0, description="Elevation of the item base above the floor, metres")
    rotation: float = 0.0
    label: str = ""
    material: str = ""
    color: str = ""


class Stair(BaseModel):
    id: str = Field(default_factory=lambda: new_id("st"))
    floor_id: str = ""
    room_id: Optional[str] = None
    x: float
    y: float
    w: float
    d: float
    direction: Edge = "N"
    flights: int = 2
    rise_m: float = 0.17
    tread_m: float = 0.27
    risers: int = 18
    width_m: float = 1.0
    landing_m: float = 1.0
    goes_up: bool = True


class Room(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rm"))
    floor_id: str = ""
    name: str
    type: RoomType = "bedroom"
    x: float
    y: float
    w: float
    d: float
    ceiling_height: float = 3.0
    is_outdoor: bool = False
    is_wet: bool = False
    level_offset: float = 0.0
    color: Optional[str] = None
    floor_material: Optional[str] = None
    wall_color: Optional[str] = None
    notes: str = ""
    tags: List[str] = Field(default_factory=list, description="e.g. attached_to:<room_id>, entrance, wet_zone")

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.d)

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.d)

    @field_validator("w", "d")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Room dimensions must be positive")
        return round(float(v), 4)


class Floor(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fl"))
    index: int = 0
    name: str = "Ground Floor"
    level: float = 0.0
    height: float = 3.0
    slab_thickness: float = 0.15
    is_terrace: bool = False
    rooms: List[Room] = Field(default_factory=list)
    openings: List[Opening] = Field(default_factory=list)
    furniture: List[FurnitureItem] = Field(default_factory=list)
    stairs: List[Stair] = Field(default_factory=list)

    def room(self, room_id: str) -> Optional[Room]:
        return next((r for r in self.rooms if r.id == room_id), None)

    @property
    def built_up_area(self) -> float:
        return sum(r.area for r in self.rooms if not r.is_outdoor)

    @property
    def covered_area(self) -> float:
        return sum(r.area for r in self.rooms)


class MaterialSet(BaseModel):
    """Interior + exterior material / colour choices applied by the 3D renderer."""

    exterior_wall: str = "#e8e2d6"
    exterior_accent: str = "#8a6a4f"
    interior_wall: str = "#f5f2ec"
    feature_wall: str = "#4a6fa5"
    floor_material: Literal["tile", "marble", "wood", "concrete", "granite", "kota_stone"] = "tile"
    floor_color: str = "#cbb89a"
    bathroom_tile: str = "#dfe7ea"
    kitchen_counter: str = "#2f3437"
    kitchen_cabinet: str = "#7d5a3c"
    ceiling_style: Literal["plain", "cove", "grid", "beam"] = "plain"
    roof_type: Literal["flat", "gable", "hip", "shed", "terrace"] = "flat"
    roof_color: str = "#7c6a5c"
    door_material: Literal["wood", "panel", "glass", "metal"] = "wood"
    window_frame: str = "#3a3f45"
    facade_style: str = "contemporary"
    boundary_wall: bool = True
    boundary_color: str = "#cfc7b8"
    gate_color: str = "#4b4f55"
    landscaping: bool = True
    exterior_lighting: bool = True
    curtain_color: str = "#d9d2c5"


class BudgetAssumptions(BaseModel):
    finish_level: FinishLevel = "standard"
    city_tier: str = "tier2"
    currency: str = "INR"
    contingency_pct: float = 8.0
    labour_share_pct: float = 30.0
    custom_rates: Dict[str, float] = Field(default_factory=dict)
    rate_basis: str = ""
    include_furniture: bool = True
    include_landscaping: bool = True
    gst_pct: float = 0.0
    verified: bool = False


class BudgetLine(BaseModel):
    """One cost category in the estimate."""

    category: str
    label: str
    quantity: float = 0.0
    unit: str = "m²"
    rate_low: float = 0.0
    rate_high: float = 0.0
    amount_low: float = 0.0
    amount_high: float = 0.0
    amount_mid: float = 0.0
    labour_share_pct: float = 30.0
    source: str = ""
    verified: bool = False
    note: str = ""


class BudgetEstimate(BaseModel):
    currency: str = "INR"
    finish_level: FinishLevel = "standard"
    city_tier: str = "tier2"
    built_up_m2: float = 0.0
    plot_area_m2: float = 0.0
    lines: List[BudgetLine] = Field(default_factory=list)
    subtotal_low: float = 0.0
    subtotal_high: float = 0.0
    subtotal_mid: float = 0.0
    contingency_low: float = 0.0
    contingency_high: float = 0.0
    gst: float = 0.0
    total_low: float = 0.0
    total_high: float = 0.0
    total_mid: float = 0.0
    rate_per_sqm_low: float = 0.0
    rate_per_sqm_high: float = 0.0
    rate_per_sqft_low: float = 0.0
    rate_per_sqft_high: float = 0.0
    labour_total: float = 0.0
    material_total: float = 0.0
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    disclaimer: str = ""
    generated_at: Optional[datetime] = None


class BudgetComparison(BaseModel):
    label: str
    key: str = ""
    built_up_m2: float = 0.0
    total_low: float = 0.0
    total_high: float = 0.0
    total_mid: float = 0.0
    rate_per_sqft_low: float = 0.0
    rate_per_sqft_high: float = 0.0
    bedrooms: int = 0
    bathrooms: int = 0
    circulation_pct: float = 0.0
    open_space_pct: float = 0.0
    usable_area_m2: float = 0.0
    notes: List[str] = Field(default_factory=list)


class ImageRef(BaseModel):
    id: str = ""
    filename: str = ""
    url: str = ""
    room_type: str = "other"
    goals: List[str] = Field(default_factory=list)
    note: str = ""
    analysis: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["uploaded", "analysing", "analysed", "failed"] = "uploaded"
    created_at: Optional[datetime] = None
    generated: List[Dict[str, Any]] = Field(default_factory=list)


class Assumption(BaseModel):
    topic: str
    statement: str
    severity: Literal["info", "warning", "critical"] = "info"


class DesignMetrics(BaseModel):
    plot_area_m2: float = 0.0
    buildable_area_m2: float = 0.0
    ground_coverage_pct: float = 0.0
    total_built_up_m2: float = 0.0
    total_built_up_sqft: float = 0.0
    floor_areas_m2: Dict[str, float] = Field(default_factory=dict)
    room_count: int = 0
    bedroom_count: int = 0
    bathroom_count: int = 0
    circulation_pct: float = 0.0
    open_space_m2: float = 0.0
    open_space_pct: float = 0.0
    parking_area_m2: float = 0.0


class ConceptSummary(BaseModel):
    key: ConceptKey
    label: str
    blurb: str
    trade_offs: List[str] = Field(default_factory=list)
    metrics: DesignMetrics = Field(default_factory=DesignMetrics)


class ProjectData(BaseModel):
    """Full project document (persisted as JSON, mirrored by the TS types)."""

    model_config = ConfigDict(validate_assignment=False)

    schema_version: int = 1
    name: str = "Untitled House Project"
    unit_system: Literal["metric", "imperial"] = "imperial"
    length_unit: LengthUnit = "ft"
    area_unit: AreaUnit = "sqft"
    plot: Plot
    requirements: Requirements = Field(default_factory=Requirements)
    floors: List[Floor] = Field(default_factory=list)
    materials: MaterialSet = Field(default_factory=MaterialSet)
    budget: BudgetAssumptions = Field(default_factory=BudgetAssumptions)
    images: List[ImageRef] = Field(default_factory=list)
    metrics: DesignMetrics = Field(default_factory=DesignMetrics)
    concepts: List[ConceptSummary] = Field(default_factory=list)
    active_concept: Optional[ConceptKey] = None
    assumptions: List[Assumption] = Field(default_factory=list)
    ai_recommendations: List[Dict[str, Any]] = Field(default_factory=list)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    last_validated: Optional[datetime] = None

    def floor(self, floor_id: str) -> Optional[Floor]:
        return next((f for f in self.floors if f.id == floor_id), None)

    def floor_by_index(self, index: int) -> Optional[Floor]:
        return next((f for f in self.floors if f.index == index), None)

    def find_room(self, room_id: str) -> tuple[Optional[Floor], Optional[Room]]:
        for f in self.floors:
            r = f.room(room_id)
            if r:
                return f, r
        return None, None

    def all_rooms(self) -> List[Room]:
        return [r for f in self.floors for r in f.rooms]

    def log_event(self, kind: str, message: str, **extra: Any) -> None:
        entry = {"at": utcnow().isoformat(), "kind": kind, "message": message}
        entry.update(extra)
        self.history.append(entry)
        if len(self.history) > 400:
            self.history = self.history[-400:]


class ProjectRecord(BaseModel):
    id: str
    name: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    share_token: Optional[str] = None
    shared: bool = False
    data: ProjectData


class ProjectVersion(BaseModel):
    id: str
    project_id: str
    label: str
    source: str = "manual"
    created_at: datetime
    data: ProjectData


IssueSource = Literal["geometry", "regulation", "structure", "accessibility",
                      "programme", "comfort", "budget"]


class ValidationIssue(BaseModel):
    """One thing a human should look at in the design."""

    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    room_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_ids: List[str] = Field(default_factory=list)
    floor_name: Optional[str] = None
    suggested_fix: Optional[str] = None
    source: IssueSource = "geometry"


class ValidationResult(BaseModel):
    ok: bool
    issues: List[ValidationIssue] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=utcnow)
    disclaimer: str = ("Automated checks only. They do not certify structural safety, fire safety or "
                       "legal compliance -- a licensed architect and structural engineer must review "
                       "any design before construction.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def polygon_area(points: List[tuple[float, float]]) -> float:
    """Shoelace area of a simple polygon (m²)."""
    n = len(points)
    if n < 3:
        return 0.0
    acc = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0
