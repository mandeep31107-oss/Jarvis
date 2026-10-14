/** Room navigation graph built from the *actual* plan: nodes are walkable rooms
 *  (plus the street), edges exist only where a door/opening really connects two
 *  spaces or where a stair connects two floors. Used by the guided tour, the
 *  "walk me to this room" feature and the inaccessibility warnings. */

import type { Floor, ProjectData, Room } from "../../types/project";
import { openingSpan } from "../walls";

export interface NavNode {
  id: string;
  label: string;
  floor: number;
  x: number; y: number;
  kind: "room" | "street" | "stair";
  roomId?: string;
}

export interface NavEdge { from: string; to: string; via: { x: number; y: number; floor: number }; kind: "door" | "stair" | "step" }

export interface NavGraph { nodes: NavNode[]; edges: NavEdge[] }

export function buildNavGraph(data: ProjectData): NavGraph {
  const nodes: NavNode[] = [];
  const edges: NavEdge[] = [];
  const nodeFor = new Map<string, NavNode>();

  const street: NavNode = { id: "street", label: "Street", floor: 0, x: data.plot.width / 2,
    y: data.plot.road_side === "S" ? -3.5 : data.plot.road_side === "N" ? data.plot.depth + 3.5 : data.plot.road_side === "W" ? -3.5 : data.plot.width + 3.5,
    kind: "street" };
  if (data.plot.road_side === "E") street.x = data.plot.width + 3.5, street.y = data.plot.depth / 2;
  if (data.plot.road_side === "W") street.x = -3.5, street.y = data.plot.depth / 2;
  nodes.push(street); nodeFor.set(street.id, street);

  for (const floor of data.floors) {
    for (const room of floor.rooms) {
      if (room.type === "open_to_below") continue;
      const node: NavNode = { id: `${floor.index}:${room.id}`, label: room.name,
        floor: floor.index, x: room.x + room.w / 2, y: room.y + room.d / 2, kind: "room",
        roomId: room.id };
      nodes.push(node); nodeFor.set(node.id, node);
    }
    for (const stair of floor.stairs) {
      const node: NavNode = { id: `stair:${floor.index}:${stair.id}`, label: "Staircase",
        floor: floor.index, x: stair.x + stair.w / 2, y: stair.y + stair.d / 2, kind: "stair" };
      nodes.push(node); nodeFor.set(node.id, node);
      const up = data.floors.find((f) => f.index === floor.index + 1);
      const above = up?.stairs.find((s) => Math.abs(s.x - stair.x) < 0.6 && Math.abs(s.y - stair.y) < 0.6);
      if (up && above) {
        edges.push({ from: node.id, to: `stair:${up.index}:${above.id}`,
          via: { x: node.x, y: node.y, floor: up.index }, kind: "stair" });
      }
      // stair connects to the room containing it and (above) to the room above
      const host = floor.rooms.find((r) => r.id === stair.room_id)
        || floor.rooms.find((r) => contains(r, node.x, node.y));
      if (host) edges.push({ from: node.id, to: `${floor.index}:${host.id}`,
        via: { x: node.x, y: node.y, floor: floor.index }, kind: "step" });
      if (up && above) {
        const hostUp = up.rooms.find((r) => r.id === above.room_id)
          || up.rooms.find((r) => contains(r, above.x + above.w / 2, above.y + above.d / 2));
        if (hostUp) edges.push({ from: `stair:${up.index}:${above.id}`, to: `${up.index}:${hostUp.id}`,
          via: { x: above.x + above.w / 2, y: above.y + above.d / 2, floor: up.index }, kind: "step" });
      }
    }
    // door edges between rooms on this floor
    for (const op of floor.openings) {
      if (op.kind !== "door" && op.kind !== "sliding" && op.kind !== "pass_through") continue;
      const room = floor.rooms.find((r) => r.id === op.room_id);
      if (!room) continue;
      const span = openingSpan(room, op);
      const mx = span.axis === "y" ? (span.start + span.end) / 2 : span.line;
      const my = span.axis === "y" ? span.line : (span.start + span.end) / 2;
      const other = floor.rooms.find((r) => r.id !== room.id && !r.is_outdoor === !room.is_outdoor
        && touchesEdge(r, op.edge, mx, my));
      const from = `${floor.index}:${room.id}`;
      if (op.connects_to && nodeFor.has(`${floor.index}:${op.connects_to}`)) {
        edges.push({ from, to: `${floor.index}:${op.connects_to}`,
          via: { x: mx, y: my, floor: floor.index }, kind: "door" });
      } else if (other) {
        edges.push({ from, to: `${floor.index}:${other.id}`, via: { x: mx, y: my, floor: floor.index }, kind: "door" });
      } else if (roomIsOutsideFacing(data, floor, room, op)) {
        edges.push({ from: "street", to: from, via: { x: mx, y: my, floor: floor.index }, kind: "door" });
      }
    }
    // outdoor pads connect to the room they touch
    for (const room of floor.rooms) {
      if (!room.is_outdoor) continue;
      const neighbour = floor.rooms.find((r) => !r.is_outdoor && sharesEdge(r, room));
      if (neighbour) {
        edges.push({ from: `${floor.index}:${room.id}`, to: `${floor.index}:${neighbour.id}`,
          via: { x: (room.x + room.w / 2 + neighbour.x + neighbour.w / 2) / 2,
                 y: (room.y + room.d / 2 + neighbour.y + neighbour.d / 2) / 2,
                 floor: floor.index }, kind: "step" });
      }
    }
  }
  return { nodes, edges };
}

function contains(room: Room, x: number, y: number): boolean {
  return x >= room.x && x <= room.x + room.w && y >= room.y && y <= room.y + room.d;
}

function touchesEdge(room: Room, edge: string, x: number, y: number): boolean {
  const tol = 0.12;
  if (edge === "S") return Math.abs(room.y - y) < tol && x > room.x - tol && x < room.x + room.w + tol;
  if (edge === "N") return Math.abs(room.y + room.d - y) < tol && x > room.x - tol && x < room.x + room.w + tol;
  if (edge === "W") return Math.abs(room.x - x) < tol && y > room.y - tol && y < room.y + room.d + tol;
  return Math.abs(room.x + room.w - x) < tol && y > room.y - tol && y < room.y + room.d + tol;
}

function sharesEdge(a: Room, b: Room): boolean {
  const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const oy = Math.min(a.y + a.d, b.y + b.d) - Math.max(a.y, b.y);
  return (Math.abs(a.y + a.d - b.y) < 0.1 || Math.abs(b.y + b.d - a.y) < 0.1 ||
          Math.abs(a.x + a.w - b.x) < 0.1 || Math.abs(b.x + b.w - a.x) < 0.1) &&
         (ox > 0.4 || oy > 0.4);
}

function roomIsOutsideFacing(data: ProjectData, floor: Floor, room: Room, op: { edge: string }): boolean {
  if (floor.index !== 0) return false;
  const side = data.plot.road_side;
  const r = room;
  if (op.edge === side) return true;
  return r.tags?.includes("entrance") || false;
}

export function routeBetween(graph: NavGraph, fromId: string, toId: string): NavNode[] | null {
  const adj = new Map<string, { node: string; via: NavEdge }[]>();
  for (const edge of graph.edges) {
    if (!adj.has(edge.from)) adj.set(edge.from, []);
    if (!adj.has(edge.to)) adj.set(edge.to, []);
    adj.get(edge.from)!.push({ node: edge.to, via: edge });
    adj.get(edge.to)!.push({ node: edge.from, via: edge });
  }
  const prev = new Map<string, string>();
  const seen = new Set([fromId]);
  const queue = [fromId];
  while (queue.length) {
    const current = queue.shift()!;
    if (current === toId) break;
    for (const next of adj.get(current) || []) {
      if (seen.has(next.node)) continue;
      seen.add(next.node);
      prev.set(next.node, current);
      queue.push(next.node);
    }
  }
  if (!seen.has(toId)) return null;
  const path: string[] = [toId];
  let cursor = toId;
  while (cursor !== fromId) {
    cursor = prev.get(cursor)!;
    path.unshift(cursor);
  }
  return path.map((id) => graph.nodes.find((n) => n.id === id)!).filter(Boolean);
}

/** Waypoints (plan space) for walking a node path: room centre -> door -> room centre. */
export function waypointsFor(graph: NavGraph, path: NavNode[]): { x: number; y: number; floor: number }[] {
  const points: { x: number; y: number; floor: number }[] = [];
  for (let i = 0; i < path.length; i++) {
    const node = path[i];
    if (i > 0) {
      const edge = graph.edges.find((e) =>
        (e.from === path[i - 1].id && e.to === node.id) ||
        (e.to === path[i - 1].id && e.from === node.id));
      if (edge) points.push(edge.via);
    }
    points.push({ x: node.x, y: node.y, floor: node.floor });
  }
  return points;
}

export interface TourStop { nodeId: string; label: string; blurb: string }

export const TOUR_ROUTES: { key: string; label: string; pick: (g: NavGraph, d: ProjectData) => string[] }[] = [
  { key: "street_to_door", label: "Street to main entrance", pick: (g) => {
      const door = g.edges.find((e) => e.from === "street");
      return door ? ["street", door.to] : ["street"];
    } },
  { key: "entrance_living", label: "Entrance to living room", pick: (g, d) => {
      const entry = g.edges.find((e) => e.from === "street")?.to;
      const living = g.nodes.find((n) => roomType(d, n) === "living")?.id;
      return entry && living ? ["street", entry, living] : [];
    } },
  { key: "living_dining_kitchen", label: "Living · dining · kitchen", pick: (g, d) => pickTypes(g, d, ["living", "dining", "kitchen"]) },
  { key: "bedrooms", label: "Bedrooms and bathrooms", pick: (g, d) => pickTypes(g, d, ["master_bedroom", "bedroom", "bathroom"]) },
  { key: "upper_floor", label: "Staircase and upper floor", pick: (g) => {
      const stair = g.nodes.find((n) => n.kind === "stair" && n.floor === 0);
      const upper = g.nodes.filter((n) => n.floor === 1 && n.kind === "room").slice(0, 3);
      return stair ? ["street", ...([] as string[]), stair.id, ...upper.map((u) => u.id)] : [];
    } },
  { key: "full", label: "Complete property tour", pick: (g, d) => {
      const order = ["living", "dining", "kitchen", "master_bedroom", "bedroom", "bathroom", "balcony", "terrace"];
      const ids: string[] = ["street"];
      const entry = g.edges.find((e) => e.from === "street")?.to;
      if (entry) ids.push(entry);
      for (const type of order) {
        const node = g.nodes.find((n) => roomType(d, n) === type && !ids.includes(n.id));
        if (node) ids.push(node.id);
      }
      return ids;
    } },
];

function roomType(data: ProjectData, node: NavNode): string | null {
  if (!node.roomId) return null;
  const floor = data.floors.find((f) => f.index === node.floor);
  return floor?.rooms.find((r) => r.id === node.roomId)?.type || null;
}

function pickTypes(g: NavGraph, d: ProjectData, types: string[]): string[] {
  const entry = g.edges.find((e) => e.from === "street")?.to;
  const ids: string[] = entry ? ["street", entry] : ["street"];
  for (const type of types) {
    const node = g.nodes.find((n) => roomType(d, n) === type && !ids.includes(n.id));
    if (node) ids.push(node.id);
  }
  return ids;
}

/** Stitch node ids into a walkable waypoint list; returns null with a reason when
 *  any hop has no valid route (never walk through walls). */
export function planTour(graph: NavGraph, stopIds: string[]):
  { points: { x: number; y: number; floor: number }[]; stops: TourStop[] } | null {
  const points: { x: number; y: number; floor: number }[] = [];
  const stops: TourStop[] = [];
  for (let i = 0; i < stopIds.length; i++) {
    const from = stopIds[i];
    const node = graph.nodes.find((n) => n.id === from);
    if (!node) continue;
    if (i > 0) {
      const path = routeBetween(graph, stopIds[i - 1], from);
      if (!path) return null;
      const legs = waypointsFor(graph, path);
      points.push(...legs.slice(1));
    } else {
      points.push({ x: node.x, y: node.y, floor: node.floor });
    }
    stops.push({ nodeId: from, label: node.label, blurb: "" });
  }
  return points.length ? { points, stops } : null;
}

export function inaccessibleRooms(data: ProjectData, graph: NavGraph): string[] {
  const entry = graph.edges.find((e) => e.from === "street")?.to || "street";
  const out: string[] = [];
  for (const node of graph.nodes) {
    if (node.kind !== "room") continue;
    const room = data.floors.find((f) => f.index === node.floor)?.rooms.find((r) => r.id === node.roomId);
    if (!room || room.is_outdoor) continue;
    if (!routeBetween(graph, entry, node.id)) out.push(room.name);
  }
  return out;
}
