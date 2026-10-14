/** Canvas + camera mode manager. Orbit modes share one camera rig; walk mode
 *  hands the camera to the FirstPerson controller. */

import { Suspense, useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { ProjectData } from "../types/project";
import { HouseModel } from "./HouseModel";
import { FirstPerson, type HudState } from "./FirstPerson";
import { LightingRig } from "./Lighting";
import { useStore } from "../lib/store";
import { FLOOR_HEIGHT } from "../lib/model/scene";

function CameraRig({ data }: { data: ProjectData }) {
  const viewMode = useStore((s) => s.viewMode);
  const activeFloor = useStore((s) => s.activeFloor);
  const focusRoomId = useStore((s) => s.focusRoomId);
  const { camera } = useThree();
  const applied = useRef("");

  useFrame(() => {
    const key = `${viewMode}|${activeFloor}|${focusRoomId}`;
    if (viewMode === "walk") return;
    if (applied.current === key) return;
    applied.current = key;
    const { width, depth } = data.plot;
    const cx = width / 2, cz = -depth / 2;
    const level = activeFloor * FLOOR_HEIGHT;
    const target = new THREE.Vector3(cx, level + 1.2, cz);
    if (focusRoomId) {
      for (const floor of data.floors) {
        const room = floor.rooms.find((r) => r.id === focusRoomId);
        if (room) target.set(room.x + room.w / 2, floor.index * FLOOR_HEIGHT + 1.2,
          -(room.y + room.d / 2));
      }
    }
    let pos = new THREE.Vector3(cx + width, level + width * 0.8, cz + depth);
    if (viewMode === "top") pos = new THREE.Vector3(cx, level + Math.max(width, depth) * 1.6, cz + 0.01);
    if (viewMode === "dollhouse") pos = new THREE.Vector3(cx + width * 0.9, level + Math.max(width, depth) * 1.1, cz + depth * 0.9);
    if (viewMode === "room") pos = new THREE.Vector3(target.x + 3.2, level + 2.1, target.z + 3.2);
    if (viewMode === "elev_front") {
      const side = data.plot.road_side;
      const d = Math.max(width, depth) * 1.4;
      pos = side === "S" ? new THREE.Vector3(cx, level + 2, cz + depth / 2 + d)
        : side === "N" ? new THREE.Vector3(cx, level + 2, cz - depth / 2 - d)
        : side === "W" ? new THREE.Vector3(cx - width / 2 - d, level + 2, cz)
        : new THREE.Vector3(cx + width / 2 + d, level + 2, cz);
    }
    if (viewMode === "elev_side") pos = new THREE.Vector3(cx + width / 2 + Math.max(width, depth) * 1.4, level + 2, cz);
    if (viewMode === "elev_rear") {
      const side = data.plot.road_side;
      const d = Math.max(width, depth) * 1.4;
      pos = side === "S" ? new THREE.Vector3(cx, level + 2, cz - depth / 2 - d)
        : side === "N" ? new THREE.Vector3(cx, level + 2, cz + depth / 2 + d)
        : side === "W" ? new THREE.Vector3(cx + width / 2 + d, level + 2, cz)
        : new THREE.Vector3(cx - width / 2 - d, level + 2, cz);
    }
    camera.position.copy(pos);
    camera.lookAt(target);
    camera.near = 0.1; camera.far = 600;
    camera.updateProjectionMatrix();
  });
  return null;
}

export function SceneRoot({ data, onHud, interactive = true }:
  { data: ProjectData; onHud?: (hud: HudState) => void; interactive?: boolean }) {
  const viewMode = useStore((s) => s.viewMode);
  const quality = useStore((s) => s.quality);
  const visibility = useStore((s) => s.visibility);
  const controls = useRef<React.ComponentRef<typeof OrbitControls>>(null);

  useEffect(() => {
    if (viewMode === "walk" && document.pointerLockElement) document.exitPointerLock();
  }, [viewMode]);

  const dpr = useMemo(() => {
    switch (quality.preset) {
      case "high": return 2;
      case "balanced": return 1.5;
      case "performance": return 1;
      default: return 0.75;
    }
  }, [quality.preset]);

  return (
    <Canvas
      shadows={quality.shadows}
      dpr={dpr}
      gl={{ antialias: quality.preset !== "mobile", powerPreference: "high-performance" }}
      camera={{ fov: viewMode === "walk" ? 72 : 50, position: [20, 14, 20], near: 0.05, far: 600 }}
      onCreated={({ gl }) => { gl.toneMapping = THREE.ACESFilmicToneMapping; }}
    >
      <Suspense fallback={null}>
        <LightingRig data={data} />
        <HouseModel data={data} interactive={interactive} />
        {viewMode === "walk" ? (
          <FirstPerson data={data} onHud={onHud || (() => undefined)} />
        ) : (
          <>
            <CameraRig data={data} />
            <OrbitControls
              ref={controls}
              enableDamping
              dampingFactor={0.08}
              maxPolarAngle={visibility.cutaway ? Math.PI / 2.05 : Math.PI / 1.9}
              minDistance={1.2}
              maxDistance={140}
              target={[data.plot.width / 2, 1.2, -data.plot.depth / 2]}
            />
          </>
        )}
      </Suspense>
    </Canvas>
  );
}
