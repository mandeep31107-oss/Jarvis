/** Builds the shared scene model from ProjectData: real wall pieces split around
 *  door/window openings, hinged door panels, window frames + glass, stair treads,
 *  slabs, roofs, furniture and site elements. The 3D viewer, the walkthrough
 *  collision world and the GLB-style export all consume this same model. */

import type { Edge, Floor, Opening, ProjectData, Room, Stair } from "../../types/project";
import { deriveWalls, openingSpan, roomRect, wallBox, type Rect, type WallSegment } from "../walls";

export const FLOOR_HEIGHT = 3.0;
export const SLAB = 0.15;
export const EYE_HEIGHT = 1.65;

export interface BoxPiece {
  id: string;
  group: SceneGroup;
  /** plan-space centre + size + vertical span (metres, absolute z) */
  cx: number; cy: number; z0: number; z1: number;
  sx: number; sz: number;
  floorIndex: number;
  roomId?: string;
  openingId?: string;
  label?: string;
  materialHint?: string;
}

export type SceneGroup =
  | "slab" | "exterior_wall" | "interior_wall" | "door" | "window" | "glass"
  | "stair" | "roof" | "floor_finish" | "furniture" | "fixture" | "site" | "landscape" | "railing";

export interface DoorPanel {
  id: string;
  floorIndex: number;
  roomId: string;
  edge: Edge;
  /** hinge position in plan space */
  hx: number; hy: number;
  /** direction the free end points when closed (unit vector in plan) */
  dx: number; dy: number;
  width: number; height: number;
  axis: "x" | "y";
  line: number;
  label: string;
  isEntrance: boolean;
}

export interface WindowUnit {
  id: string;
  floorIndex: number;
  roomId: string;
  edge: Edge;
  cx: number; cy: number;
  width: number; sill: number; height: number;
  axis: "x" | "y";
  kind: Opening["kind"];
  label: string;
}

export interface StairModel {
  id: string;
  floorIndex: number;
  rect: Rect;
  direction: Edge;
  rise: number;
  tread: number;
  risers: number;
  landing: number;
  width: number;
  treads: { cx: number; cy: number; z0: number; z1: number; sx: number; sz: number }[];
  /** walkable ramp description used by the character controller */
  flightDepth: number;
  landingDepth: number;
}

export interface SceneModel {
  pieces: BoxPiece[];
  doors: DoorPanel[];
  windows: WindowUnit[];
  stairs: StairModel[];
  /** collision solids per floor index (plan-space AABBs) */
  solidsByFloor: Map<number, Rect[]>;
  /** walkable rects per floor (rooms + outdoor pads) */
  walkByFloor: Map<number, Rect[]>;
  floorCount: number;
  plot: { width: number; depth: number };
}

export function buildScene(data: ProjectData): SceneModel {
  const pieces: BoxPiece[] = [];
  const doors: DoorPanel[] = [];
  const windows: WindowUnit[] = [];
  const stairs: StairModel[] = [];
  const solidsByFloor = new Map<number, Rect[]>();
  const walkByFloor = new Map<number, Rect[]>();
  const plot = { width: data.plot.width, depth: data.plot.depth };

  const solids = (f: number) => {
    if (!solidsByFloor.has(f)) solidsByFloor.set(f, []);
    return solidsByFloor.get(f)!;
  };
  const walk = (f: number) => {
    if (!walkByFloor.has(f)) walkByFloor.set(f, []);
    return walkByFloor.get(f)!;
  };

  for (const floor of data.floors) {
    const f = floor.index;
    const z0 = f * FLOOR_HEIGHT;
    pieces.push({ id: `slab_${floor.id}`, group: "slab", cx: plot.width / 2, cy: plot.depth / 2,
      z0: z0 - SLAB, z1: z0, sx: plot.width, sz: plot.depth, floorIndex: f });

    for (const room of floor.rooms) {
      if (room.type === "open_to_below") continue;
      walk(f).push(roomRect(room));
      if (!room.is_outdoor) {
        pieces.push({ id: `finish_${room.id}`, group: "floor_finish", cx: room.x + room.w / 2,
          cy: room.y + room.d / 2, z0, z1: z0 + 0.03, sx: room.w, sz: room.d, floorIndex: f,
          roomId: room.id, materialHint: room.floor_material || undefined, label: room.name });
      } else {
        pieces.push({ id: `pad_${room.id}`, group: room.type === "parking" ? "site" : "landscape",
          cx: room.x + room.w / 2, cy: room.y + room.d / 2, z0: z0 + 0.01, z1: z0 + 0.05,
          sx: room.w, sz: room.d, floorIndex: f, roomId: room.id, label: room.name });
      }
    }

    // walls split around openings
    const walls = deriveWalls(floor.rooms);
    for (const wall of walls) {
      const spans = floor.openings
        .map((op) => ({ op, span: openingForWall(floor, wall, op) }))
        .filter((entry): entry is { op: Opening; span: { lo: number; hi: number } } => !!entry.span)
        .sort((a, b) => a.span.lo - b.span.lo);
      let cursor = wall.lo;
      const emit = (lo: number, hi: number, za: number, zb: number) => {
        if (hi - lo < 0.01 || zb - za < 0.01) return;
        const box = wall.axis === "y"
          ? { cx: (lo + hi) / 2, cy: wall.line, sx: hi - lo, sz: wall.thickness }
          : { cx: wall.line, cy: (lo + hi) / 2, sx: wall.thickness, sz: hi - lo };
        pieces.push({ id: `wall_${wall.id}_${lo.toFixed(2)}_${za.toFixed(2)}`,
          group: wall.kind === "exterior" ? "exterior_wall" : "interior_wall",
          z0: z0 + za, z1: z0 + zb, floorIndex: f, ...box });
        solids(f).push(wall.axis === "y"
          ? { x: lo, y: wall.line - wall.thickness / 2, w: hi - lo, d: wall.thickness }
          : { x: wall.line - wall.thickness / 2, y: lo, w: wall.thickness, d: hi - lo });
      };
      for (const { op, span } of spans) {
        const lo = Math.max(span.lo, wall.lo);
        const hi = Math.min(span.hi, wall.hi);
        if (hi <= lo) continue;
        if (lo > cursor) emit(cursor, lo, 0, wallHeight(floor));
        const sill = op.kind === "window" || op.kind === "vent" ? op.sill : 0;
        const head = Math.min(op.sill + op.height, wallHeight(floor));
        if (sill > 0.02) emit(lo, hi, 0, sill);
        if (head < wallHeight(floor) - 0.02) emit(lo, hi, head, wallHeight(floor));
        cursor = Math.max(cursor, hi);
        if (op.kind === "door" || op.kind === "sliding" || op.kind === "pass_through") {
          doors.push(makeDoor(floor, op, f));
        } else {
          windows.push(makeWindow(floor, op, f));
        }
      }
      if (cursor < wall.hi) emit(cursor, wall.hi, 0, wallHeight(floor));
    }

    // stairs
    for (const stair of floor.stairs) {
      const model = buildStair(stair, f, z0);
      stairs.push(model);
      for (const tread of model.treads) {
        pieces.push({ id: `tread_${stair.id}_${tread.cx.toFixed(2)}_${tread.cy.toFixed(2)}`,
          group: "stair", cx: tread.cx, cy: tread.cy, z0: tread.z0, z1: tread.z1,
          sx: tread.sx, sz: tread.sz, floorIndex: f });
      }
      // railing on the open side of the well
      pieces.push({ id: `rail_${stair.id}`, group: "railing",
        cx: stair.x + stair.w / 2, cy: stair.y + stair.d / 2, z0: z0 + FLOOR_HEIGHT,
        z1: z0 + FLOOR_HEIGHT + 0.95, sx: stair.w * 0.9, sz: 0.06, floorIndex: f + 1 });
    }

    // furniture
    for (const item of floor.furniture) {
      const fixture = ["toilet", "washbasin", "shower", "bathtub", "sink", "hob", "fridge",
        "washing_machine"].includes(item.kind);
      pieces.push({ id: item.id, group: fixture ? "fixture" : "furniture", cx: item.x,
        cy: item.y, z0: z0 + item.z, z1: z0 + item.z + item.height, sx: item.w, sz: item.d,
        floorIndex: f, roomId: item.room_id, label: item.label || item.kind,
        materialHint: item.material });
      if (item.height > 0.45) {
        solids(f).push({ x: item.x - item.w / 2, y: item.y - item.d / 2, w: item.w, d: item.d });
      }
    }

    // roof over the top habitable floor
    const topHabitable = Math.max(...data.floors
      .filter((fl) => fl.rooms.some((r) => !r.is_outdoor)).map((fl) => fl.index));
    if (f === topHabitable && !floor.is_terrace) {
      for (const room of floor.rooms) {
        if (room.is_outdoor) continue;
        pieces.push({ id: `roof_${room.id}`, group: "roof", cx: room.x + room.w / 2,
          cy: room.y + room.d / 2, z0: z0 + FLOOR_HEIGHT, z1: z0 + FLOOR_HEIGHT + SLAB,
          sx: room.w + 0.3, sz: room.d + 0.3, floorIndex: f, roomId: room.id });
      }
    }
  }

  // site: ground pad + optional boundary wall
  pieces.push({ id: "site_ground", group: "site", cx: plot.width / 2, cy: plot.depth / 2,
    z0: -0.12, z1: -0.02, sx: plot.width + 10, sz: plot.depth + 10, floorIndex: 0 });
  if (data.materials.boundary_wall) {
    const t = 0.15, h = 1.5;
    const gate = 2.6;
    const side = data.plot.road_side;
    const runs: Rect[] = [];
    // road-side run is split around a real gate opening so the journey starts outside
    if (side === "S" || side === "N") {
      const y = side === "S" ? 0 : plot.depth - t;
      const left = (plot.width - gate) / 2;
      runs.push({ x: 0, y, w: left, d: t }, { x: left + gate, y, w: left, d: t });
    } else {
      const y0 = 0;
      runs.push({ x: 0, y: y0, w: plot.width, d: t }, { x: 0, y: plot.depth - t, w: plot.width, d: t });
    }
    if (side === "E" || side === "W") {
      const x = side === "W" ? 0 : plot.width - t;
      const left = (plot.depth - gate) / 2;
      runs.push({ x, y: 0, w: t, d: left }, { x, y: left + gate, w: t, d: left });
    } else {
      runs.push({ x: 0, y: 0, w: t, d: plot.depth }, { x: plot.width - t, y: 0, w: t, d: plot.depth });
    }
    runs.forEach((run, i) => {
      if (run.w < 0.05 || run.d < 0.05) return;
      pieces.push({ id: `boundary_${i}`, group: "site", cx: run.x + run.w / 2,
        cy: run.y + run.d / 2, z0: 0, z1: h, sx: run.w, sz: run.d, floorIndex: 0 });
      solids(0).push(run);
    });
    // gate posts mark the opening
    const postAt = side === "S" || side === "N"
      ? [{ x: (plot.width - gate) / 2, y: side === "S" ? 0 : plot.depth - t },
         { x: (plot.width + gate) / 2, y: side === "S" ? 0 : plot.depth - t }]
      : [{ x: side === "W" ? 0 : plot.width - t, y: (plot.depth - gate) / 2 },
         { x: side === "W" ? 0 : plot.width - t, y: (plot.depth + gate) / 2 }];
    postAt.forEach((post, i) => {
      pieces.push({ id: `gate_post_${i}`, group: "site", cx: post.x + t / 2, cy: post.y + t / 2,
        z0: 0, z1: 1.9, sx: t + 0.1, sz: t + 0.1, floorIndex: 0 });
    });
  }

  return { pieces, doors, windows, stairs, solidsByFloor, walkByFloor,
    floorCount: data.floors.length, plot };
}

function wallHeight(floor: Floor): number {
  return floor.height || FLOOR_HEIGHT;
}

function openingForWall(floor: Floor, wall: WallSegment, op: Opening):
  { lo: number; hi: number } | null {
  const room = floor.rooms.find((r) => r.id === op.room_id);
  if (!room) return null;
  const span = openingSpan(room, op);
  if (span.axis !== wall.axis) return null;
  if (Math.abs(span.line - wall.line) > 0.08) return null;
  if (span.end < wall.lo - 0.05 || span.start > wall.hi + 0.05) return null;
  return { lo: span.start, hi: span.end };
}

function makeDoor(floor: Floor, op: Opening, f: number): DoorPanel {
  const room = floor.rooms.find((r) => r.id === op.room_id)!;
  const span = openingSpan(room, op);
  const hingeAtStart = true;
  const hx = span.axis === "y" ? span.start : span.line;
  const hy = span.axis === "y" ? span.line : span.start;
  // closed leaf lies along the wall; swing direction points into the room
  const into: Record<Edge, [number, number]> = { S: [0, 1], N: [0, -1], W: [1, 0], E: [-1, 0] };
  const [dx, dy] = into[op.edge];
  return { id: op.id, floorIndex: f, roomId: op.room_id, edge: op.edge, hx, hy, dx, dy,
    width: op.width, height: op.height, axis: span.axis, line: span.line,
    label: `${op.kind === "sliding" ? "Sliding door" : "Door"} · ${room.name}`,
    isEntrance: room.tags?.includes("entrance") || op.connects_to === null };
}

function makeWindow(floor: Floor, op: Opening, f: number): WindowUnit {
  const room = floor.rooms.find((r) => r.id === op.room_id)!;
  const span = openingSpan(room, op);
  const cx = span.axis === "y" ? (span.start + span.end) / 2 : span.line;
  const cy = span.axis === "y" ? span.line : (span.start + span.end) / 2;
  return { id: op.id, floorIndex: f, roomId: op.room_id, edge: op.edge, cx, cy,
    width: op.width, sill: op.sill, height: op.height, axis: span.axis, kind: op.kind,
    label: `Window · ${room.name}` };
}

export function buildStair(stair: Stair, f: number, z0: number): StairModel {
  const half = Math.max(1, Math.floor(stair.risers / 2));
  const rest = Math.max(1, stair.risers - half);
  const rise = stair.rise_m, tread = stair.tread_m;
  const alongY = stair.direction === "N" || stair.direction === "S";
  const sign = stair.direction === "N" || stair.direction === "E" ? 1 : -1;
  const halfW = (alongY ? stair.w : stair.d) / 2;
  const treads: StairModel["treads"] = [];
  const start = alongY
    ? (sign > 0 ? stair.y : stair.y + stair.d)
    : (sign > 0 ? stair.x : stair.x + stair.w);
  const put = (along: number, zc: number, band: "A" | "B") => {
    if (alongY) {
      treads.push({ cx: stair.x + (band === "A" ? stair.w * 0.25 : stair.w * 0.75), cy: along,
        z0: zc - rise / 2, z1: zc + rise / 2, sx: halfW, sz: tread });
    } else {
      treads.push({ cx: along, cy: stair.y + (band === "A" ? stair.d * 0.25 : stair.d * 0.75),
        z0: zc - rise / 2, z1: zc + rise / 2, sx: tread, sz: halfW });
    }
  };
  for (let i = 0; i < half; i++) put(start + sign * tread * (i + 0.5), z0 + rise * (i + 1) - rise / 2, "A");
  const landingStart = start + sign * tread * half;
  const landingMid = landingStart + sign * stair.landing_m / 2;
  if (alongY) {
    treads.push({ cx: stair.x + stair.w / 2, cy: landingMid, z0: z0 + rise * half - rise,
      z1: z0 + rise * half, sx: stair.w, sz: stair.landing_m });
  } else {
    treads.push({ cx: landingMid, cy: stair.y + stair.d / 2, z0: z0 + rise * half - rise,
      z1: z0 + rise * half, sx: stair.landing_m, sz: stair.d });
  }
  for (let j = 0; j < rest; j++) {
    put(landingStart + sign * stair.landing_m - sign * tread * (j + 0.5),
      z0 + rise * (half + j + 1) - rise / 2, "B");
  }
  return { id: stair.id, floorIndex: f, rect: { x: stair.x, y: stair.y, w: stair.w, d: stair.d },
    direction: stair.direction, rise, tread, risers: stair.risers, landing: stair.landing_m,
    width: stair.width_m, treads, flightDepth: tread * half, landingDepth: stair.landing_m };
}

/** Standing-surface height (absolute z) for a plan position, or null when the
 *  position is a hole / outside everything walkable. */
export function groundAt(model: SceneModel, data: ProjectData, x: number, y: number):
  { floor: number; z: number } | null {
  for (let f = model.floorCount - 1; f >= 0; f--) {
    const stair = model.stairs.find((s) => s.floorIndex === f && inside(s.rect, x, y));
    if (stair) {
      return { floor: f, z: stairHeight(stair, f, x, y) };
    }
    const floor = data.floors.find((fl) => fl.index === f);
    if (!floor) continue;
    for (const room of floor.rooms) {
      if (room.type === "open_to_below") continue;
      if (inside(roomRect(room), x, y)) {
        return { floor: f, z: f * FLOOR_HEIGHT + (room.level_offset || 0) };
      }
    }
  }
  // outside the building but inside the site pad
  if (x > -5 && y > -5 && x < model.plot.width + 5 && y < model.plot.depth + 5) {
    return { floor: 0, z: 0 };
  }
  return null;
}

export function inside(r: Rect, x: number, y: number, pad = 0): boolean {
  return x >= r.x - pad && x <= r.x + r.w + pad && y >= r.y - pad && y <= r.y + r.d + pad;
}

/** Continuous ramp height over a dog-leg stair so walking feels like steps. */
export function stairHeight(stair: StairModel, f: number, x: number, y: number): number {
  const base = f * FLOOR_HEIGHT;
  const { rect, direction, rise, risers, landing, flightDepth } = stair;
  const half = Math.max(1, Math.floor(risers / 2));
  const rest = Math.max(1, risers - half);
  const alongY = direction === "N" || direction === "S";
  const sign = direction === "N" || direction === "E" ? 1 : -1;
  const total = alongY ? rect.d : rect.w;
  const alongRaw = alongY ? (sign > 0 ? y - rect.y : rect.y + rect.d - y)
    : (sign > 0 ? x - rect.x : rect.x + rect.w - x);
  const along = Math.max(0, Math.min(total, alongRaw));
  const bandFrac = alongY ? (x - rect.x) / rect.w : (y - rect.y) / rect.d;
  const inA = bandFrac < 0.5;
  const midZ = base + rise * half;
  if (along <= flightDepth && inA) {
    return base + (along / Math.max(0.01, flightDepth)) * (rise * half);
  }
  if (along > flightDepth && along <= flightDepth + landing) return midZ;
  if (!inA) {
    const back = flightDepth + landing - along;
    const depthB = Math.max(0.01, stair.tread * rest);
    const t = Math.max(0, Math.min(1, back / depthB));
    return midZ + t * (rise * rest);
  }
  return midZ;
}

export function wallBoxOf(wall: WallSegment): Rect {
  return wallBox(wall);
}
