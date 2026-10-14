/** Character controller for the walkthrough: circle-vs-AABB collision in plan
 *  space with wall sliding, step-up limits, stair ramps and door states. Pure
 *  TypeScript so it can be unit tested without a browser. */

import type { ProjectData } from "../../types/project";
import { inside, groundAt, stairHeight, type SceneModel, type StairModel } from "./scene";
import type { Rect } from "../walls";

export const PLAYER_RADIUS = 0.28;
export const STEP_HEIGHT = 0.26;

export interface DoorStates { [openingId: string]: number }   // 0 closed .. 1 open

export interface CollisionWorld {
  model: SceneModel;
  data: ProjectData;
  doorStates: DoorStates;
  /** extra solids to ignore (e.g. hidden furniture) */
  ignoreSolids?: Set<string>;
  furnitureBlocks: boolean;
}

export interface MoveResult {
  x: number; y: number; z: number; floor: number;
  collided: boolean; onStair: StairModel | null;
}

function solidsFor(world: CollisionWorld, floor: number): Rect[] {
  const base = world.model.solidsByFloor.get(floor) || [];
  const out: Rect[] = [];
  for (const rect of base) out.push(rect);
  // closed doors block their doorway
  for (const door of world.model.doors) {
    if (door.floorIndex !== floor) continue;
    const open = world.doorStates[door.id] || 0;
    if (open > 0.55) continue;
    const t = 0.12;
    if (door.axis === "y") out.push({ x: door.hx - 0.02, y: door.line - t / 2, w: door.width + 0.04, d: t });
    else out.push({ x: door.line - t / 2, y: door.hy - 0.02, w: t, d: door.width + 0.04 });
  }
  if (!world.furnitureBlocks) {
    // furniture solids were appended after wall solids; drop them by size heuristic
    return out.filter((r) => !(r.w < 2.6 && r.d < 2.6 && isFurnitureRect(world, floor, r)));
  }
  return out;
}

function isFurnitureRect(world: CollisionWorld, floor: number, r: Rect): boolean {
  const f = world.data.floors.find((fl) => fl.index === floor);
  if (!f) return false;
  return f.furniture.some((item) => Math.abs(item.x - (r.x + r.w / 2)) < 0.01
    && Math.abs(item.y - (r.y + r.d / 2)) < 0.01);
}

function circleVsRects(x: number, y: number, radius: number, rects: Rect[]):
  { x: number; y: number; hit: boolean } {
  let px = x, py = y, hit = false;
  for (const r of rects) {
    const cx = Math.max(r.x, Math.min(px, r.x + r.w));
    const cy = Math.max(r.y, Math.min(py, r.y + r.d));
    let dx = px - cx, dy = py - cy;
    let dist2 = dx * dx + dy * dy;
    if (dist2 >= radius * radius) continue;
    hit = true;
    if (dist2 < 1e-9) {
      // centre inside rect: push out along the smallest penetration axis
      const left = px - r.x, right = r.x + r.w - px, bottom = py - r.y, top = r.y + r.d - py;
      const min = Math.min(left, right, bottom, top);
      if (min === left) px = r.x - radius;
      else if (min === right) px = r.x + r.w + radius;
      else if (min === bottom) py = r.y - radius;
      else py = r.y + r.d + radius;
      continue;
    }
    const dist = Math.sqrt(dist2);
    const push = (radius - dist) / dist;
    px += dx * push;
    py += dy * push;
  }
  return { x: px, y: py, hit };
}

/** Attempt a plan-space move; returns the resolved position + standing height. */
export function movePlayer(world: CollisionWorld, from: { x: number; y: number; z: number; floor: number },
  dx: number, dy: number): MoveResult {
  const targetX = from.x + dx;
  const targetY = from.y + dy;
  // resolve axis separately for natural wall sliding
  const rectsHere = solidsFor(world, from.floor);
  const stepX = circleVsRects(targetX, from.y, PLAYER_RADIUS, rectsHere);
  const stepY = circleVsRects(stepX.x, targetY, PLAYER_RADIUS, rectsHere);
  let x = stepX.x, y = stepY.y;
  // site boundary: keep the player on the property pad
  x = Math.max(-2.6, Math.min(world.model.plot.width + 2.6, x));
  y = Math.max(-2.6, Math.min(world.model.plot.depth + 2.6, y));

  const ground = groundAt(world.model, world.data, x, y);
  if (!ground) {
    // tried to step into a hole or off the pad: stay put on the failing axis
    const gx = groundAt(world.model, world.data, x, from.y);
    const gy = groundAt(world.model, world.data, from.x, y);
    if (gx) { y = from.y; }
    else if (gy) { x = from.x; }
    else { x = from.x; y = from.y; }
    const fallback = groundAt(world.model, world.data, x, y);
    if (!fallback) return { ...from, collided: true, onStair: null };
    return { x, y, z: fallback.z, floor: fallback.floor, collided: true,
      onStair: stairAt(world, fallback.floor, x, y) };
  }
  // step-up limit: no jumping floors except via stairs (continuous ramps)
  const dz = ground.z - from.z;
  const onStairNow = stairAt(world, from.floor, from.x, from.y);
  const onStairNext = stairAt(world, ground.floor, x, y);
  if (!onStairNow && !onStairNext && dz > STEP_HEIGHT) {
    return { ...from, collided: true, onStair: null };
  }
  if (dz > 0.9 && !onStairNow && !onStairNext) return { ...from, collided: true, onStair: null };
  return { x, y, z: ground.z, floor: ground.floor, collided: stepX.hit || stepY.hit,
    onStair: onStairNext || onStairNow };
}

function stairAt(world: CollisionWorld, floor: number, x: number, y: number): StairModel | null {
  return world.model.stairs.find((s) => s.floorIndex === floor && inside(s.rect, x, y)) || null;
}

/** A safe spawn point: centre of the entrance room, else first indoor room. */
export function spawnPoint(data: ProjectData): { x: number; y: number; z: number; floor: number } {
  const ground = data.floors.find((f) => f.index === 0);
  const entrance = ground?.rooms.find((r) => r.tags?.includes("entrance"))
    || ground?.rooms.find((r) => r.type === "lobby" || r.type === "living")
    || ground?.rooms.find((r) => !r.is_outdoor);
  if (entrance) return { x: entrance.x + entrance.w / 2, y: entrance.y + entrance.d / 2, z: 0, floor: 0 };
  return { x: data.plot.width / 2, y: -2, z: 0, floor: 0 };
}

/** Street view spawn: outside the plot on the road side. */
export function streetSpawn(data: ProjectData): { x: number; y: number; z: number; floor: number } {
  const { width, depth } = data.plot;
  const side = data.plot.road_side;
  const c = { x: width / 2, y: depth / 2 };
  if (side === "S") return { x: c.x, y: -4.5, z: 0, floor: 0 };
  if (side === "N") return { x: c.x, y: depth + 4.5, z: 0, floor: 0 };
  if (side === "W") return { x: -4.5, y: c.y, z: 0, floor: 0 };
  return { x: width + 4.5, y: c.y, z: 0, floor: 0 };
}

export { stairHeight };
