/** App shell: left navigation, project header, safety banner, toasts and the
 *  WebSocket-driven progress overlay. */

import { useEffect, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { useStore } from "../lib/store";
import { connectProjectStream } from "../lib/ws";
import { Badge } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: "▦" },
  { to: "/setup", label: "Project Setup", icon: "✎" },
  { to: "/studio", label: "AI Design Studio", icon: "✦" },
  { to: "/plan", label: "2D Floor Plan", icon: "⌗" },
  { to: "/viewer", label: "3D House Viewer", icon: "⬢" },
  { to: "/interior", label: "Interior Design", icon: "❖" },
  { to: "/exterior", label: "Exterior Design", icon: "⌂" },
  { to: "/budget", label: "Budget Planner", icon: "₹" },
  { to: "/history", label: "Project History", icon: "↺" },
  { to: "/exports", label: "Export & Reports", icon: "⇩" },
  { to: "/settings", label: "AI Providers", icon: "⚙" },
];

export function Layout({ children }: { children: ReactNode }) {
  const data = useStore((s) => s.data);
  const saving = useStore((s) => s.saving);
  const validation = useStore((s) => s.validation);
  const toasts = useStore((s) => s.toasts);
  const progress = useStore((s) => s.progress);
  const projectId = useStore((s) => s.projectId);
  const set = useStore((s) => s.set);
  const navigate = useNavigate();

  useEffect(() => {
    if (!projectId) return;
    return connectProjectStream(projectId, (event) => {
      if (event.type === "progress") {
        set({ progress: { stage: String(event.stage || ""), percent: Number(event.percent || 0),
          message: String(event.message || "") } });
      } else if (event.type === "done" || event.type === "failed") {
        set({ progress: null });
        if (event.type === "failed") {
          useStore.getState().toast("error", String(event.message || "The operation failed."));
        }
      } else if (event.type === "project_changed") {
        /* live reload handled per page */
      }
    });
  }, [projectId, set]);

  const errors = validation?.counts?.error || 0;

  return (
    <div className="flex h-full">
      <aside className="flex w-56 shrink-0 flex-col border-r border-ink-600 bg-ink-800">
        <button className="flex items-center gap-2 px-4 py-4 text-left" onClick={() => navigate("/")}>
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-accent text-lg font-bold text-white shadow-glow">J</span>
          <span>
            <span className="block text-sm font-semibold text-white">AI Home Designer</span>
            <span className="block text-[11px] text-slate-400">preliminary design aid</span>
          </span>
        </button>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"}
              className={({ isActive }) => clsx(
                "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[13px] transition",
                isActive ? "bg-accent-dim/70 text-white shadow-glow" : "text-slate-300 hover:bg-ink-700")}>
              <span className="w-4 text-center text-accent-soft">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-ink-600 p-3 text-[11px] leading-relaxed text-slate-500">
          Outputs are visualisations, not construction drawings. A licensed architect and
          structural engineer must review anything built from them.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-ink-600 bg-ink-800/80 px-4 py-2.5">
          <h1 className="truncate text-[15px] font-semibold text-white">{data?.name || "No project open"}</h1>
          {data && (
            <>
              <Badge>{data.floors.length} floor(s)</Badge>
              <Badge>{data.metrics.total_built_up_m2.toFixed(0)} m² built-up</Badge>
              <Badge tone={errors ? "error" : "ok"}>{errors ? `${errors} blocking issue(s)` : "checks pass"}</Badge>
            </>
          )}
          <div className="ml-auto flex items-center gap-2 text-[12px] text-slate-400">
            {saving ? <span className="animate-pulse">saving…</span> : <span>saved</span>}
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>

      {progress && (
        <div className="fixed bottom-4 left-1/2 z-50 w-96 -translate-x-1/2 rounded-xl border border-ink-600 bg-ink-800/95 p-3 shadow-panel">
          <div className="mb-1 flex justify-between text-[12px] text-slate-300">
            <span className="capitalize">{progress.stage.replace(/_/g, " ")}</span>
            <span>{progress.percent.toFixed(0)}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded bg-ink-600">
            <div className="h-full bg-accent transition-all" style={{ width: `${progress.percent}%` }} />
          </div>
          <p className="mt-1 text-[11px] text-slate-400">{progress.message}</p>
        </div>
      )}

      <div className="fixed right-4 top-4 z-50 space-y-2">
        {toasts.map((toast) => (
          <div key={toast.id}
            className={clsx("max-w-sm rounded-lg border px-3 py-2 text-[13px] shadow-panel",
              toast.kind === "error" && "border-red-700 bg-red-950/90 text-red-100",
              toast.kind === "warn" && "border-amber-700 bg-amber-950/90 text-amber-100",
              toast.kind === "success" && "border-emerald-700 bg-emerald-950/90 text-emerald-100",
              toast.kind === "info" && "border-ink-600 bg-ink-800/95 text-slate-200")}>
            {toast.text}
          </div>
        ))}
      </div>
    </div>
  );
}
