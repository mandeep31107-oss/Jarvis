/** Exterior design: facade, roof, boundary wall, gate, landscaping, exterior
 *  lighting, terrace/balcony treatment — with a live elevation preview. */

import { useEffect, useState } from "react";
import type { ProjectData } from "../types/project";
import { useStore } from "../lib/store";
import { SceneRoot } from "../three/SceneRoot";
import { Button, Panel, SectionTitle, Toggle, inputClass } from "../components/ui";
import { api } from "../lib/api";

interface Catalogue { options: Record<string, { label: string; value?: string }[]> }

export default function Exterior() {
  const data = useStore((s) => s.data) as ProjectData;
  const mutate = useStore((s) => s.mutate);
  const set = useStore((s) => s.set);
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const projectId = useStore((s) => s.projectId)!;

  useEffect(() => {
    api.get<Catalogue>(`/api/projects/${projectId}/materials/catalogue`).then(setCatalogue).catch(() => undefined);
    const previous = useStore.getState().viewMode;
    set({ viewMode: "elev_front" });
    return () => set({ viewMode: previous });
  }, [projectId, set]);

  const patch = (fn: (m: ProjectData["materials"]) => void, label: string) =>
    mutate((draft) => fn(draft.materials), label);

  const select = (field: keyof ProjectData["materials"], label: string, optionKey: string) => (
    <label className="block">
      <span className="mb-0.5 block text-[11.5px] text-slate-400">{label}</span>
      <select className={inputClass} value={(data.materials as unknown as Record<string, string>)[field] || ""}
        onChange={(e) => patch((m) => { (m as unknown as Record<string, string>)[field] = e.target.value; }, label)}>
        {(catalogue?.options?.[optionKey] || []).map((o) => (
          <option key={o.label} value={String(o.value || o.label)}>{o.label}</option>
        ))}
        {!(catalogue?.options?.[optionKey] || []).length && (
          <option value={(data.materials as unknown as Record<string, string>)[field]}>
            {(data.materials as unknown as Record<string, string>)[field]}
          </option>
        )}
      </select>
    </label>
  );

  return (
    <div className="grid h-full grid-cols-1 xl:grid-cols-[380px_1fr]">
      <aside className="space-y-3 overflow-y-auto border-r border-ink-600 bg-ink-800 p-3">
        <Panel className="space-y-2 p-3">
          <SectionTitle>Façade & roof</SectionTitle>
          {select("facade_style", "Facade style", "facades")}
          {select("exterior_wall", "Exterior wall finish", "wall_finishes")}
          {select("exterior_accent", "Accent colour", "colours")}
          {select("roof_type", "Roof form", "roofs")}
          {select("roof_color", "Roof colour", "colours")}
        </Panel>
        <Panel className="space-y-2 p-3">
          <SectionTitle>Boundary, gate & site</SectionTitle>
          <Toggle label="Boundary wall" checked={data.materials.boundary_wall}
            onChange={(v) => patch((m) => { m.boundary_wall = v; }, "boundary wall")} />
          {select("boundary_color", "Boundary wall colour", "colours")}
          {select("gate_color", "Gate colour", "colours")}
          <Toggle label="Landscaping / garden beds" checked={data.materials.landscaping}
            onChange={(v) => patch((m) => { m.landscaping = v; }, "landscaping")} />
          <Toggle label="Exterior lighting" checked={data.materials.exterior_lighting}
            onChange={(v) => patch((m) => { m.exterior_lighting = v; }, "exterior lighting")} />
        </Panel>
        <Panel className="p-3 text-[12.5px] leading-relaxed text-slate-400">
          Balconies and terraces come from the plan itself — add or resize them in the 2D editor and
          the elevation updates. Façade choices here are finishes only; they never move structure.
        </Panel>
      </aside>
      <div className="relative min-h-[420px]">
        <SceneRoot data={data} interactive={false} />
        <div className="absolute bottom-3 right-3 flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "elev_front" })}>Front</Button>
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "elev_side" })}>Side</Button>
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "elev_rear" })}>Rear</Button>
          <Button size="sm" variant="ghost" onClick={() => set({ viewMode: "exterior" })}>Orbit</Button>
        </div>
      </div>
    </div>
  );
}
