/** First-person character controller: pointer-lock look, WASD/arrow walk with
 *  acceleration, collision + wall slide against the real wall solids, stair ramp
 *  traversal, head bob, footstep audio, centre-ray interaction prompts and the
 *  guided-tour driver. Never teleports through walls. */

import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { ProjectData } from "../types/project";
import { buildNavGraph, planTour, routeBetween, waypointsFor, TOUR_ROUTES } from "../lib/model/navgraph";
import { movePlayer, spawnPoint, streetSpawn, PLAYER_RADIUS } from "../lib/model/collision";
import { buildScene, EYE_HEIGHT, groundAt } from "../lib/model/scene";
import { useStore } from "../lib/store";

export interface HudState {
  prompt: string | null;
  position: { x: number; y: number; z: number; floor: number };
  yaw: number;
  walking: boolean;
  tourStop: string | null;
}

export function FirstPerson({ data, onHud }: { data: ProjectData; onHud: (hud: HudState) => void }) {
  const { camera, gl } = useThree();
  const walk = useStore((s) => s.walk);
  const quality = useStore((s) => s.quality);
  const tour = useStore((s) => s.tour);
  const set = useStore((s) => s.set);
  const toast = useStore((s) => s.toast);

  const world = useMemo(() => ({
    model: buildScene(data),
    data,
    doorStates: useStore.getState().doorStates,
    furnitureBlocks: true,
  }), [data]);
  const graph = useMemo(() => buildNavGraph(data), [data]);

  const state = useRef({
    pos: streetSpawn(data),
    vel: new THREE.Vector2(0, 0),
    yaw: 0, pitch: 0,
    keys: new Set<string>(),
    locked: false,
    bobPhase: 0,
    stepCycle: 0,
    velocity: new THREE.Vector3(),
    tourPoints: null as null | { x: number; y: number; floor: number }[],
    tourIndex: 0,
    tourStop: null as string | null,
    prompt: null as string | null,
    target: null as null | { kind: "door" | "window"; id: string; label: string },
    lastHud: 0,
  });

  // keep door states live without rebuilding the world
  useEffect(() => {
    const unsubscribe = useStore.subscribe((s, prev) => {
      if (s.doorStates !== prev.doorStates) world.doorStates = s.doorStates;
    });
    return unsubscribe;
  }, [world]);

  // guided tour route
  useEffect(() => {
    const s = state.current;
    if (!tour.active) { s.tourPoints = null; return; }
    const route = TOUR_ROUTES.find((r) => r.key === tour.routeKey) || TOUR_ROUTES[TOUR_ROUTES.length - 1];
    const stopIds = route.pick(graph, data);
    const planned = planTour(graph, stopIds);
    if (!planned) {
      toast("warn", "No walkable route for that tour with the current plan. Fix blocked doors or disconnected rooms first.");
      set({ tour: { ...tour, active: false } });
      return;
    }
    s.tourPoints = planned.points;
    s.tourIndex = 0;
    const first = planned.points[0];
    s.pos = { x: first.x, y: first.y, z: first.floor * 3, floor: first.floor };
  }, [tour.active, tour.routeKey, graph, data]);

  // pointer lock + keyboard
  useEffect(() => {
    const dom = gl.domElement;
    const s = state.current;
    const onClick = () => {
      if (!s.locked && dom.requestPointerLock) dom.requestPointerLock();
    };
    const onLockChange = () => { s.locked = document.pointerLockElement === dom; };
    const onMouseMove = (event: MouseEvent) => {
      if (!s.locked) return;
      const sens = 0.0022 * walk.sensitivity;
      s.yaw -= event.movementX * sens;
      s.pitch -= event.movementY * sens * (walk.invertLook ? -1 : 1);
      s.pitch = Math.max(-1.45, Math.min(1.45, s.pitch));
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code === "KeyE") { interact(); return; }
      s.keys.add(event.code);
      if (tour.active && ["KeyW", "KeyA", "KeyS", "KeyD"].includes(event.code)) {
        set({ tour: { ...useStore.getState().tour, active: false } });
        toast("info", "Tour paused — you have manual control.");
      }
    };
    const onKeyUp = (event: KeyboardEvent) => s.keys.delete(event.code);
    dom.addEventListener("click", onClick);
    document.addEventListener("pointerlockchange", onLockChange);
    document.addEventListener("mousemove", onMouseMove);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      dom.removeEventListener("click", onClick);
      document.removeEventListener("pointerlockchange", onLockChange);
      document.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [gl, walk.sensitivity, walk.invertLook, tour.active]);

  const raycaster = useMemo(() => new THREE.Raycaster(), []);

  const interact = () => {
    const s = state.current;
    if (!s.target) return;
    if (s.target.kind === "door") {
      const open = useStore.getState().doorStates[s.target.id] || 0;
      useStore.getState().setDoor(s.target.id, open > 0.5 ? 0 : 1);
      footstep(true);
    } else {
      const open = useStore.getState().windowStates[s.target.id] || 0;
      useStore.getState().setWindow(s.target.id, open > 0.5 ? 0 : 1);
    }
  };

  // ---- audio (opt-in, synthesised, never required) ----
  const audio = useRef<{ ctx: AudioContext | null; ambient: AudioBufferSourceNode | null; indoors: boolean }>({ ctx: null, ambient: null, indoors: false });
  const footstep = (force = false) => {
    if (!walk.audio && !force) return;
    if (!walk.audio) return;
    const ctx = audio.current.ctx || (audio.current.ctx = new AudioContext());
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 0.09, ctx.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < channel.length; i++) {
      channel[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / channel.length, 2.2);
    }
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 420 + Math.random() * 220;
    const gain = ctx.createGain();
    gain.gain.value = 0.16;
    source.connect(filter).connect(gain).connect(ctx.destination);
    source.start();
  };

  useFrame((frameState, delta) => {
    const s = state.current;
    const dt = Math.min(delta, 0.05);
    const eye = walk.eyeHeight || EYE_HEIGHT;

    // ---------- guided tour driving ----------
    if (tour.active && s.tourPoints && !tour.paused) {
      const points = s.tourPoints;
      const target = points[Math.min(s.tourIndex, points.length - 1)];
      const dx = target.x - s.pos.x, dy = target.y - s.pos.y;
      const dist = Math.hypot(dx, dy);
      const speed = 1.15 * tour.speed;
      if (dist < 0.35) {
        s.tourIndex += 1;
        const node = graph.nodes.find((n) => Math.abs(n.x - target.x) < 0.6 && Math.abs(n.y - target.y) < 0.6 && n.floor === target.floor);
        s.tourStop = node?.label || null;
        if (s.tourIndex >= points.length) {
          set({ tour: { ...useStore.getState().tour, active: false } });
          toast("success", "Tour complete.");
        }
      } else {
        const step = Math.min(dist, speed * dt);
        const moved = movePlayer(world, { ...s.pos, z: s.pos.z }, (dx / dist) * step, (dy / dist) * step);
        // tours follow walkable waypoints; if collision blocks (a door was closed) open it
        if (Math.hypot(moved.x - s.pos.x, moved.y - s.pos.y) < step * 0.25) {
          const door = world.model.doors.find((d) => d.floorIndex === s.pos.floor &&
            Math.hypot(d.hx - target.x, d.hy - target.y) < 1.6);
          if (door && !(useStore.getState().doorStates[door.id] > 0.5)) {
            useStore.getState().setDoor(door.id, 1);
          }
        }
        s.pos = moved;
        s.yaw = Math.atan2(-(dx), -(dy)) + Math.PI;
        s.pitch = -0.05;
      }
    } else {
      // ---------- manual movement ----------
      const forward = (s.keys.has("KeyW") || s.keys.has("ArrowUp") ? 1 : 0) -
        (s.keys.has("KeyS") || s.keys.has("ArrowDown") ? 1 : 0);
      const strafe = (s.keys.has("KeyD") || s.keys.has("ArrowRight") ? 1 : 0) -
        (s.keys.has("KeyA") || s.keys.has("ArrowLeft") ? 1 : 0);
      const sprint = s.keys.has("ShiftLeft") && walk.mode === "free";
      const targetSpeed = forward || strafe ? (sprint ? walk.sprint : walk.speed) * (walk.mode === "free" ? 1.7 : 1) : 0;
      const dirX = (-Math.sin(s.yaw) * forward + Math.cos(s.yaw) * strafe);
      const dirY = (-Math.cos(s.yaw) * forward - Math.sin(s.yaw) * strafe);
      const len = Math.hypot(dirX, dirY) || 1;
      const accel = walk.mode === "realistic" ? 8 : 14;
      s.vel.x += ((dirX / len) * targetSpeed - s.vel.x) * Math.min(1, accel * dt);
      s.vel.y += ((dirY / len) * targetSpeed - s.vel.y) * Math.min(1, accel * dt);
      if (Math.hypot(s.vel.x, s.vel.y) > 0.001) {
        const moved = movePlayer(world, s.pos, s.vel.x * dt, s.vel.y * dt);
        s.pos = moved;
      }
      const speedNow = Math.hypot(s.vel.x, s.vel.y);
      if (speedNow > 0.25 && walk.bob) {
        s.bobPhase += dt * speedNow * 5.2;
        s.stepCycle += dt * speedNow;
        if (s.stepCycle > 0.72) { s.stepCycle = 0; footstep(); }
      }
    }

    // ---------- camera ----------
    const bobAmount = walk.bob && !tour.active ? Math.sin(s.bobPhase) * 0.022 * Math.min(1, Math.hypot(s.vel.x, s.vel.y)) : 0;
    camera.position.set(s.pos.x, s.pos.z + eye + bobAmount, -s.pos.y);
    camera.rotation.order = "YXZ";
    camera.rotation.y = s.yaw;
    camera.rotation.x = s.pitch;
    camera.near = 0.05;
    camera.far = 400;
    camera.updateProjectionMatrix();

    // ---------- centre-ray interaction ----------
    raycaster.setFromCamera(new THREE.Vector2(0, 0), camera);
    raycaster.far = 2.6;
    const hits = raycaster.intersectObjects(frameState.scene.children, true);
    let prompt: string | null = null;
    s.target = null;
    for (const hit of hits) {
      let obj: THREE.Object3D | null = hit.object;
      while (obj && !obj.userData?.pickId) obj = obj.parent;
      if (!obj) continue;
      if (obj.userData.door) {
        const open = useStore.getState().doorStates[obj.userData.pickId] || 0;
        prompt = open > 0.5 ? `Close door — ${obj.userData.label}` : `Open door — ${obj.userData.label}`;
        s.target = { kind: "door", id: obj.userData.pickId, label: obj.userData.label };
      } else if (obj.userData.window) {
        const open = useStore.getState().windowStates[obj.userData.pickId] || 0;
        prompt = open > 0.5 ? `Close window — ${obj.userData.label}` : `Open window — ${obj.userData.label}`;
        s.target = { kind: "window", id: obj.userData.pickId, label: obj.userData.label };
      } else if (obj.userData.label) {
        prompt = `Inspect — ${obj.userData.label}`;
        s.target = null;
      }
      break;
    }
    s.prompt = prompt;

    // ---------- HUD (throttled) ----------
    const now = performance.now();
    if (now - s.lastHud > 90) {
      s.lastHud = now;
      onHud({ prompt, position: { ...s.pos }, yaw: s.yaw,
        walking: Math.hypot(s.vel.x, s.vel.y) > 0.2, tourStop: s.tourStop });
    }
  });

  // public helpers attached to window for the HUD buttons
  useEffect(() => {
    (window as unknown as Record<string, unknown>).__jarvisWalk = {
      reset: () => { state.current.pos = streetSpawn(data); state.current.vel.set(0, 0); },
      enter: () => { state.current.pos = spawnPoint(data); },
      goToRoom: (roomId: string) => {
        const node = graph.nodes.find((n) => n.roomId === roomId);
        if (!node) return false;
        const here = graph.nodes.find((n) => Math.abs(n.x - state.current.pos.x) < 1 &&
          Math.abs(n.y - state.current.pos.y) < 1 && n.floor === state.current.pos.floor);
        const from = here?.id || "street";
        const path = routeBetween(graph, from, node.id);
        if (!path) return false;
        const points = waypointsFor(graph, path);
        state.current.tourPoints = points;
        state.current.tourIndex = 0;
        set({ tour: { ...useStore.getState().tour, active: true, paused: false, routeKey: "walk_to" } });
        return true;
      },
      state: state.current,
    };
    return () => { delete (window as unknown as Record<string, unknown>).__jarvisWalk; };
  }, [graph, data]);

  return null;
}

export function walkApi(): {
  reset: () => void; enter: () => void; goToRoom: (roomId: string) => boolean;
  state: { pos: { x: number; y: number; z: number; floor: number }; yaw: number };
} | null {
  return ((window as unknown as Record<string, unknown>).__jarvisWalk as never) || null;
}
