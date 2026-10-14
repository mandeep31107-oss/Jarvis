/** Client-side mirror of backend/app/services/geometry.py: walls are *derived*
 *  from room rectangles so the 2D editor, the 3D viewer and the walkthrough all
 *  agree with the backend without duplicating state. */

import type { Edge, Floor, Opening, Room } from "../types/project";

export const EXTERIOR_THICKNESS = 0.23;
export const INTERIOR_THICKNESS = 0.115;

export interface WallSegment {
  id: string;
  axis: "x" | "y";           // "x" runs north-south, "y" runs east-west
  line: number;              // centre-line coordinate on the cross axis
  lo: number;
  hi: number;
  thickness: number;
  kind: "exterior" | "interior";
  rooms: string[];
  openings: string[];
}

export interface Rect { x: number; y: number; w: number; d: number }

export const roomRect = (room: Room): Rect => ({ x: room.x, y: room.y, w: room.w, d: room.d });
export const rectArea = (r: Rect) => Math.max(0, r.w) * Math.max(0, r.d);
export const roomArea = (r: { w: number; d: number }) => Math.max(0, r.w) * Math.max(0, r.d);

const KEY = 0.001;
const round3 = (v: number) => Math.round(v * 1000) / 1000;

interface EdgeRun { line: number; lo: number; hi: number; rooms: string[]; interior: boolean }

export function deriveWalls(rooms: Room[]): WallSegment[] {
  const horizontal = new Map<string, EdgeRun>();   // axis "y": line = y coordinate
  const vertical = new Map<string, EdgeRun>();     // axis "x": line = x coordinate

  const bump = (map: Map<string, EdgeRun>, line: number, lo: number, hi: number,
                roomId: string, interior: boolean) => {
    const key = `${round3(line)}`;
    const run = map.get(key);
    if (!run) {
      map.set(key, { line, lo, hi, rooms: [roomId], interior });
    } else {
      run.lo = Math.min(run.lo, lo);
      run.hi = Math.max(run.hi, hi);
      if (!run.rooms.includes(roomId)) run.rooms.push(roomId);
      run.interior = run.interior && interior;
    }
  };

  for (const room of rooms) {
    if (room.is_outdoor && room.type !== "balcony") continue;   // gardens etc. have no walls
    const r = roomRect(room);
    // find neighbours sharing each edge to decide interior vs exterior
    bump(horizontal, round3(r.y), round3(r.x), round3(r.x + r.w), room.id,
      !!neighbourOn(rooms, room, "S"));
    bump(horizontal, round3(r.y + r.d), round3(r.x), round3(r.x + r.w), room.id,
      !!neighbourOn(rooms, room, "N"));
    bump(vertical, round3(r.x), round3(r.y), round3(r.y + r.d), room.id,
      !!neighbourOn(rooms, room, "W"));
    bump(vertical, round3(r.x + r.w), round3(r.y), round3(r.y + r.d), room.id,
      !!neighbourOn(rooms, room, "E"));
  }

  const walls: WallSegment[] = [];
  const merge = (map: Map<string, EdgeRun>, axis: "x" | "y") => {
    for (const run of map.values()) {
      walls.push({
        id: `w_${axis}_${round3(run.line)}_${round3(run.lo)}_${round3(run.hi)}`,
        axis, line: run.line, lo: run.lo, hi: run.hi,
        thickness: run.rooms.length > 1 || run.interior ? INTERIOR_THICKNESS : EXTERIOR_THICKNESS,
        kind: run.rooms.length > 1 || run.interior ? "interior" : "exterior",
        rooms: run.rooms, openings: [],
      });
    }
  };
  merge(horizontal, "y");
  merge(vertical, "x");

  // split collinear duplicates that only touch (keep full runs; overlaps merged above)
  walls.sort((a, b) => a.line - b.line || a.lo - b.lo);
  return walls.filter((w) => w.hi - w.lo > 0.05);
}

export function neighbourOn(rooms: Room[], room: Room, edge: Edge): Room | null {
  const r = roomRect(room);
  for (const other of rooms) {
    if (other.id === room.id || other.is_outdoor) continue;
    const o = roomRect(other);
    const touchX = Math.min(r.x + r.w, o.x + o.w) - Math.max(r.x, o.x);
    const touchY = Math.min(r.y + r.d, o.y + o.d) - Math.max(r.y, o.y);
    if (edge === "S" && Math.abs(o.y + o.d - r.y) < 0.06 && touchX > 0.3) return other;
    if (edge === "N" && Math.abs(o.y - (r.y + r.d)) < 0.06 && touchX > 0.3) return other;
    if (edge === "W" && Math.abs(o.x + o.w - r.x) < 0.06 && touchY > 0.3) return other;
    if (edge === "E" && Math.abs(o.x - (r.x + r.w)) < 0.06 && touchY > 0.3) return other;
  }
  return null;
}

export function wallBox(wall: WallSegment): Rect {
  const t = wall.thickness;
  if (wall.axis === "y") return { x: wall.lo, y: wall.line - t / 2, w: wall.hi - wall.lo, d: t };
  return { x: wall.line - t / 2, y: wall.lo, w: t, d: wall.hi - wall.lo };
}

/** World position of an opening rectangle in plan space. */
export function openingSpan(room: Room, opening: Opening): { start: number; end: number; line: number; axis: "x" | "y" } {
  const r = roomRect(room);
  if (opening.edge === "S") return { start: r.x + opening.offset, end: r.x + opening.offset + opening.width, line: r.y, axis: "y" };
  if (opening.edge === "N") return { start: r.x + opening.offset, end: r.x + opening.offset + opening.width, line: r.y + r.d, axis: "y" };
  if (opening.edge === "W") return { start: r.y + opening.offset, end: r.y + opening.offset + opening.width, line: r.x, axis: "x" };
  return { start: r.y + opening.offset, end: r.y + opening.offset + opening.width, line: r.x + r.w, axis: "x" };
}

export function edgeLength(room: Room, edge: Edge): number {
  return edge === "N" || edge === "S" ? room.w : room.d;
}

export const snap = (value: number, grid = 0.05) => Math.round(value / grid) * grid;
export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
export const KEY_TOL = KEY;
