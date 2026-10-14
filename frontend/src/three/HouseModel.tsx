/** Renders the shared scene model as real Three.js geometry: slabs, walls split
 *  around openings, hinged doors, windows with glass, stair treads, roofs,
 *  furniture, site. Every mesh is addressable for selection and interaction. */

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { ProjectData } from "../types/project";
import { buildScene, FLOOR_HEIGHT, type DoorPanel, type SceneModel, type WindowUnit } from "../lib/model/scene";
import { materialFor } from "../lib/materials3d";
import { useStore } from "../lib/store";

const PLAN_TO_THREE = (x: number, y: number, z: number): [number, number, number] => [x, z, -y];

export function HouseModel({ data, interactive }: { data: ProjectData; interactive: boolean }) {
  const model = useMemo(() => buildScene(data), [data]);
  const visibility = useStore((s) => s.visibility);
  const viewMode = useStore((s) => s.viewMode);
  const activeFloor = useStore((s) => s.activeFloor);
  const focusRoomId = useStore((s) => s.focusRoomId);
  const selectedObjectId = useStore((s) => s.selectedObjectId);
  const set = useStore((s) => s.set);
  const quality = useStore((s) => s.quality);

  const explode = visibility.exploded ? 2.6 : 0;
  const dollhouse = viewMode === "dollhouse" || viewMode === "top";
  const singleFloor = viewMode === "room" ? activeFloor : null;

  const showPiece = (group: string, floorIndex: number, roomId?: string): boolean => {
    if (singleFloor !== null && floorIndex !== singleFloor && group !== "site" && group !== "landscape") return false;
    if (viewMode === "room" && focusRoomId && roomId && roomId !== focusRoomId &&
        (group === "furniture" || group === "fixture" || group === "floor_finish")) return false;
    switch (group) {
      case "roof": return visibility.roof && !dollhouse;
      case "slab": return visibility.floors;
      case "floor_finish": return visibility.floors;
      case "exterior_wall":
      case "interior_wall":
        if (!visibility.walls) return false;
        if (dollhouse && group === "exterior_wall") return false;
        if (dollhouse && floorIndex === activeFloor && viewMode === "top") return false;
        return true;
      case "furniture":
      case "fixture":
        return visibility.furniture;
      default:
        return true;
    }
  };

  const pick = (event: { stopPropagation: () => void; object: THREE.Object3D; point: THREE.Vector3 }) => {
    if (!interactive) return;
    event.stopPropagation();
    if (useStore.getState().measureMode) {
      const points = [...useStore.getState().measurePoints,
        { x: event.point.x, y: event.point.y, z: event.point.z }].slice(-2);
      set({ measurePoints: points });
      return;
    }
    const id = event.object.userData.pickId as string | undefined;
    const roomId = event.object.userData.roomId as string | undefined;
    if (id) set({ selectedObjectId: id, selectedRoomId: roomId || useStore.getState().selectedRoomId });
    else if (roomId) set({ selectedRoomId: roomId, selectedObjectId: null });
  };

  return (
    <group>
      {model.pieces.map((piece) => {
        if (!showPiece(piece.group, piece.floorIndex, piece.roomId)) return null;
        const yOffset = piece.floorIndex * explode;
        const transparent = visibility.transparentWalls &&
          (piece.group === "exterior_wall" || piece.group === "interior_wall");
        const cut = visibility.cutaway && piece.group === "exterior_wall" &&
          piece.floorIndex === activeFloor;
        const material = materialFor({
          kind: groupKind(piece.group), materials: data.materials,
          color: piece.group === "interior_wall" ? roomWallColor(data, piece.roomId) : undefined,
          hint: piece.materialHint,
        });
        const mat = transparent || cut
          ? material.clone()
          : material;
        if (transparent || cut) {
          mat.transparent = true;
          mat.opacity = cut ? 0.12 : 0.35;
          mat.depthWrite = false;
        }
        const selected = selectedObjectId === piece.id || selectedObjectId === piece.roomId;
        return (
          <mesh
            key={piece.id}
            position={PLAN_TO_THREE(piece.cx, piece.cy, (piece.z0 + piece.z1) / 2 + yOffset)}
            castShadow={quality.shadows && piece.group !== "site" && piece.group !== "landscape"}
            receiveShadow={quality.shadows}
            userData={{ pickId: piece.id, roomId: piece.roomId, label: piece.label }}
            onPointerDown={pick}
          >
            <boxGeometry args={[piece.sx, Math.max(0.02, piece.z1 - piece.z0), piece.sz]} />
            <primitive object={mat} attach="material" />
            {selected ? (
              <meshBasicMaterial attach="material" color="#3f7fd6" wireframe transparent opacity={0.5} />
            ) : null}
          </mesh>
        );
      })}
      {model.doors.map((door) => (
        <DoorMesh key={door.id} door={door} model={model} explode={explode}
          visible={showPiece("door", door.floorIndex)} />
      ))}
      {model.windows.map((win) => (
        <WindowMesh key={win.id} win={win} explode={explode}
          visible={showPiece("window", win.floorIndex)} />
      ))}
    </group>
  );
}

function groupKind(group: string): Parameters<typeof materialFor>[0]["kind"] {
  switch (group) {
    case "exterior_wall": return "wall_ext";
    case "interior_wall": return "wall_int";
    case "floor_finish": return "floor";
    case "door": return "door";
    case "window": return "window_frame";
    case "glass": return "glass";
    case "roof": return "roof";
    case "stair": return "stair";
    case "furniture": return "furniture";
    case "fixture": return "fixture";
    case "site": return "site";
    case "landscape": return "landscape";
    case "railing": return "railing";
    default: return "slab";
  }
}

function roomWallColor(data: ProjectData, roomId?: string): string | null {
  if (!roomId) return null;
  for (const floor of data.floors) {
    const room = floor.rooms.find((r) => r.id === roomId);
    if (room?.wall_color) return room.wall_color;
  }
  return null;
}

function DoorMesh({ door, explode, visible }: { door: DoorPanel; model: SceneModel; explode: number; visible: boolean }) {
  const open = useStore((s) => s.doorStates[door.id] || 0);
  const setDoor = useStore((s) => s.setDoor);
  const selected = useStore((s) => s.selectedObjectId === door.id);
  const set = useStore((s) => s.set);
  const current = useRef(0);
  const group = useRef<THREE.Group>(null);
  useFrame((_, delta) => {
    current.current += (open - current.current) * Math.min(1, delta * 6);
    if (group.current) {
      // hinge rotation about the vertical axis; sign depends on wall axis
      const angle = current.current * (Math.PI / 2) * 0.92 * (door.axis === "y" ? 1 : -1);
      group.current.rotation.y = angle;
    }
  });
  const thickness = 0.05;
  const y = door.floorIndex * explode;
  const material = materialFor({ kind: "door" });
  return (
    <group
      position={PLAN_TO_THREE(door.hx, door.hy, y)}
      ref={group}
      userData={{ pickId: door.id, label: door.label, door: true }}
      onPointerDown={(e) => { e.stopPropagation(); set({ selectedObjectId: door.id }); }}
      onDoubleClick={() => setDoor(door.id, open > 0.5 ? 0 : 1)}
      visible={visible}
    >
      <mesh position={PLAN_TO_THREE(door.dx * door.width / 2, door.dy * door.width / 2, door.height / 2)}
        castShadow>
        <boxGeometry args={[door.axis === "y" ? door.width : thickness, door.height,
          door.axis === "y" ? thickness : door.width]} />
        <primitive object={selected ? material.clone() : material} attach="material" />
      </mesh>
      {/* handle */}
      <mesh position={PLAN_TO_THREE(door.dx * door.width * 0.85, door.dy * door.width * 0.85, 1.05)}>
        <sphereGeometry args={[0.035, 10, 10]} />
        <meshStandardMaterial color="#c9a227" metalness={0.8} roughness={0.3} />
      </mesh>
    </group>
  );
}

function WindowMesh({ win, explode, visible }: { win: WindowUnit; explode: number; visible: boolean }) {
  const open = useStore((s) => s.windowStates[win.id] || 0);
  const setWindow = useStore((s) => s.setWindow);
  const selected = useStore((s) => s.selectedObjectId === win.id);
  const set = useStore((s) => s.set);
  const y = win.floorIndex * explode;
  const frame = materialFor({ kind: "window_frame" });
  const glass = materialFor({ kind: "glass" });
  const slide = win.kind === "sliding" ? open * win.width * 0.45 : 0;
  const casement = win.kind === "window" ? open * 0.6 : 0;
  const t = 0.06;
  const along = win.axis === "y" ? [win.width, 0.08, t] as const : [t, 0.08, win.width] as const;
  return (
    <group visible={visible}
      userData={{ pickId: win.id, label: win.label, window: true }}
      onPointerDown={(e) => { e.stopPropagation(); set({ selectedObjectId: win.id }); }}
      onDoubleClick={() => setWindow(win.id, open > 0.5 ? 0 : 1)}>
      {/* sill + head */}
      <mesh position={PLAN_TO_THREE(win.cx, win.cy, y + win.sill - 0.04)}>
        <boxGeometry args={[along[0] + 0.1, 0.08, along[2] + 0.1]} />
        <primitive object={frame} attach="material" />
      </mesh>
      <mesh position={PLAN_TO_THREE(win.cx, win.cy, y + win.sill + win.height + 0.04)}>
        <boxGeometry args={[along[0] + 0.1, 0.08, along[2] + 0.1]} />
        <primitive object={frame} attach="material" />
      </mesh>
      {/* glass leaf (slides or swings) */}
      <group position={PLAN_TO_THREE(win.cx + (win.axis === "y" ? slide : 0), win.cy + (win.axis === "x" ? slide : 0), y + win.sill + win.height / 2)}
        rotation={[0, casement * (win.axis === "y" ? 1 : -1), 0]}>
        <mesh castShadow={false}>
          <boxGeometry args={[win.axis === "y" ? win.width : 0.03, win.height, win.axis === "y" ? 0.03 : win.width]} />
          <primitive object={selected ? glass.clone() : glass} attach="material" />
        </mesh>
        <mesh>
          <boxGeometry args={[win.axis === "y" ? win.width : 0.05, 0.05, win.axis === "y" ? 0.05 : win.width]} />
          <primitive object={frame} attach="material" />
        </mesh>
      </group>
    </group>
  );
}

export { FLOOR_HEIGHT };
