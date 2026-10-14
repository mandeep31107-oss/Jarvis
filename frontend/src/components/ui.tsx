/** Small design-system primitives shared by every page. */

import type { ReactNode } from "react";
import clsx from "clsx";

export function Button({ children, onClick, variant = "primary", size = "md", disabled, title, type, className }:
  { children: ReactNode; onClick?: () => void; variant?: "primary" | "ghost" | "danger" | "subtle";
    size?: "sm" | "md" | "lg"; disabled?: boolean; title?: string; type?: "button" | "submit";
    className?: string }) {
  return (
    <button type={type || "button"} title={title} disabled={disabled} onClick={onClick}
      className={clsx(
        "rounded-md font-medium transition focus:outline-none focus:ring-2 focus:ring-accent/60",
        size === "sm" && "px-2.5 py-1 text-xs",
        size === "md" && "px-3.5 py-1.5 text-sm",
        size === "lg" && "px-5 py-2.5 text-base",
        variant === "primary" && "bg-accent text-white hover:bg-accent-soft disabled:bg-ink-600",
        variant === "ghost" && "bg-ink-700 text-slate-200 hover:bg-ink-600 disabled:text-slate-500",
        variant === "subtle" && "bg-transparent text-accent-soft hover:bg-ink-700",
        variant === "danger" && "bg-red-800/80 text-red-50 hover:bg-red-700",
        disabled && "cursor-not-allowed opacity-60",
        className,
      )}>
      {children}
    </button>
  );
}

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx("rounded-xl border border-ink-600 bg-ink-800/90 text-slate-200 shadow-panel backdrop-blur", className)}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <h3 className={clsx("mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400", className)}>
      {children}
    </h3>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[12px] font-medium text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-md border border-ink-600 bg-ink-700 px-2.5 py-1.5 text-sm text-slate-100 placeholder-slate-500 focus:border-accent focus:outline-none";

export function Toggle({ label, checked, onChange, small }:
  { label: string; checked: boolean; onChange: (v: boolean) => void; small?: boolean }) {
  return (
    <button onClick={() => onChange(!checked)}
      className={clsx("flex items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-ink-700",
        small ? "text-[11px]" : "text-sm")}>
      <span className={clsx("relative inline-flex h-4 w-7 shrink-0 rounded-full transition",
        checked ? "bg-accent" : "bg-ink-600")}>
        <span className={clsx("absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all",
          checked ? "left-3.5" : "left-0.5")} />
      </span>
      <span className="text-slate-300">{label}</span>
    </button>
  );
}

export function Slider({ label, value, min, max, step, onChange, format }:
  { label: string; value: number; min: number; max: number; step: number;
    onChange: (v: number) => void; format?: (v: number) => string }) {
  return (
    <label className="block">
      <span className="mb-0.5 flex justify-between text-[11px] text-slate-400">
        <span>{label}</span>
        <span className="text-slate-300">{format ? format(value) : value.toFixed(2)}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[#3f7fd6]" />
    </label>
  );
}

export function Badge({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "error" | "ok" }) {
  return (
    <span className={clsx("rounded-full px-2 py-0.5 text-[11px] font-medium",
      tone === "info" && "bg-accent-dim/60 text-accent-soft",
      tone === "warn" && "bg-amber-900/50 text-amber-200",
      tone === "error" && "bg-red-900/60 text-red-200",
      tone === "ok" && "bg-emerald-900/50 text-emerald-200")}>
      {children}
    </span>
  );
}

export function EmptyState({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-ink-600 bg-ink-800/50 p-10 text-center">
      <div className="text-3xl">🏠</div>
      <h3 className="text-lg font-semibold text-slate-200">{title}</h3>
      <p className="max-w-md text-sm text-slate-400">{body}</p>
      {action}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      {label || "Working…"}
    </div>
  );
}

export function SafetyNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-700/50 bg-amber-900/20 p-3 text-[12.5px] leading-relaxed text-amber-100/90">
      {children}
    </div>
  );
}
