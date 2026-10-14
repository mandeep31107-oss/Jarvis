/** Interactive 2D floor-plan editor: move/resize/rename rooms, drag doors and
 *  windows along their wall, move furniture, grid snap, zoom/pan, undo/redo,
 *  reset to the saved version, live area recalculation and conflict flags.
 *  Walls are derived from room rectangles, so moving a shared wall between two
 *  rooms is exactly a resize of one of them — the neighbour follows. */

import { useMemo, useRef, useState } from "react";
import type { Edge, Floor, Opening, ProjectData, Room } from "../types/project";
import { useStore } from "../lib/store";
import { deriveWalls, openingSpan, roomRect, roomArea, snap, clamp } from "../lib/walls";
import { formatArea, formatLength } from "../lib/units";
import { Badge, Button, Panel, SectionTitle, Slider, Toggle, inputClass } from "../components/ui";
import { CommandBox } from "../components/CommandBox";

const ROOM_FILL: Record<string, string> = {
  living: "#31465e", dining: "#3c4a3a", kitchen: "#54432f", utility: "#3d4a4a",
  bedroom: "#37405c", master_bedroom: "#3b3f66", bathroom: "#2f4a52", toilet: "#2f4a52",
  study: "#40405a", puja: "#54405a", store: "#454545", stairs: "#4a4a4a", lobby: "#3a4a55",
  corridor: "#3a4a55", balcony: "#2c3e33", terrace: "#2c3e33", garden: "#27402a",
  courtyard: "#27402a", parking: "#3c3c3c", porch: "#3c4a44", servant_room: "#41414d",
  shop: "#4d3f33", open_to_below: "#20262c",
};

type DragState =
  | { kind: "pan"; startX: number; startY: number; ox: number; oy: number }
  | { kind: "move"; roomId: string; startX: number; startY: number; ox: number; oy: number }
  | { kind: "resize"; roomId: string; edge: Edge; startX: number; startY: number; rect: { x: number; y: number; w: number; d: number } }
  | { kind: "opening"; openingId: string }
  | { kind: "furniture"; itemId: string }
  | null;

export default function Plan2D() {
  const data = useStore((s) => s.data) as ProjectData;
  const setData = useStore((s) => s.setData);
  const mutate = useStore((s) => s.mutate);
  const undo = useStore((s) => s.undo);
  const redo = useStore((s) => s.redo);
  const undoStack = useStore((s) => s.undoStack);
  const redoStack = useStore((s) => s.redoStack);
  const activeFloor = useStore((s) => s.activeFloor);
  const selectedRoomId = useStore((s) => s.selectedRoomId);
  const selectedObjectId = useStore((s) => s.selectedObjectId);
  const validation = useStore((s) => s.validation);
  const set = useStore((s) => s.set);
  const toast = useStore((s) => s.toast);

  const floor: Floor = data.floors.find((f) => f.index === activeFloor) || data.floors[0];
  const [view, setView] = useState({ scale: 42, ox: 0, oy: 0 });
  const [gridSnap, setGridSnap] = useState(true);
  const [showDims, setShowDims] = useState(true);
  const drag = useRef<DragState>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const walls = useMemo(() => deriveWalls(floor.rooms), [floor]);
  const errorRooms = useMemo(() => new Set((validation?.issues || [])
    .filter((i) => i.severity === "error").flatMap((i) => i.room_ids || (i.room_id ? [i.room_id] : []))),
  [validation]);

  const px = (x: number) => (x + view.ox) * view.scale;
  const py = (y: number) => (data.plot.depth - y + view.oy) * view.scale;
  const toPlan = (clientX: number, clientY: number) => {
    const rect = svgRef.current!.getBoundingClientRect();
    return {
      x: (clientX - rect.left) / view.scale - view.ox,
      y: data.plot.depth - ((clientY - rect.top) / view.scale - view.oy),
    };
  };

  const apply = (fn: (draft: ProjectData) => void, save = false) => {
    const draft = structuredClone(data) as ProjectData;
    fn(draft);
    if (save) void mutate(() => fn(structuredClone(data) as ProjectData));
    else void setData(draft, { save: false, snapshot: false });
  };

  const commitEdit = () => { void useStore.getState().save("2D edit"); };

  // ---------------- pointer handlers ----------------
  const onRoomDown = (event: React.PointerEvent, room: Room) => {
    event.stopPropagation();
    set({ selectedRoomId: room.id, selectedObjectId: null });
    const point = toPlan(event.clientX, event.clientY);
    drag.current = { kind: "move", roomId: room.id, startX: point.x, startY: point.y, ox: room.x, oy: room.y };
  };
  const onHandleDown = (event: React.PointerEvent, room: Room, edge: Edge) => {
    event.stopPropagation();
    const point = toPlan(event.clientX, event.clientY);
    drag.current = { kind: "resize", roomId: room.id, edge, startX: point.x, startY: point.y, rect: roomRect(room) };
  };
  const onOpeningDown = (event: React.PointerEvent, opening: Opening) => {
    event.stopPropagation();
    set({ selectedObjectId: opening.id, selectedRoomId: opening.room_id });
    drag.current = { kind: "opening", openingId: opening.id };
  };
  const onFurnitureDown = (event: React.PointerEvent, itemId: string) => {
    event.stopPropagation();
    set({ selectedObjectId: itemId });
    drag.current = { kind: "furniture", itemId };
  };
  const onBackgroundDown = (event: React.PointerEvent) => {
    drag.current = { kind: "pan", startX: event.clientX, startY: event.clientY, ox: view.ox, oy: view.oy };
  };
  const onMove = (event: React.PointerEvent) => {
    const state = drag.current;
    if (!state) return;
    const point = toPlan(event.clientX, event.clientY);
    const g = (v: number) => (gridSnap ? snap(v) : Math.round(v * 100) / 100);
    if (state.kind === "pan") {
      setView((v) => ({ ...v, ox: state.ox + (event.clientX - state.startX) / v.scale,
        oy: state.oy - (event.clientY - state.startY) / v.scale }));
      return;
    }
    if (state.kind === "move") {
      const dx = g(point.x - state.startX), dy = g(point.y - state.startY);
      apply((draft) => {
        const target = draft.floors.find((f) => f.index === activeFloor)!.rooms.find((r) => r.id === state.roomId)!;
        target.x = clamp(g(state.ox + dx), 0, data.plot.width - target.w);
        target.y = clamp(g(state.oy + dy), 0, data.plot.depth - target.d);
      });
      return;
    }
    if (state.kind === "resize") {
      const dx = point.x - state.startX, dy = point.y - state.startY;
      apply((draft) => {
        const target = draft.floors.find((f) => f.index === activeFloor)!.rooms.find((r) => r.id === state.roomId)!;
        const r = state.rect;
        const min = 1.4;
        if (state.edge === "E") target.w = clamp(g(r.w + dx), min, data.plot.width - r.x);
        if (state.edge === "W") { const nx = clamp(g(r.x + dx), 0, r.x + r.w - min); target.w = r.w + (r.x - nx); target.x = nx; }
        if (state.edge === "N") target.d = clamp(g(r.d + dy), min, data.plot.depth - r.y);
        if (state.edge === "S") { const ny = clamp(g(r.y + dy), 0, r.y + r.d - min); target.d = r.d + (r.y - ny); target.y = ny; }
      });
      return;
    }
    if (state.kind === "opening") {
      apply((draft) => {
        const fl = draft.floors.find((f) => f.index === activeFloor)!;
        const opening = fl.openings.find((o) => o.id === state.openingId)!;
        const room = fl.rooms.find((r) => r.id === opening.room_id)!;
        const length = opening.edge === "N" || opening.edge === "S" ? room.w : room.d;
        const start = opening.edge === "S" || opening.edge === "W"
          ? (opening.edge === "S" ? point.x - room.x : point.y - room.y)
          : (opening.edge === "N" ? point.x - room.x : point.y - room.y);
        opening.offset = clamp(g(start - opening.width / 2), 0.05, Math.max(0.05, length - opening.width - 0.05));
      });
      return;
    }
    if (state.kind === "furniture") {
      apply((draft) => {
        const fl = draft.floors.find((f) => f.index === activeFloor)!;
        const item = fl.furniture.find((i) => i.id === state.itemId)!;
        item.x = g(point.x); item.y = g(point.y);
      });
    }
  };
  const onUp = () => {
    if (drag.current && drag.current.kind !== "pan") commitEdit();
    drag.current = null;
  };

  const selectedRoom = floor.rooms.find((r) => r.id === selectedRoomId);
  const selectedOpening = floor.openings.find((o) => o.id === selectedObjectId);

  const addRoom = (type: Room["type"]) => {
    mutate((draft) => {
      const fl = draft.floors.find((f) => f.index === activeFloor)!;
      const size = type === "bathroom" || type === "toilet" ? 2.2 : type === "study" || type === "store" ? 2.6 : 3.2;
      fl.rooms.push({
        id: `rm_${Math.random().toString(36).slice(2, 10)}`, floor_id: fl.id,
        name: type.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()), type,
        x: snap(data.plot.width / 2 - size / 2), y: snap(data.plot.depth / 2 - size / 2),
        w: size, d: size, ceiling_height: 3, is_outdoor: ["balcony", "terrace", "garden", "courtyard", "parking"].includes(type),
        is_wet: ["bathroom", "toilet", "utility"].includes(type), level_offset: 0, notes: "", tags: [],
      });
    }, "add room");
  };

  const addOpening = (kind: Opening["kind"]) => {
    if (!selectedRoom) { toast("info", "Select a room first, then add a door or window to it."); return; }
    mutate((draft) => {
      const fl = draft.floors.find((f) => f.index === activeFloor)!;
      const room = fl.rooms.find((r) => r.id === selectedRoom.id)!;
      const width = kind === "window" ? 1.2 : 0.9;
      fl.openings.push({
        id: `op_${Math.random().toString(36).slice(2, 10)}`, kind, room_id: room.id, floor_id: fl.id,
        edge: "S", offset: snap(Math.max(0.05, (room.w - width) / 2)), width,
        height: kind === "window" ? 1.2 : 2.1, sill: kind === "window" ? 0.9 : 0,
        connects_to: null, style: "", material: "",
      });
    }, "add opening");
  };

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1 bg-ink-900">
        <div className="absolute left-3 top-3 z-10 flex flex-wrap gap-1">
          {data.floors.map((f) => (
            <Button key={f.id} size="sm" variant={f.index === activeFloor ? "primary" : "ghost"}
              onClick={() => set({ activeFloor: f.index })}>{f.name}</Button>
          ))}
        </div>
        <div className="absolute right-3 top-3 z-10 flex gap-1">
          <Button size="sm" variant="ghost" title="Undo" disabled={!undoStack.length} onClick={() => void undo()}>↶</Button>
          <Button size="sm" variant="ghost" title="Redo" disabled={!redoStack.length} onClick={() => void redo()}>↷</Button>
          <Button size="sm" variant="ghost" onClick={() => setView((v) => ({ ...v, scale: v.scale * 1.2 }))}>+</Button>
          <Button size="sm" variant="ghost" onClick={() => setView((v) => ({ ...v, scale: v.scale / 1.2 }))}>−</Button>
          <Button size="sm" variant="ghost" onClick={() => setView({ scale: 42, ox: 0, oy: 0 })}>Fit</Button>
          <Button size="sm" variant="ghost" title="Reset to the last saved version"
            onClick={async () => { await useStore.getState().loadProject(useStore.getState().projectId!); }}>Reset</Button>
        </div>
        <div className="absolute bottom-3 left-3 z-10 flex flex-col gap-1 rounded-lg bg-ink-800/85 p-2">
          <Toggle small label="Grid snap (5 cm)" checked={gridSnap} onChange={setGridSnap} />
          <Toggle small label="Dimensions" checked={showDims} onChange={setShowDims} />
        </div>
        <svg ref={svgRef} className="h-full w-full touch-none select-none"
          onPointerDown={onBackgroundDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={onUp}
          onWheel={(e) => setView((v) => ({ ...v, scale: clamp(v.scale * (e.deltaY < 0 ? 1.08 : 0.92), 12, 160) }))}>
          <rect x={px(0)} y={py(data.plot.depth)} width={data.plot.width * view.scale}
            height={data.plot.depth * view.scale} fill="#18222c" stroke="#31465e" strokeWidth={1.5} />
          {/* grid */}
          {Array.from({ length: Math.floor(data.plot.width) + 1 }, (_, i) => (
            <line key={`gx${i}`} x1={px(i)} y1={py(0)} x2={px(i)} y2={py(data.plot.depth)} stroke="#20303f" strokeWidth={0.5} />
          ))}
          {Array.from({ length: Math.floor(data.plot.depth) + 1 }, (_, i) => (
            <line key={`gy${i}`} x1={px(0)} y1={py(i)} x2={px(data.plot.width)} y2={py(i)} stroke="#20303f" strokeWidth={0.5} />
          ))}
          {/* outdoor pads */}
          {floor.rooms.filter((r) => r.is_outdoor).map((room) => (
            <g key={room.id} onPointerDown={(e) => onRoomDown(e, room)} className="cursor-move">
              <rect x={px(room.x)} y={py(room.y + room.d)} width={room.w * view.scale} height={room.d * view.scale}
                fill={ROOM_FILL[room.type] || "#2c3e33"} opacity={0.55} stroke="#4d6a55" strokeDasharray="6 4" />
              <text x={px(room.x + room.w / 2)} y={py(room.y + room.d / 2)} textAnchor="middle"
                fontSize={11} fill="#9fb6cc">{room.name}</text>
            </g>
          ))}
          {/* walls */}
          {walls.map((wall) => {
            const box = wall.axis === "y"
              ? { x: wall.lo, y: wall.line - wall.thickness / 2, w: wall.hi - wall.lo, d: wall.thickness }
              : { x: wall.line - wall.thickness / 2, y: wall.lo, w: wall.thickness, d: wall.hi - wall.lo };
            return <rect key={wall.id} x={px(box.x)} y={py(box.y + box.d)} width={box.w * view.scale}
              height={box.d * view.scale} fill={wall.kind === "exterior" ? "#7d8a97" : "#5b6a78"} />;
          })}
          {/* rooms */}
          {floor.rooms.filter((r) => !r.is_outdoor).map((room) => (
            <g key={room.id} onPointerDown={(e) => onRoomDown(e, room)} onDoubleClick={() => {
              const name = window.prompt("Rename room", room.name);
              if (name) mutate((d) => {
                d.floors.find((f) => f.index === activeFloor)!.rooms.find((r) => r.id === room.id)!.name = name;
              }, "rename room");
            }} className="cursor-move">
              <rect x={px(room.x)} y={py(room.y + room.d)} width={room.w * view.scale} height={room.d * view.scale}
                fill={ROOM_FILL[room.type] || "#31465e"} opacity={selectedRoomId === room.id ? 0.95 : 0.8}
                stroke={errorRooms.has(room.id) ? "#e05252" : selectedRoomId === room.id ? "#7fb0ea" : "#22303d"}
                strokeWidth={errorRooms.has(room.id) ? 2.5 : selectedRoomId === room.id ? 2 : 1} />
              <text x={px(room.x + room.w / 2)} y={py(room.y + room.d / 2) - 4} textAnchor="middle"
                fontSize={12} fontWeight={600} fill="#e6edf4" pointerEvents="none">{room.name}</text>
              {showDims && (
                <text x={px(room.x + room.w / 2)} y={py(room.y + room.d / 2) + 12} textAnchor="middle"
                  fontSize={10} fill="#9fb6cc" pointerEvents="none">
                  {formatLength(room.w)} × {formatLength(room.d)} · {formatArea(roomArea(room))}
                </text>
              )}
              {selectedRoomId === room.id && (["E", "W", "N", "S"] as Edge[]).map((edge) => {
                const hx = edge === "W" ? room.x : edge === "E" ? room.x + room.w : room.x + room.w / 2;
                const hy = edge === "S" ? room.y : edge === "N" ? room.y + room.d : room.y + room.d / 2;
                return <rect key={edge} x={px(hx) - 5} y={py(hy) - 5} width={10} height={10}
                  fill="#3f7fd6" stroke="#dce9fa" className="cursor-nwse-resize"
                  onPointerDown={(e) => onHandleDown(e, room, edge)} />;
              })}
            </g>
          ))}
          {/* openings */}
          {floor.openings.map((op) => {
            const room = floor.rooms.find((r) => r.id === op.room_id);
            if (!room) return null;
            const span = openingSpan(room, op);
            const isDoor = op.kind === "door" || op.kind === "sliding" || op.kind === "pass_through";
            if (span.axis === "y") {
              return (
                <g key={op.id} onPointerDown={(e) => onOpeningDown(e, op)} className="cursor-ew-resize">
                  <rect x={px(span.start)} y={py(span.line + 0.12)} width={op.width * view.scale} height={0.24 * view.scale}
                    fill={isDoor ? "#c98d4b" : "#7fb0ea"} stroke={selectedObjectId === op.id ? "#ffffff" : "none"} strokeWidth={1.5} />
                </g>
              );
            }
            return (
              <g key={op.id} onPointerDown={(e) => onOpeningDown(e, op)} className="cursor-ns-resize">
                <rect x={px(span.line - 0.12)} y={py(span.end)} width={0.24 * view.scale} height={op.width * view.scale}
                  fill={isDoor ? "#c98d4b" : "#7fb0ea"} stroke={selectedObjectId === op.id ? "#ffffff" : "none"} strokeWidth={1.5} />
              </g>
            );
          })}
          {/* furniture */}
          {floor.furniture.map((item) => (
            <rect key={item.id} x={px(item.x - item.w / 2)} y={py(item.y + item.d / 2)}
              width={item.w * view.scale} height={item.d * view.scale} rx={2}
              fill="#8fa3b8" opacity={0.5} stroke={selectedObjectId === item.id ? "#ffffff" : "#5c6f82"}
              className="cursor-move" onPointerDown={(e) => onFurnitureDown(e, item.id)}>
              <title>{item.label || item.kind}</title>
            </rect>
          ))}
          {/* stairs */}
          {floor.stairs.map((stair) => (
            <g key={stair.id} pointerEvents="none">
              <rect x={px(stair.x)} y={py(stair.y + stair.d)} width={stair.w * view.scale} height={stair.d * view.scale}
                fill="none" stroke="#9fb6cc" strokeDasharray="4 3" />
              <text x={px(stair.x + stair.w / 2)} y={py(stair.y + stair.d / 2)} fontSize={10}
                fill="#9fb6cc" textAnchor="middle">UP ↓</text>
            </g>
          ))}
        </svg>
      </div>

      <aside className="w-80 shrink-0 space-y-3 overflow-y-auto border-l border-ink-600 bg-ink-800 p-3">
        <CommandBox compact />
        <Panel className="p-3">
          <SectionTitle>Add</SectionTitle>
          <div className="flex flex-wrap gap-1">
            {(["bedroom", "bathroom", "study", "store", "balcony"] as Room["type"][]).map((t) => (
              <Button key={t} size="sm" variant="ghost" onClick={() => addRoom(t)}>+ {t}</Button>
            ))}
            <Button size="sm" variant="ghost" onClick={() => addOpening("door")}>+ door</Button>
            <Button size="sm" variant="ghost" onClick={() => addOpening("window")}>+ window</Button>
          </div>
          <p className="mt-2 text-[11.5px] leading-relaxed text-slate-500">
            Drag rooms to move them; drag the blue handles to resize. A shared wall moves for both
            rooms because walls are derived from the rectangles — if a neighbour would become too
            small or overlap, the checks panel flags it. Drag doors/windows along their wall.
          </p>
        </Panel>
        {selectedRoom && (
          <Panel className="space-y-2 p-3">
            <SectionTitle>{selectedRoom.name}</SectionTitle>
            <p className="text-[12.5px] text-slate-300">
              {selectedRoom.w.toFixed(2)} × {selectedRoom.d.toFixed(2)} m · {formatArea(roomArea(selectedRoom))} · ceiling {selectedRoom.ceiling_height.toFixed(2)} m
            </p>
            <Slider label="Width (m)" min={1.4} max={12} step={0.05} value={selectedRoom.w}
              onChange={(v) => apply((d) => {
                const r = d.floors.find((f) => f.index === activeFloor)!.rooms.find((x) => x.id === selectedRoom.id)!;
                r.w = v;
              })} />
            <Slider label="Depth (m)" min={1.4} max={12} step={0.05} value={selectedRoom.d}
              onChange={(v) => apply((d) => {
                const r = d.floors.find((f) => f.index === activeFloor)!.rooms.find((x) => x.id === selectedRoom.id)!;
                r.d = v;
              })} />
            <div className="flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => commitEdit()}>Save</Button>
              <Button size="sm" variant="danger" onClick={() => mutate((d) => {
                const fl = d.floors.find((f) => f.index === activeFloor)!;
                fl.rooms = fl.rooms.filter((r) => r.id !== selectedRoom.id);
                fl.openings = fl.openings.filter((o) => o.room_id !== selectedRoom.id);
                fl.furniture = fl.furniture.filter((i) => i.room_id !== selectedRoom.id);
              }, "delete room")}>Delete room</Button>
            </div>
          </Panel>
        )}
        {selectedOpening && (
          <Panel className="space-y-2 p-3">
            <SectionTitle>Opening ({selectedOpening.kind})</SectionTitle>
            <Slider label="Width (m)" min={0.5} max={3} step={0.05} value={selectedOpening.width}
              onChange={(v) => apply((d) => {
                const o = d.floors.find((f) => f.index === activeFloor)!.openings.find((x) => x.id === selectedOpening.id)!;
                o.width = v;
              })} />
            <div className="flex gap-1">
              {(["S", "N", "E", "W"] as Edge[]).map((edge) => (
                <Button key={edge} size="sm" variant={selectedOpening.edge === edge ? "primary" : "ghost"}
                  onClick={() => mutate((d) => {
                    const o = d.floors.find((f) => f.index === activeFloor)!.openings.find((x) => x.id === selectedOpening.id)!;
                    o.edge = edge; o.offset = 0.2;
                  }, "move opening")}>{edge}</Button>
              ))}
              <Button size="sm" variant="danger" onClick={() => mutate((d) => {
                const fl = d.floors.find((f) => f.index === activeFloor)!;
                fl.openings = fl.openings.filter((o) => o.id !== selectedOpening.id);
              }, "delete opening")}>✕</Button>
            </div>
          </Panel>
        )}
        <Panel className="p-3">
          <SectionTitle>Checks on this floor</SectionTitle>
          <ul className="space-y-1 text-[12px]">
            {(validation?.issues || []).filter((i) => i.floor_id === floor.id || !i.floor_id).slice(0, 8).map((issue, i) => (
              <li key={i} className={issue.severity === "error" ? "text-red-300" : issue.severity === "warning" ? "text-amber-300" : "text-slate-400"}>
                <Badge tone={issue.severity === "error" ? "error" : issue.severity === "warning" ? "warn" : "info"}>{issue.code}</Badge> {issue.message}
              </li>
            ))}
            {(validation?.issues || []).length === 0 && <li className="text-emerald-300">No issues detected.</li>}
          </ul>
        </Panel>
      </aside>
    </div>
  );
}
