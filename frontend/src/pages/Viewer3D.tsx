/** 3D House Viewer + immersive walkthrough: camera modes, visibility manager,
 *  first-person HUD, guided tours, room directory, inspection panel, mini-map,
 *  measure tool, comparison snapshot and comfort/performance settings. */

import { useEffect, useMemo, useRef, useState } from "react";
import { Line } from "@react-three/drei";
import type { ProjectData } from "../types/project";
import { SceneRoot } from "../three/SceneRoot";
import type { HudState } from "../three/FirstPerson";
import { walkApi } from "../three/FirstPerson";
import { MiniMap } from "../components/MiniMap";
import { useStore } from "../lib/store";
import { roomArea } from "../lib/walls";
import { buildNavGraph, inaccessibleRooms, TOUR_ROUTES } from "../lib/model/navgraph";
import { formatArea, formatLength } from "../lib/units";
import { Button, Panel, SectionTitle, Slider, Toggle } from "../components/ui";

const VIEW_MODES: { key: string; label: string; hint: string }[] = [
  { key: "exterior", label: "Exterior", hint: "Orbit the whole house" },
  { key: "walk", label: "Walkthrough", hint: "First-person, human eye level" },
  { key: "top", label: "Top down", hint: "Plan view from above" },
  { key: "dollhouse", label: "Dollhouse", hint: "Walls hidden, floors stacked" },
  { key: "room", label: "Single room", hint: "Focus the selected room" },
  { key: "elev_front", label: "Front elevation", hint: "Road-side elevation" },
  { key: "elev_side", label: "Side elevation", hint: "East or west elevation" },
  { key: "elev_rear", label: "Rear elevation", hint: "Opposite the road" },
];

export default function Viewer3D() {
  const data = useStore((s) => s.data) as ProjectData | null;
  const viewMode = useStore((s) => s.viewMode);
  const set = useStore((s) => s.set);
  const [hud, setHud] = useState<HudState | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showTour, setShowTour] = useState(false);
  const [showRooms, setShowRooms] = useState(false);
  const [showInspect, setShowInspect] = useState(true);
  const canvasWrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!data) return;
    const handler = (event: KeyboardEvent) => {
      if (event.code === "Escape" && useStore.getState().viewMode === "walk") {
        /* pointer lock releases itself; nothing else to do */
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [data]);

  const graph = useMemo(() => (data ? buildNavGraph(data) : null), [data]);
  const blocked = useMemo(() => (data && graph ? inaccessibleRooms(data, graph) : []), [data, graph]);

  if (!data) return <Panel className="m-8">Loading project…</Panel>;

  const measure = useStore.getState().measurePoints;

  return (
    <div className="relative h-full w-full overflow-hidden bg-ink-900">
      <div ref={canvasWrap} className="absolute inset-0">
        <SceneRoot data={data} onHud={setHud} />
        {measure.length === 2 && (
          <div className="pointer-events-none absolute left-1/2 top-16 z-20 rounded bg-ink-800/90 px-3 py-1 text-sm text-accent-soft">
            Distance {Math.hypot(measure[0].x - measure[1].x, measure[0].y - measure[1].y,
              measure[0].z - measure[1].z).toFixed(2)} m
          </div>
        )}
      </div>

      {/* top toolbar */}
      <div className="absolute left-0 right-0 top-0 z-20 flex flex-wrap items-center gap-2 border-b border-ink-600 bg-ink-800/90 px-3 py-2 backdrop-blur">
        <div className="flex flex-wrap gap-1">
          {VIEW_MODES.map((mode) => (
            <button key={mode.key} title={mode.hint}
              onClick={() => set({ viewMode: mode.key as never, tour: { ...useStore.getState().tour, active: false } })}
              className={`rounded px-2.5 py-1 text-xs font-medium transition ${viewMode === mode.key
                ? "bg-accent text-white shadow-glow" : "bg-ink-700 text-slate-300 hover:bg-ink-600"}`}>
              {mode.label}
            </button>
          ))}
        </div>
        <div className="mx-2 h-5 w-px bg-ink-600" />
        <div className="flex gap-1">
          {data.floors.map((floor) => (
            <button key={floor.id}
              onClick={() => set({ activeFloor: floor.index, focusRoomId: null })}
              className={`rounded px-2 py-1 text-xs ${useStore.getState().activeFloor === floor.index
                ? "bg-accent-dim text-white" : "bg-ink-700 text-slate-300 hover:bg-ink-600"}`}>
              {floor.name}
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => set({ measureMode: !useStore.getState().measureMode, measurePoints: [] })}>
            {useStore.getState().measureMode ? "Measuring: on" : "Measure"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => captureSnapshot()}>Compare A/B</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowRooms((v) => !v)}>Rooms</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowTour((v) => !v)}>Tour</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowSettings((v) => !v)}>Settings</Button>
        </div>
      </div>

      {/* visibility + lighting strip */}
      <div className="absolute right-3 top-14 z-20 w-56 space-y-2">
        <Panel className="p-3">
          <SectionTitle>Visibility</SectionTitle>
          <VisibilityToggles />
          <SectionTitle className="mt-3">Lighting</SectionTitle>
          <LightingControls />
        </Panel>
      </div>

      {/* walk HUD */}
      {viewMode === "walk" && (
        <>
          <div className="pointer-events-none absolute left-1/2 top-1/2 z-20 -translate-x-1/2 -translate-y-1/2">
            <div className="h-1.5 w-1.5 rounded-full bg-white/80 shadow-glow" />
          </div>
          {hud?.prompt && (
            <div className="absolute bottom-28 left-1/2 z-20 -translate-x-1/2 rounded-lg bg-ink-800/95 px-4 py-2 text-sm text-accent-soft shadow-panel">
              {hud.prompt} <span className="ml-2 rounded bg-accent-dim px-1.5 py-0.5 text-[11px] text-white">E</span>
            </div>
          )}
          {hud?.tourStop && useStore.getState().tour.active && (
            <div className="absolute bottom-40 left-1/2 z-20 -translate-x-1/2 rounded-lg bg-accent/90 px-4 py-2 text-sm font-medium text-white shadow-panel">
              {hud.tourStop}
            </div>
          )}
          <div className="absolute bottom-3 left-1/2 z-20 -translate-x-1/2 rounded-lg bg-ink-800/85 px-4 py-2 text-[12px] text-slate-300 shadow-panel">
            Click to look · WASD / arrows to walk · Shift sprint (free mode) · E interact · Esc release mouse
            <div className="mt-1 flex gap-2">
              <Button size="sm" variant="ghost" onClick={() => walkApi()?.reset()}>Street view</Button>
              <Button size="sm" variant="ghost" onClick={() => walkApi()?.enter()}>Front door</Button>
              <Button size="sm" variant="ghost"
                onClick={() => set({ viewMode: "exterior" })}>Exit walkthrough</Button>
            </div>
          </div>
          <div className="absolute bottom-3 right-3 z-20"><MiniMap data={data} hud={hud} /></div>
        </>
      )}
      {viewMode !== "walk" && (
        <div className="absolute bottom-3 right-3 z-20"><MiniMap data={data} hud={hud} /></div>
      )}

      {/* rooms directory */}
      {showRooms && (
        <Panel className="absolute left-3 top-14 z-20 max-h-[70%] w-72 overflow-auto p-3">
          <SectionTitle>Room directory</SectionTitle>
          {blocked.length > 0 && (
            <p className="mb-2 rounded bg-amber-900/40 p-2 text-[12px] text-amber-200">
              Not reachable by walking with the current plan: {blocked.join(", ")}. Fix blocked
              doors or disconnected rooms in the 2D editor.
            </p>
          )}
          {data.floors.map((floor) => (
            <div key={floor.id} className="mb-2">
              <div className="text-[11px] uppercase tracking-wide text-slate-400">{floor.name}</div>
              {floor.rooms.filter((r) => r.type !== "open_to_below").map((room) => (
                <div key={room.id} className="mt-1 flex items-center justify-between gap-2 rounded bg-ink-700/70 px-2 py-1">
                  <button className="flex-1 truncate text-left text-[13px] text-slate-200 hover:text-white"
                    onClick={() => set({ selectedRoomId: room.id, focusRoomId: room.id, activeFloor: floor.index })}>
                    {room.name}
                  </button>
                  <button title="Walk to its entrance along a real route"
                    className="rounded bg-accent-dim px-1.5 py-0.5 text-[11px] text-white hover:bg-accent"
                    onClick={() => {
                      set({ viewMode: "walk" });
                      window.setTimeout(() => {
                        const ok = walkApi()?.goToRoom(room.id);
                        if (!ok) useStore.getState().toast("warn",
                          `No walkable route to ${room.name} from where you are standing.`);
                      }, 60);
                    }}>
                    Walk
                  </button>
                </div>
              ))}
            </div>
          ))}
        </Panel>
      )}

      {/* tour panel */}
      {showTour && (
        <Panel className="absolute left-3 top-14 z-20 w-72 p-3">
          <SectionTitle>Guided virtual tour</SectionTitle>
          <p className="mb-2 text-[12px] text-slate-400">
            Routes follow real doors and stairs from the plan. If a route is impossible you get
            an explanation instead of a camera flying through walls.
          </p>
          <select className="mb-2 w-full rounded bg-ink-700 px-2 py-1 text-sm text-slate-200"
            value={useStore.getState().tour.routeKey}
            onChange={(e) => set({ tour: { ...useStore.getState().tour, routeKey: e.target.value } })}>
            {TOUR_ROUTES.map((route) => <option key={route.key} value={route.key}>{route.label}</option>)}
          </select>
          <div className="flex flex-wrap gap-1">
            <Button size="sm" onClick={() => set({ viewMode: "walk", tour: { ...useStore.getState().tour, active: true, paused: false } })}>
              {useStore.getState().tour.active ? "Restart" : "Start tour"}
            </Button>
            <Button size="sm" variant="ghost"
              onClick={() => set({ tour: { ...useStore.getState().tour, paused: !useStore.getState().tour.paused } })}>
              {useStore.getState().tour.paused ? "Resume" : "Pause"}
            </Button>
            <Button size="sm" variant="ghost"
              onClick={() => set({ tour: { ...useStore.getState().tour, active: false } })}>Stop</Button>
          </div>
          <Slider label="Tour speed" min={0.4} max={2.5} step={0.1}
            value={useStore.getState().tour.speed}
            onChange={(v) => set({ tour: { ...useStore.getState().tour, speed: v } })} />
        </Panel>
      )}

      {/* settings */}
      {showSettings && (
        <Panel className="absolute left-1/2 top-16 z-30 w-80 -translate-x-1/2 p-4">
          <SectionTitle>Comfort & performance</SectionTitle>
          <WalkSettings />
          <SectionTitle className="mt-3">Quality preset</SectionTitle>
          <QualityControls />
        </Panel>
      )}

      {/* inspection panel */}
      {showInspect && <InspectionPanel data={data} />}
      <CompareOverlay />
    </div>
  );
}

function captureSnapshot() {
  const canvas = document.querySelector("canvas");
  if (!canvas) return;
  const url = (canvas as HTMLCanvasElement).toDataURL("image/jpeg", 0.8);
  useStore.getState().set({ compareSnapshot: url });
  useStore.getState().toast("info", "Snapshot captured. Make a material change, then drag the A/B slider.");
}

function VisibilityToggles() {
  const visibility = useStore((s) => s.visibility);
  const set = useStore((s) => s.set);
  const entries: [keyof typeof visibility, string][] = [
    ["roof", "Roof"], ["ceiling", "Ceiling"], ["walls", "Walls"], ["furniture", "Furniture"],
    ["floors", "Floors"], ["cutaway", "Cutaway walls"], ["exploded", "Exploded floors"],
    ["transparentWalls", "Ghost walls"],
  ];
  return (
    <div className="grid grid-cols-2 gap-1">
      {entries.map(([key, label]) => (
        <Toggle key={key} label={label} checked={visibility[key]}
          onChange={(v) => set({ visibility: { ...visibility, [key]: v } })} small />
      ))}
    </div>
  );
}

function LightingControls() {
  const quality = useStore((s) => s.quality);
  const set = useStore((s) => s.set);
  return (
    <div className="space-y-2">
      <div className="flex gap-1">
        {(["day", "evening", "night"] as const).map((preset) => (
          <button key={preset}
            onClick={() => set({ quality: { ...quality, lighting: preset } })}
            className={`flex-1 rounded px-2 py-1 text-[11px] capitalize ${quality.lighting === preset
              ? "bg-accent text-white" : "bg-ink-700 text-slate-300 hover:bg-ink-600"}`}>
            {preset}
          </button>
        ))}
      </div>
      <div className="flex gap-1">
        {(["warm", "neutral", "cool"] as const).map((temp) => (
          <button key={temp}
            onClick={() => set({ quality: { ...quality, temperature: temp } })}
            className={`flex-1 rounded px-2 py-1 text-[11px] capitalize ${quality.temperature === temp
              ? "bg-accent-dim text-white" : "bg-ink-700 text-slate-300 hover:bg-ink-600"}`}>
            {temp}
          </button>
        ))}
      </div>
      <Slider label="Sun position" min={0} max={360} step={5} value={quality.sunAngle}
        onChange={(v) => set({ quality: { ...quality, sunAngle: v } })} />
      <Slider label="Exposure" min={0.4} max={2} step={0.05} value={quality.exposure}
        onChange={(v) => set({ quality: { ...quality, exposure: v } })} />
    </div>
  );
}

function WalkSettings() {
  const walk = useStore((s) => s.walk);
  const set = useStore((s) => s.set);
  return (
    <div className="space-y-2">
      <div className="flex gap-1">
        {(["realistic", "free"] as const).map((mode) => (
          <button key={mode} onClick={() => set({ walk: { ...walk, mode } })}
            className={`flex-1 rounded px-2 py-1 text-[11px] capitalize ${walk.mode === mode
              ? "bg-accent text-white" : "bg-ink-700 text-slate-300 hover:bg-ink-600"}`}>
            {mode === "realistic" ? "Realistic walk" : "Free explore"}
          </button>
        ))}
      </div>
      <Slider label="Eye height (m)" min={1.2} max={2} step={0.05} value={walk.eyeHeight}
        onChange={(v) => set({ walk: { ...walk, eyeHeight: v } })} />
      <Slider label="Walk speed (m/s)" min={0.6} max={2.5} step={0.05} value={walk.speed}
        onChange={(v) => set({ walk: { ...walk, speed: v } })} />
      <Slider label="Look sensitivity" min={0.3} max={2.5} step={0.05} value={walk.sensitivity}
        onChange={(v) => set({ walk: { ...walk, sensitivity: v } })} />
      <Toggle label="Invert look" checked={walk.invertLook}
        onChange={(v) => set({ walk: { ...walk, invertLook: v } })} small />
      <Toggle label="Head bob" checked={walk.bob}
        onChange={(v) => set({ walk: { ...walk, bob: v } })} small />
      <Toggle label="Ambient audio & footsteps" checked={walk.audio}
        onChange={(v) => set({ walk: { ...walk, audio: v } })} small />
    </div>
  );
}

function QualityControls() {
  const quality = useStore((s) => s.quality);
  const set = useStore((s) => s.set);
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-1">
        {(["high", "balanced", "performance", "mobile"] as const).map((preset) => (
          <button key={preset} onClick={() => set({ quality: { ...quality, preset,
            shadows: preset !== "mobile" && preset !== "performance",
            dpr: preset === "high" ? 2 : preset === "balanced" ? 1.5 : 1 } })}
            className={`rounded px-2 py-1 text-[11px] capitalize ${quality.preset === preset
              ? "bg-accent text-white" : "bg-ink-700 text-slate-300 hover:bg-ink-600"}`}>
            {preset}
          </button>
        ))}
      </div>
      <Toggle label="Shadows" checked={quality.shadows}
        onChange={(v) => set({ quality: { ...quality, shadows: v } })} small />
    </div>
  );
}

function InspectionPanel({ data }: { data: ProjectData }) {
  const selectedRoomId = useStore((s) => s.selectedRoomId);
  const selectedObjectId = useStore((s) => s.selectedObjectId);
  const set = useStore((s) => s.set);
  const room = data.floors.flatMap((f) => f.rooms).find((r) => r.id === selectedRoomId);
  const floor = data.floors.find((f) => f.rooms.some((r) => r.id === selectedRoomId));
  const object = useMemo(() => {
    if (!selectedObjectId) return null;
    for (const f of data.floors) {
      const item = f.furniture.find((i) => i.id === selectedObjectId);
      if (item) return { label: item.label || item.kind, dims: `${item.w.toFixed(2)} × ${item.d.toFixed(2)} × ${item.height.toFixed(2)} m`, material: item.material || item.color || "—" };
      const op = f.openings.find((o) => o.id === selectedObjectId);
      if (op) return { label: `${op.kind} (${op.width.toFixed(2)} m wide)`, dims: `${op.width.toFixed(2)} × ${op.height.toFixed(2)} m, sill ${op.sill.toFixed(2)} m`, material: op.material || "—" };
    }
    return null;
  }, [selectedObjectId, data]);

  if (!room && !object) return null;
  return (
    <Panel className="absolute bottom-3 left-3 z-20 w-80 p-3 text-[13px]">
      <div className="flex items-start justify-between">
        <SectionTitle>{room ? room.name : object?.label}</SectionTitle>
        <button className="text-slate-400 hover:text-white" onClick={() => set({ selectedRoomId: null, selectedObjectId: null })}>✕</button>
      </div>
      {room && floor && (
        <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-slate-300">
          <dt className="text-slate-400">Floor</dt><dd>{floor.name}</dd>
          <dt className="text-slate-400">Size</dt><dd>{room.w.toFixed(2)} × {room.d.toFixed(2)} m</dd>
          <dt className="text-slate-400">Area</dt><dd>{formatArea(roomArea(room), "sqft")} ({roomArea(room).toFixed(1)} m²)</dd>
          <dt className="text-slate-400">Ceiling</dt><dd>{room.ceiling_height.toFixed(2)} m</dd>
          <dt className="text-slate-400">Wall finish</dt><dd>{room.wall_color || data.materials.interior_wall}</dd>
          <dt className="text-slate-400">Floor finish</dt><dd>{room.floor_material || data.materials.floor_material}</dd>
          <dt className="text-slate-400">Doors</dt>
          <dd>{floor.openings.filter((o) => o.room_id === room.id && (o.kind === "door" || o.kind === "sliding")).length}</dd>
          <dt className="text-slate-400">Windows</dt>
          <dd>{floor.openings.filter((o) => o.room_id === room.id && o.kind === "window").length}</dd>
          <dt className="text-slate-400">Furniture</dt>
          <dd>{floor.furniture.filter((i) => i.room_id === room.id).map((i) => i.label || i.kind).join(", ") || "—"}</dd>
        </dl>
      )}
      {object && (
        <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-slate-300">
          <dt className="text-slate-400">Dimensions</dt><dd>{object.dims}</dd>
          <dt className="text-slate-400">Material</dt><dd>{object.material}</dd>
        </dl>
      )}
      <p className="mt-2 text-[11px] text-slate-500">
        Change finishes live from the Interior page; nothing here edits the plan until you save it there.
      </p>
    </Panel>
  );
}

function CompareOverlay() {
  const snapshot = useStore((s) => s.compareSnapshot);
  const set = useStore((s) => s.set);
  const [split, setSplit] = useState(50);
  if (!snapshot) return null;
  return (
    <div className="pointer-events-none absolute inset-0 z-30">
      <img src={snapshot} alt="Before change" className="absolute inset-0 h-full w-full object-cover"
        style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }} />
      <div className="pointer-events-auto absolute bottom-24 left-1/2 flex -translate-x-1/2 items-center gap-3 rounded-lg bg-ink-800/95 px-4 py-2 shadow-panel">
        <span className="text-xs text-slate-300">Before</span>
        <input type="range" min={0} max={100} value={split} className="w-56 accent-[#3f7fd6]"
          onChange={(e) => setSplit(Number(e.target.value))} />
        <span className="text-xs text-slate-300">Now</span>
        <button className="text-xs text-accent-soft hover:underline" onClick={() => set({ compareSnapshot: null })}>
          clear
        </button>
      </div>
    </div>
  );
}
