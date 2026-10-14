/** TypeScript mirror of backend/app/schemas.py.
 *  Units: metres. Plan origin = south-west corner of the plot, +x East, +y North.
 *  Room (x, y) = south-west corner; furniture (x, y) = centre of the footprint.
 */

export type Edge = "N" | "S" | "E" | "W";
export type LengthUnit = "ft" | "in" | "m" | "cm" | "yd";
export type AreaUnit = "sqft" | "sqm" | "sqyd" | "gaj" | "acre" | "cent";
export type ConceptKey =
  | "space_efficient"
  | "budget_conscious"
  | "family_comfort"
  | "modern_contemporary"
  | "traditional_indian";
export type PlotShape = "rectangular" | "square" | "l_shape" | "irregular" | "custom";
export type CityTier = "metro" | "tier2" | "tier3" | "rural";
export type Climate = "hot_dry" | "hot_humid" | "temperate" | "cold" | "composite";
export type FinishLevel = "economical" | "standard" | "premium";
export type RoomType =
  | "living" | "dining" | "kitchen" | "utility" | "bedroom" | "master_bedroom"
  | "bathroom" | "toilet" | "study" | "puja" | "store" | "stairs" | "lobby"
  | "corridor" | "balcony" | "terrace" | "garden" | "courtyard" | "parking"
  | "porch" | "servant_room" | "shop" | "open_to_below";
export type OpeningKind = "door" | "window" | "sliding" | "pass_through" | "vent";

export interface Point { x: number; y: number }

export interface Setbacks {
  north: number; south: number; east: number; west: number;
  source: string; verified: boolean; note: string;
}

export interface Plot {
  width: number; depth: number;
  input_width?: number | null; input_depth?: number | null;
  input_unit: LengthUnit; shape: PlotShape; boundary: Point[];
  road_side: Edge; road_width_m?: number | null; setbacks: Setbacks;
  country: string; state: string; city: string;
  city_tier: CityTier; climate: Climate;
}

export interface Requirements {
  floors: number; bedrooms: number; bathrooms: number; attached_baths: number;
  kitchen_type: "open" | "closed" | "modular" | "platform_only";
  wants_living: boolean; wants_dining: boolean; wants_pooja: boolean;
  wants_study: boolean; wants_store: boolean; wants_utility: boolean;
  wants_parking: "none" | "two_wheeler" | "one_car" | "two_car";
  wants_balcony: boolean; wants_terrace: boolean; wants_garden: boolean;
  wants_courtyard: boolean; wants_servant_room: boolean; wants_shop: boolean;
  family_size: number; accessibility: boolean; style: string; vastu: boolean;
  budget_target?: number | null; currency: string; finish_level: FinishLevel;
  preferred_materials: string[]; preferred_colors: string[];
  future_expansion: string; extra_notes: string; concept: ConceptKey;
}

export interface Opening {
  id: string; kind: OpeningKind; room_id: string; floor_id: string; edge: Edge;
  offset: number; width: number; height: number; sill: number;
  connects_to?: string | null; style: string; material: string;
}

export interface FurnitureItem {
  id: string; kind: string; room_id: string; floor_id: string;
  x: number; y: number; w: number; d: number; height: number; z: number;
  rotation: number; label: string; material: string; color: string;
}

export interface Stair {
  id: string; floor_id: string; room_id?: string | null;
  x: number; y: number; w: number; d: number; direction: Edge; flights: number;
  rise_m: number; tread_m: number; risers: number; width_m: number;
  landing_m: number; goes_up: boolean;
}

export interface Room {
  id: string; floor_id: string; name: string; type: RoomType;
  x: number; y: number; w: number; d: number; ceiling_height: number;
  is_outdoor: boolean; is_wet: boolean; level_offset: number;
  color?: string | null; floor_material?: string | null; wall_color?: string | null;
  notes: string; tags: string[];
}

export interface Floor {
  id: string; index: number; name: string; level: number; height: number;
  slab_thickness: number; is_terrace: boolean;
  rooms: Room[]; openings: Opening[]; furniture: FurnitureItem[]; stairs: Stair[];
}

export interface MaterialSet {
  exterior_wall: string; exterior_accent: string; interior_wall: string;
  feature_wall: string;
  floor_material: "tile" | "marble" | "wood" | "concrete" | "granite" | "kota_stone";
  floor_color: string; bathroom_tile: string; kitchen_counter: string;
  kitchen_cabinet: string;
  ceiling_style: "plain" | "cove" | "grid" | "beam";
  roof_type: "flat" | "gable" | "hip" | "shed" | "terrace";
  roof_color: string;
  door_material: "wood" | "panel" | "glass" | "metal";
  window_frame: string; facade_style: string;
  boundary_wall: boolean; boundary_color: string; gate_color: string;
  landscaping: boolean; exterior_lighting: boolean; curtain_color: string;
}

export interface BudgetAssumptions {
  finish_level: FinishLevel; city_tier: string; currency: string;
  contingency_pct: number; labour_share_pct: number;
  custom_rates: Record<string, number>; rate_basis: string;
  include_furniture: boolean; include_landscaping: boolean;
  gst_pct: number; verified: boolean;
}

export interface BudgetLine {
  category: string; label: string; quantity: number; unit: string;
  rate_low: number; rate_high: number; amount_low: number; amount_high: number;
  amount_mid: number; labour_share_pct: number; source: string;
  verified: boolean; note: string;
}

export interface BudgetEstimate {
  currency: string; finish_level: FinishLevel; city_tier: string;
  built_up_m2: number; plot_area_m2: number; lines: BudgetLine[];
  subtotal_low: number; subtotal_high: number; subtotal_mid: number;
  contingency_low: number; contingency_high: number; gst: number;
  total_low: number; total_high: number; total_mid: number;
  rate_per_sqm_low: number; rate_per_sqm_high: number;
  rate_per_sqft_low: number; rate_per_sqft_high: number;
  labour_total: number; material_total: number;
  assumptions: string[]; warnings: string[]; disclaimer: string;
  generated_at?: string | null;
}

export interface BudgetComparison {
  label: string; key: string; built_up_m2: number;
  total_low: number; total_high: number; total_mid: number;
  rate_per_sqft_low: number; rate_per_sqft_high: number;
  bedrooms: number; bathrooms: number; circulation_pct: number;
  open_space_pct: number; usable_area_m2: number; notes: string[];
}

export interface ImageRef {
  id: string; filename: string; url: string; room_type: string; goals: string[];
  note: string; analysis: Record<string, unknown>;
  status: "uploaded" | "analysing" | "analysed" | "failed";
  created_at?: string | null; generated: Record<string, unknown>[];
}

export interface Assumption { topic: string; statement: string; severity: "info" | "warning" | "critical" }

export interface DesignMetrics {
  plot_area_m2: number; buildable_area_m2: number; ground_coverage_pct: number;
  total_built_up_m2: number; total_built_up_sqft: number;
  floor_areas_m2: Record<string, number>; room_count: number;
  bedroom_count: number; bathroom_count: number; circulation_pct: number;
  open_space_m2: number; open_space_pct: number; parking_area_m2: number;
}

export interface ConceptSummary {
  key: ConceptKey; label: string; blurb: string; trade_offs: string[];
  metrics: DesignMetrics;
}

export interface ProjectData {
  schema_version: number; name: string;
  unit_system: "metric" | "imperial"; length_unit: LengthUnit; area_unit: AreaUnit;
  plot: Plot; requirements: Requirements; floors: Floor[]; materials: MaterialSet;
  budget: BudgetAssumptions; images: ImageRef[]; metrics: DesignMetrics;
  concepts: ConceptSummary[]; active_concept: ConceptKey | null;
  assumptions: Assumption[]; ai_recommendations: Record<string, unknown>[];
  history: Record<string, unknown>[]; last_validated?: string | null;
}

export interface ValidationIssue {
  code: string; severity: "info" | "warning" | "error"; message: string;
  room_id?: string | null; floor_id?: string | null; room_ids: string[];
  floor_name?: string | null; suggested_fix?: string | null;
  source: "geometry" | "regulation" | "structure" | "accessibility" | "programme" | "comfort" | "budget";
}

export interface ValidationResult {
  ok: boolean; issues: ValidationIssue[]; counts: Record<string, number>;
  checked_at: string; disclaimer: string;
}

export interface ProjectRecord {
  id: string; name: string; owner_id: string; created_at?: string | null;
  updated_at?: string | null; share_token?: string | null;
  sharing_enabled: boolean;
  summary: ProjectSummary; validation: { counts: Record<string, number>; issues: ValidationIssue[] };
  missing_information: MissingInfo[]; data: ProjectData;
}

export interface ProjectSummary {
  [key: string]: unknown;
}

export interface MissingInfo { field: string; question: string; why: string; options?: string[] }

export interface VersionInfo {
  id: string; project_id: string; label: string; source: string; note: string;
  created_at: string; built_up_m2: number; floors: number; rooms: number;
}

export interface ExportInfo {
  id: string; kind: string; filename: string; url: string; size_bytes: number;
  meta: Record<string, unknown>; created_at: string;
}

export interface ChatMessage {
  id: string; role: "user" | "assistant" | "system"; content: string;
  meta: Record<string, unknown>; created_at: string;
}

export interface ProviderStatus {
  provider: string; configured: boolean; model?: string | null; requires_key: boolean;
}
