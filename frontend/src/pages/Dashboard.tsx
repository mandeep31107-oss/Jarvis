import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ProjectRecord } from "../types/project";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, EmptyState, Panel, SafetyNote, SectionTitle, Spinner } from "../components/ui";
import { formatArea } from "../lib/units";

export default function Dashboard() {
  const [projects, setProjects] = useState<ProjectRecord[] | null>(null);
  const [authMode, setAuthMode] = useState<string>("");
  const loadList = useStore((s) => s.loadList);
  const loadProject = useStore((s) => s.loadProject);
  const navigate = useNavigate();

  useEffect(() => {
    loadList().then(setProjects).catch(() => setProjects([]));
    api.get<{ mode: string }>("/api/auth/status").then((s) => setAuthMode(s.mode)).catch(() => undefined);
  }, [loadList]);

  const open = async (id: string) => {
    await loadProject(id);
    navigate("/studio");
  };

  return (
    <div className="space-y-5 p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold text-white">Your house projects</h2>
          <p className="mt-1 text-sm text-slate-400">
            {authMode === "local-single-user"
              ? "Local single-user mode: everything stays on this machine — no account or internet needed."
              : "Signed-in mode: projects belong to your account."}
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => navigate("/setup")}>New project</Button>
          <Button variant="ghost" onClick={() => navigate("/setup?sample=1")}>Try a sample plot</Button>
        </div>
      </div>

      <SafetyNote>
        This tool produces <strong>preliminary design visualisations and parametric estimates</strong>.
        A realistic model is not a safe or legal one: foundations, structure, electrical and plumbing
        always need a licensed professional and municipal sanction.
      </SafetyNote>

      {projects === null ? <Spinner label="Loading projects…" /> :
        projects.length === 0 ? (
          <EmptyState title="No designs yet"
            body="Start from your plot dimensions — or just a photo of your current home, or both. Every step is skippable and images are always optional."
            action={<Button onClick={() => navigate("/setup")}>Create your first design</Button>} />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {projects.map((project) => (
              <Panel key={project.id} className="p-4 transition hover:border-accent/60">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-[15px] font-semibold text-white">{project.name}</h3>
                    <p className="text-[12px] text-slate-400">
                      {project.data.plot.city || "Location not set"} · {project.data.plot.shape.replace("_", " ")} plot
                    </p>
                  </div>
                  <Badge tone={(project.validation?.counts?.error || 0) ? "error" : "ok"}>
                    {project.validation?.counts?.error || 0} issues
                  </Badge>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[12.5px] text-slate-300">
                  <dt className="text-slate-500">Plot</dt>
                  <dd>{formatArea(project.data.plot.width * project.data.plot.depth, "sqft")}</dd>
                  <dt className="text-slate-500">Built-up</dt>
                  <dd>{project.data.metrics.total_built_up_m2.toFixed(0)} m²</dd>
                  <dt className="text-slate-500">Floors</dt>
                  <dd>{project.data.floors.length}</dd>
                  <dt className="text-slate-500">Concept</dt>
                  <dd className="capitalize">{(project.data.active_concept || "—").replace(/_/g, " ")}</dd>
                </dl>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => open(project.id)}>Open</Button>
                  <Button size="sm" variant="ghost" onClick={() => { open(project.id).then(() => navigate("/viewer")); }}>
                    3D view
                  </Button>
                  <Button size="sm" variant="subtle" onClick={async () => {
                    if (window.confirm(`Delete "${project.name}"? You can restore it from History for a while.`)) {
                      await api.delete(`/api/projects/${project.id}`);
                      setProjects((list) => (list || []).filter((p) => p.id !== project.id));
                    }
                  }}>Delete</Button>
                </div>
              </Panel>
            ))}
          </div>
        )}

      <Panel className="p-4">
        <SectionTitle>What this app does (and does not) do</SectionTitle>
        <ul className="grid grid-cols-1 gap-2 text-[13px] text-slate-300 md:grid-cols-2">
          <li>• Deterministic layout engine generates real room geometry — never a stock image.</li>
          <li>• Interactive 2D editor and a true first-person 3D walkthrough of your own plan.</li>
          <li>• Parametric budget ranges with visible assumptions and manual rate entry.</li>
          <li>• AI assistance that explains and edits — it never invents geometry or prices.</li>
          <li>• Exports: PNG/SVG/PDF plans, report PDF, GLB model, CSV schedules, documented JSON.</li>
          <li>• Not a construction document; not structural, legal or code certification.</li>
        </ul>
      </Panel>
    </div>
  );
}
