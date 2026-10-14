/** Live mini-map from the real 2D plan: rooms, doors, player arrow + view cone,
 *  tour route dots. Click a room to focus / walk to it. */

import { useMemo } from "react";
import type { ProjectData } from "../types/project";
import { useStore } from "../lib/store";
import type { HudState } from "../three/FirstPerson";

export function MiniMap({ data, hud, size = 190 }:
  { data: ProjectData; hud: HudState | null; size?: number }) {
  const activeFloor = useStore((s) => s.activeFloor);
  const set = useStore((s) => s.set);
  const floor = data.floors.find((f) => f.index === (hud?.position.floor ?? activeFloor)) || data.floors[0];
  const scale = size / Math.max(data.plot.width, data.plot.depth);
  const rooms = useMemo(() => floor?.rooms.filter((r) => r.type !== "open_to_below") || [], [floor]);

  const px = (x: number) => x * scale;
  const py = (y: number) => size - y * scale;

  return (
    <div className="rounded-lg border border-ink-600 bg-ink-800/85 p-2 shadow-panel backdrop-blur">
      <div className="mb-1 flex items-center justify-between text-[11px] text-slate-300">
        <span>{floor?.name}</span>
        <span className="text-slate-400">{data.plot.width.toFixed(1)} × {data.plot.depth.toFixed(1)} m</span>
      </div>
      <svg width={size} height={size} className="rounded bg-ink-900/80">
        <rect x={0} y={0} width={size} height={size} fill="#0d141b" />
        {rooms.map((room) => (
          <g key={room.id} className="cursor-pointer"
            onClick={() => set({ selectedRoomId: room.id, focusRoomId: room.id, activeFloor: floor.index })}>
            <rect x={px(room.x)} y={py(room.y + room.d)} width={room.w * scale} height={room.d * scale}
              fill={room.is_outdoor ? "#1d2b23" : "#20303f"} stroke="#41586e" strokeWidth={1} />
            {room.w * scale > 34 && room.d * scale > 16 && (
              <text x={px(room.x + room.w / 2)} y={py(room.y + room.d / 2)} fontSize={8}
                fill="#9fb6cc" textAnchor="middle">{room.name.split(" ")[0]}</text>
            )}
          </g>
        ))}
        {floor?.openings.filter((o) => o.kind === "door" || o.kind === "sliding").map((op) => {
          const room = floor.rooms.find((r) => r.id === op.room_id);
          if (!room) return null;
          const horiz = op.edge === "N" || op.edge === "S";
          const ox = horiz ? room.x + op.offset + op.width / 2 : op.edge === "W" ? room.x : room.x + room.w;
          const oy = horiz ? (op.edge === "S" ? room.y : room.y + room.d) : room.y + op.offset + op.width / 2;
          return <circle key={op.id} cx={px(ox)} cy={py(oy)} r={2.2} fill="#7fb0ea" />;
        })}
        {hud && (
          <g transform={`translate(${px(hud.position.x)}, ${py(hud.position.y)})`}>
            <path d="M 0 -7 L 4.5 5 L 0 2.5 L -4.5 5 Z" fill="#3f7fd6" stroke="#dce9fa"
              strokeWidth={0.8} transform={`rotate(${(-hud.yaw * 180) / Math.PI})`} />
          </g>
        )}
      </svg>
    </div>
  );
}
