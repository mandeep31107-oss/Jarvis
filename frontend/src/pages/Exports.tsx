/** Export & reports: every artefact the master prompt asks for, each recorded
 *  server-side and downloaded through an owner-checked route. */

import { useEffect, useState } from "react";
import type { ExportInfo, ProjectData } from "../types/project";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Panel, SafetyNote, SectionTitle } from "../components/ui";

const KINDS: { kind: string; label: string; detail: string }[] = [
  { kind: "plan_png", label: "Floor plans PNG", detail: "All floors, dimensions, scale bar, disclaimers" },
  { kind: "plan_svg", label: "Floor plan SVG", detail: "Vector plan of the active floor" },
  { kind: "plans_pdf", label: "Plans PDF", detail: "One page per floor, print-ready" },
  { kind: "report_pdf", label: "Design report PDF", detail: "Brief, plans, schedules, budget, checks, disclaimers" },
  { kind: "glb", label: "3D model GLB", detail: "Real geometry: walls with openings, stairs, furniture, site" },
  { kind: "json", label: "Documented JSON", detail: "Full project document with units + coordinate notes" },
  { kind: "csv_rooms", label: "Room schedule CSV", detail: "Every room with dimensions and areas" },
  { kind: "csv_materials", label: "Material list CSV", detail: "Global finishes + room overrides" },
  { kind: "csv_furniture", label: "Furniture list CSV", detail: "Positions, sizes, materials" },
  { kind: "csv_budget", label: "Budget CSV", detail: "14 categories with rates and totals" },
];

export default function Exports() {
  const projectId = useStore((s) => s.projectId)!;
  const data = useStore((s) => s.data) as ProjectData;
  const toast = useStore((s) => s.toast);
  const [exports, setExports] = useState<ExportInfo[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = () => api.get<{ exports: ExportInfo[] }>(`/api/projects/${projectId}/exports`)
    .then((p) => setExports(p.exports)).catch(() => undefined);

  useEffect(() => { refresh(); }, [projectId]);

  const create = async (kind: string) => {
    setBusy(kind);
    try {
      const payload = await api.post<{ url: string; filename: string }>(
        `/api/projects/${projectId}/exports`, { kind, all_floors: true });
      await refresh();
      const link = document.createElement("a");
      link.href = payload.url;
      link.download = payload.filename;
      link.click();
      toast("success", `${payload.filename} ready.`);
    } catch (error) {
      toast("error", (error as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4 p-5">
      <SafetyNote>
        Every export is labelled as a preliminary visualisation. Plans carry the note
        "not a construction drawing"; the GLB carries real model geometry but no structural
        information; budgets repeat their parametric disclaimer inside the file.
      </SafetyNote>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {KINDS.map((entry) => (
          <Panel key={entry.kind} className="flex flex-col justify-between p-4">
            <div>
              <h3 className="text-[14px] font-semibold text-white">{entry.label}</h3>
              <p className="mt-1 text-[12px] text-slate-400">{entry.detail}</p>
            </div>
            <Button className="mt-3 self-start" size="sm" disabled={busy === entry.kind}
              onClick={() => create(entry.kind)}>
              {busy === entry.kind ? "Rendering…" : "Generate & download"}
            </Button>
          </Panel>
        ))}
      </div>

      <Panel className="p-4">
        <SectionTitle>Previous exports ({exports.length})</SectionTitle>
        <ul className="space-y-1.5">
          {exports.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-2 rounded bg-ink-700/60 px-3 py-1.5 text-[12.5px]">
              <span className="text-slate-200">{entry.filename} <Badge>{entry.kind}</Badge>
                <span className="ml-2 text-slate-500">{new Date(entry.created_at).toLocaleString()} · {(entry.size_bytes / 1024).toFixed(0)} KB</span>
              </span>
              <span className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => { window.location.href = entry.url; }}>Download</Button>
                <Button size="sm" variant="subtle" onClick={async () => {
                  await api.delete(`/api/exports/${entry.id}`);
                  await refresh();
                }}>Delete</Button>
              </span>
            </li>
          ))}
          {exports.length === 0 && <li className="text-[13px] text-slate-500">Nothing exported yet.</li>}
        </ul>
      </Panel>
    </div>
  );
}
