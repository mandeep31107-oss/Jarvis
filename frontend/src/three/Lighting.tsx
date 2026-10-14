/** InteriorLightingSystem: sun + sky + interior fixtures with day/evening/night
 *  presets, colour temperature and exposure control. */

import { useMemo } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { ProjectData } from "../types/project";
import { FLOOR_HEIGHT } from "../lib/model/scene";
import { useStore } from "../lib/store";

const PRESETS = {
  day: { sun: 2.6, sunColor: "#fff4e0", ambient: 0.55, sky: "#bcd6ee", ground: "#8d8a7e", interior: 0 },
  evening: { sun: 1.1, sunColor: "#ffb066", ambient: 0.3, sky: "#5c6b8f", ground: "#4c463c", interior: 0.7 },
  night: { sun: 0.12, sunColor: "#8fa8d8", ambient: 0.12, sky: "#101826", ground: "#141414", interior: 1 },
} as const;

const TEMPS = { warm: "#ffd9a8", neutral: "#fff2e0", cool: "#e8f2ff" } as const;

export function LightingRig({ data }: { data: ProjectData }) {
  const quality = useStore((s) => s.quality);
  const sun = useMemo(() => new THREE.DirectionalLight("#ffffff", 2), []);
  const { gl, scene } = useThree();

  useFrame(() => {
    const preset = PRESETS[quality.lighting];
    const angle = (quality.sunAngle * Math.PI) / 180;
    sun.position.set(Math.cos(angle) * 30, 26 + (quality.lighting === "night" ? -8 : 0),
      Math.sin(angle) * 30);
    sun.color.set(preset.sunColor);
    sun.intensity = preset.sun;
    sun.castShadow = quality.shadows;
    sun.shadow.mapSize.set(quality.preset === "high" ? 2048 : 1024,
      quality.preset === "high" ? 2048 : 1024);
    sun.shadow.camera.left = -30; sun.shadow.camera.right = 30;
    sun.shadow.camera.top = 30; sun.shadow.camera.bottom = -30;
    sun.shadow.bias = -0.0004;
    if (!sun.parent) scene.add(sun);
    gl.toneMapping = THREE.ACESFilmicToneMapping;
    gl.toneMappingExposure = quality.exposure;
    scene.background = new THREE.Color(preset.sky);
    scene.fog = new THREE.Fog(preset.sky, 60, 220);
  });

  const ambient = PRESETS[quality.lighting].ambient;
  const interiorLevel = PRESETS[quality.lighting].interior;
  const temp = TEMPS[quality.temperature];

  // one ceiling fixture per indoor room (capped for performance)
  const fixtures = useMemo(() => {
    const out: { x: number; y: number; z: number; floor: number }[] = [];
    for (const floor of data.floors) {
      for (const room of floor.rooms) {
        if (room.is_outdoor || room.type === "open_to_below") continue;
        if (out.length > 26) break;
        out.push({ x: room.x + room.w / 2, y: room.y + room.d / 2,
          z: floor.index * FLOOR_HEIGHT + (room.ceiling_height || 2.8) - 0.15, floor: floor.index });
      }
    }
    return out;
  }, [data]);

  return (
    <>
      <hemisphereLight args={[PRESETS[quality.lighting].sky, PRESETS[quality.lighting].ground, ambient]} />
      <ambientLight intensity={ambient * 0.5} color={temp} />
      {interiorLevel > 0 && fixtures.map((f, i) => (
        <pointLight key={i} position={[f.x, f.z, -f.y]} intensity={interiorLevel * 7}
          distance={7} decay={1.8} color={temp} />
      ))}
      {fixtures.slice(0, quality.preset === "high" ? 40 : 0).map((f, i) => (
        <mesh key={`bulb${i}`} position={[f.x, f.z, -f.y]}>
          <sphereGeometry args={[0.06, 8, 8]} />
          <meshBasicMaterial color={interiorLevel > 0 ? temp : "#d8d8d2"} />
        </mesh>
      ))}
    </>
  );
}
