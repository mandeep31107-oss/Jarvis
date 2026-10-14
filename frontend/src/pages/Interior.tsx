/** Interior design assistant: styles, material/colour panels that update the 3D
 *  surfaces live, per-room finishes, furniture styles and alternatives. */

import { useEffect, useState } from "react";
import type { ProjectData } from "../types/project";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { SceneRoot } from "../three/SceneRoot";
import { Badge, Button, Panel, SectionTitle, inputClass } from "../components/ui";

interface Catalogue {
  styles: { key: string; label: string; description: string; cost_impact: string; tips: string[]; best_for: string[] }[];
  options: Record<string, { key?: string; label: string; value?: string }[]>;
  furniture_styles: { key: string; label: string }[] | Record<string, string>;
}

const MATERIAL_FIELDS: { field: keyof ProjectData["materials"]; label: string; optionKey?: string }[] = [
  { field: "interior_wall", label: "Interior wall finish", optionKey: "wall_finishes" },
  { field: "floor_material", label: "Flooring material", optionKey: "flooring" },
  { field: "floor_color", label: "Floor colour", optionKey: "colours" },
  { field: "feature_wall", label: "Feature wall", optionKey: "wall_finishes" },
  { field: "bathroom_tile", label: "Bathroom tiles", optionKey: "tiles" },
  { field: "kitchen_cabinet", label: "Kitchen cabinets", optionKey: "cabinets" },
  { field: "kitchen_counter", label: "Countertop", optionKey: "counters" },
  { field: "ceiling_style", label: "Ceiling design", optionKey: "ceilings" },
  { field: "curtain_color", label: "Curtains", optionKey: "curtains" },
  { field: "door_material", label: "Doors", optionKey: "doors" },
];

export default function Interior() {
  const data = useStore((s) => s.data) as ProjectData;
  const projectId = useStore((s) => s.projectId)!;
  const setData = useStore((s) => s.setData);
  const mutate = useStore((s) => s.mutate);
  const toast = useStore((s) => s.toast);
  const selectedRoomId = useStore((s) => s.selectedRoomId);
  const set = useStore((s) => s.set);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [alternatives, setAlternatives] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    api.get<Catalogue>(`/api/projects/${projectId}/materials/catalogue`).then(setCatalogue).catch(() => undefined);
    api.get<{ alternatives: Record<string, unknown>[] }>(`/api/projects/${projectId}/alternatives`)
      .then((p) => setAlternatives(p.alternatives)).catch(() => undefined);
    const previous = useStore.getState().viewMode;
    set({ viewMode: "room" });
    return () => set({ viewMode: previous });
  }, [projectId, set]);

  const applyMaterial = async (field: string, value: string) => {
    mutate((draft) => {
      (draft.materials as unknown as Record<string, string>)[field] = value;
    }, `material ${field}`);
  };

  const room = data.floors.flatMap((f) => f.rooms).find((r) => r.id === selectedRoomId);

  return (
    <div className="grid h-full grid-cols-1 gap-0 xl:grid-cols-[420px_1fr]">
      <aside className="space-y-3 overflow-y-auto border-r border-ink-600 bg-ink-800 p-3">
        <Panel className="p-3">
          <SectionTitle>Styles (applies everywhere, live)</SectionTitle>
          <div className="grid grid-cols-2 gap-1.5">
            {(catalogue?.styles || []).map((style) => (
              <button key={style.key} title={style.description}
                onClick={async () => {
                  const payload = await api.post<{ materials: ProjectData["materials"]; notes: string[] }>(
                    `/api/projects/${projectId}/materials/style`, { style: style.key });
                  await setData({ ...data, materials: payload.materials }, { label: `style ${style.key}`, snapshot: false });
                  toast("success", `${style.label} applied — 3D surfaces updated.`);
                }}
                className={`rounded-lg border p-2 text-left text-[12px] transition ${data.requirements.style === style.key
                  ? "border-accent bg-accent-dim/40 text-white" : "border-ink-600 bg-ink-700/50 text-slate-300 hover:border-accent/60"}`}>
                <div className="font-medium capitalize">{style.label.replace(/_/g, " ")}</div>
                <div className="text-[10.5px] text-slate-500">{style.cost_impact}</div>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="space-y-2 p-3">
          <SectionTitle>Materials & colours</SectionTitle>
          {MATERIAL_FIELDS.map((entry) => (
            <label key={String(entry.field)} className="block">
              <span className="mb-0.5 block text-[11.5px] text-slate-400">{entry.label}</span>
              <select className={inputClass} value={(data.materials as unknown as Record<string, string>)[entry.field] || ""}
                onChange={(e) => void applyMaterial(entry.field, e.target.value)}>
                {(catalogue?.options?.[entry.optionKey || ""] || []).map((option) => (
                  <option key={String(option.value || option.label)} value={String(option.value || option.label)}>
                    {option.label}
                  </option>
                ))}
                {!(catalogue?.options?.[entry.optionKey || ""] || []).length && (
                  <option value={(data.materials as unknown as Record<string, string>)[entry.field]}>
                    {(data.materials as unknown as Record<string, string>)[entry.field]}
                  </option>
                )}
              </select>
            </label>
          ))}
        </Panel>

        <Panel className="space-y-2 p-3">
          <SectionTitle>Room-level finish {room ? `— ${room.name}` : "(select a room in 2D/3D)"}</SectionTitle>
          {room ? (
            <>
              <label className="block">
                <span className="mb-0.5 block text-[11.5px] text-slate-400">Wall colour</span>
                <input className={inputClass} defaultValue={room.wall_color || ""} placeholder="e.g. sage, #d9cbb2"
                  onBlur={(e) => mutate((d) => {
                    const target = d.floors.flatMap((f) => f.rooms).find((r) => r.id === room.id)!;
                    target.wall_color = e.target.value || null;
                  }, "room wall colour")} />
              </label>
              <label className="block">
                <span className="mb-0.5 block text-[11.5px] text-slate-400">Floor material override</span>
                <input className={inputClass} defaultValue={room.floor_material || ""} placeholder="wood / marble / tile…"
                  onBlur={(e) => mutate((d) => {
                    const target = d.floors.flatMap((f) => f.rooms).find((r) => r.id === room.id)!;
                    target.floor_material = e.target.value || null;
                  }, "room floor material")} />
              </label>
            </>
          ) : <p className="text-[12px] text-slate-500">Pick a room in the 2D editor or the 3D viewer to override its finishes.</p>}
        </Panel>

        <Panel className="p-3">
          <SectionTitle>Furniture style</SectionTitle>
          <select className={inputClass} defaultValue={data.requirements.style}
            onChange={async (e) => {
              const payload = await api.post<{ data: ProjectData }>(
                `/api/projects/${projectId}/materials/furniture-style`, { style: e.target.value });
              await setData(payload.data, { label: "furniture style", snapshot: false });
            }}>
            {Object.entries(catalogue?.furniture_styles || {}).map(([key, label]) => (
              <option key={key} value={key}>{String(label)}</option>
            ))}
          </select>
          <SectionTitle className="mt-3">Alternatives without rebuilding</SectionTitle>
          <ul className="space-y-1 text-[12px] text-slate-300">
            {alternatives.slice(0, 5).map((a, i) => (
              <li key={i}>• {String(a.title || a.label || "")}: {String(a.detail || a.description || "")}</li>
            ))}
          </ul>
        </Panel>
      </aside>

      <div className="relative min-h-[420px]">
        <SceneRoot data={data} interactive={false} />
        <div className="pointer-events-none absolute left-3 top-3 rounded-lg bg-ink-800/85 px-3 py-1.5 text-[12px] text-slate-300">
          Live preview — every panel change re-renders these surfaces immediately.
          <Badge tone="info"> single room view</Badge>
        </div>
        <div className="absolute bottom-3 right-3 flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "room" })}>Room</Button>
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "dollhouse" })}>Dollhouse</Button>
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "walk" })}>Walk it</Button>
        </div>
      </div>
    </div>
  );
}
