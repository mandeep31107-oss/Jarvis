/** MaterialManager: procedural PBR materials (no downloaded textures, so nothing
 *  stretches or 404s). Canvas-generated maps give wood grain, tile joints, marble
 *  veins and plaster grain at a real-world scale via texture repeat. */

import * as THREE from "three";
import type { MaterialSet } from "../types/project";

const cache = new Map<string, THREE.MeshStandardMaterial>();
const textures = new Map<string, THREE.Texture>();

function canvasTexture(key: string, size: number, draw: (ctx: CanvasRenderingContext2D, s: number) => void, repeat = 1): THREE.Texture {
  const cacheKey = `${key}:${repeat}`;
  const existing = textures.get(cacheKey);
  if (existing) return existing;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  draw(ctx, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeat, repeat);
  texture.anisotropy = 4;
  texture.colorSpace = THREE.SRGBColorSpace;
  textures.set(cacheKey, texture);
  return texture;
}

function noise(ctx: CanvasRenderingContext2D, s: number, alpha: number, scale = 1) {
  for (let i = 0; i < s * s * 0.08 * scale; i++) {
    const x = Math.random() * s, y = Math.random() * s;
    const v = Math.floor(Math.random() * 255);
    ctx.fillStyle = `rgba(${v},${v},${v},${alpha})`;
    ctx.fillRect(x, y, 1.4, 1.4);
  }
}

export function woodTexture(repeat = 2): THREE.Texture {
  return canvasTexture("wood", 256, (ctx, s) => {
    ctx.fillStyle = "#8a6a48";
    ctx.fillRect(0, 0, s, s);
    for (let i = 0; i < 26; i++) {
      ctx.strokeStyle = `rgba(${70 + Math.random() * 60},${48 + Math.random() * 40},${28 + Math.random() * 30},0.5)`;
      ctx.lineWidth = 1 + Math.random() * 2.4;
      ctx.beginPath();
      const y = Math.random() * s;
      ctx.moveTo(0, y);
      for (let x = 0; x <= s; x += 16) ctx.lineTo(x, y + Math.sin(x * 0.05 + i) * 3);
      ctx.stroke();
    }
    for (let p = 0; p < 4; p++) {
      ctx.strokeStyle = "rgba(40,28,18,0.65)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo((p + 1) * (s / 5), 0);
      ctx.lineTo((p + 1) * (s / 5), s);
      ctx.stroke();
    }
    noise(ctx, s, 0.05);
  }, repeat);
}

export function tileTexture(base: string, repeat = 3): THREE.Texture {
  return canvasTexture(`tile:${base}`, 256, (ctx, s) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, s, s);
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 3;
    for (let i = 0; i <= 2; i++) {
      ctx.beginPath(); ctx.moveTo(0, (i * s) / 2); ctx.lineTo(s, (i * s) / 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo((i * s) / 2, 0); ctx.lineTo((i * s) / 2, s); ctx.stroke();
    }
    noise(ctx, s, 0.04);
  }, repeat);
}

export function marbleTexture(repeat = 2): THREE.Texture {
  return canvasTexture("marble", 256, (ctx, s) => {
    ctx.fillStyle = "#e9e6df";
    ctx.fillRect(0, 0, s, s);
    for (let i = 0; i < 14; i++) {
      ctx.strokeStyle = `rgba(${120 + Math.random() * 40},${118 + Math.random() * 40},${112 + Math.random() * 40},${0.25 + Math.random() * 0.3})`;
      ctx.lineWidth = 0.8 + Math.random() * 2;
      ctx.beginPath();
      let x = Math.random() * s, y = Math.random() * s;
      ctx.moveTo(x, y);
      for (let k = 0; k < 6; k++) {
        x += (Math.random() - 0.5) * 90; y += (Math.random() - 0.5) * 90;
        ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    noise(ctx, s, 0.03);
  }, repeat);
}

export function plasterTexture(base: string, repeat = 2): THREE.Texture {
  return canvasTexture(`plaster:${base}`, 128, (ctx, s) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, s, s);
    noise(ctx, s, 0.05, 1.6);
  }, repeat);
}

export function concreteTexture(repeat = 2): THREE.Texture {
  return canvasTexture("concrete", 128, (ctx, s) => {
    ctx.fillStyle = "#9aa0a6";
    ctx.fillRect(0, 0, s, s);
    noise(ctx, s, 0.09, 1.4);
  }, repeat);
}

export interface MaterialRequest {
  kind: "wall_ext" | "wall_int" | "floor" | "ceiling" | "door" | "window_frame" | "glass"
  | "roof" | "stair" | "furniture" | "fixture" | "site" | "landscape" | "railing" | "slab";
  materials?: MaterialSet;
  color?: string | null;
  hint?: string | null;
}

export function materialFor(request: MaterialRequest): THREE.MeshStandardMaterial {
  const m = request.materials;
  const key = JSON.stringify(request);
  const cached = cache.get(key);
  if (cached) return cached;
  const made = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.85, metalness: 0.02 });
  const colour = (value: string | null | undefined, fallback: string) =>
    /^#[0-9a-f]{3,8}$/i.test(value || "") ? (value as string) : fallback;

  switch (request.kind) {
    case "wall_ext":
      made.map = plasterTexture(colour(m?.exterior_wall && hexOf(m.exterior_wall), "#e8e2d6"), 2);
      made.color.set(colour(hexOf(m?.exterior_wall), "#e8e2d6"));
      made.roughness = 0.9;
      break;
    case "wall_int":
      made.map = plasterTexture(colour(request.color || hexOf(m?.interior_wall), "#f2efe9"), 2);
      made.color.set(colour(request.color || hexOf(m?.interior_wall), "#f2efe9"));
      made.roughness = 0.92;
      break;
    case "floor": {
      const finish = m?.floor_material || "tile";
      if (finish === "wood") { made.map = woodTexture(3); made.color.set("#b08b60"); made.roughness = 0.55; }
      else if (finish === "marble" || finish === "granite") { made.map = marbleTexture(2); made.color.set(colour(hexOf(m?.floor_color), "#e7e3da")); made.roughness = 0.25; }
      else if (finish === "concrete" || finish === "kota_stone") { made.map = concreteTexture(3); made.color.set(colour(hexOf(m?.floor_color), "#a7a49c")); made.roughness = 0.8; }
      else { made.map = tileTexture(colour(hexOf(m?.floor_color), "#d8d2c6"), 4); made.color.set(colour(hexOf(m?.floor_color), "#d8d2c6")); made.roughness = 0.35; }
      break;
    }
    case "ceiling":
      made.color.set("#fbfaf7"); made.roughness = 0.95;
      break;
    case "door":
      made.map = woodTexture(1); made.color.set(m?.door_material === "metal" ? "#6b7076" : m?.door_material === "glass" ? "#9fc4d8" : "#7a5636");
      made.roughness = m?.door_material === "glass" ? 0.15 : 0.5;
      break;
    case "window_frame":
      made.color.set(m?.window_frame === "wood" ? "#6d4f30" : m?.window_frame === "upvc" ? "#f4f4f2" : "#4c545c");
      made.metalness = m?.window_frame === "aluminium" ? 0.6 : 0.1; made.roughness = 0.4;
      break;
    case "glass":
      made.color.set("#bcd8e6"); made.transparent = true; made.opacity = 0.28;
      made.roughness = 0.08; made.metalness = 0.1; made.side = THREE.DoubleSide;
      break;
    case "roof":
      made.color.set(colour(hexOf(m?.roof_color), "#8c4a3c")); made.roughness = 0.8;
      break;
    case "stair":
      made.map = m?.floor_material === "wood" ? woodTexture(2) : concreteTexture(2);
      made.color.set("#b9b2a6"); made.roughness = 0.7;
      break;
    case "furniture":
      made.color.set(colour(request.color || hexOf(request.hint), "#8a6f52"));
      made.map = /wood|teak|oak|walnut/i.test(request.hint || "") ? woodTexture(1) : null;
      made.roughness = /fabric|linen|velvet|cushion/i.test(request.hint || "") ? 0.95 : 0.6;
      break;
    case "fixture":
      made.color.set("#f2f5f7"); made.roughness = 0.18; made.metalness = 0.05;
      break;
    case "site":
      made.color.set("#8d9188"); made.map = concreteTexture(4); made.roughness = 0.95;
      break;
    case "landscape":
      made.color.set("#6f8f5a"); made.roughness = 1;
      break;
    case "railing":
      made.color.set("#3d434a"); made.metalness = 0.7; made.roughness = 0.35;
      break;
    default:
      made.color.set("#b5b1a8"); made.roughness = 0.9;
  }
  cache.set(key, made);
  return made;
}

/** Map free-text material names to a hex where we know one; else null. */
export function hexOf(name?: string | null): string | null {
  if (!name) return null;
  const table: Record<string, string> = {
    white: "#f5f3ee", cream: "#efe8d8", ivory: "#f2ead9", beige: "#d9cbb2", grey: "#9aa0a6",
    gray: "#9aa0a6", charcoal: "#4a4f55", black: "#23262a", brown: "#7a5636", walnut: "#5d4327",
    teak: "#8a6a48", oak: "#b08b60", terracotta: "#b3603f", brick: "#9c4f34", red: "#a33b2e",
    green: "#5f7f4f", sage: "#9caf88", blue: "#3f6fa8", navy: "#25355c", teal: "#2f6f6a",
    yellow: "#d9b23a", mustard: "#c19a2e", pink: "#d8a1a1", peach: "#e8b48d", marble: "#e9e6df",
    granite: "#8d8a84", kota: "#a7a49c", concrete: "#9aa0a6", sand: "#d8c9a8", olive: "#6b6b3f",
  };
  return table[name.trim().toLowerCase()] || null;
}

export function disposeMaterials() {
  cache.forEach((m) => m.dispose());
  cache.clear();
  textures.forEach((t) => t.dispose());
  textures.clear();
}
