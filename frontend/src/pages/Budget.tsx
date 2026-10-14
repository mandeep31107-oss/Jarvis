/** Budget planner: 14 categories with ranges, assumptions and rate sources,
 *  manual rate entry, finish levels, concept comparison and honest disclaimers. */

import { useEffect, useState } from "react";
import type { BudgetComparison, BudgetEstimate, ProjectData } from "../types/project";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Panel, SafetyNote, SectionTitle, inputClass } from "../components/ui";
import { formatMoney } from "../lib/units";

export default function Budget() {
  const data = useStore((s) => s.data) as ProjectData;
  const projectId = useStore((s) => s.projectId)!;
  const budget = useStore((s) => s.budget);
  const refreshBudget = useStore((s) => s.refreshBudget);
  const mutate = useStore((s) => s.mutate);
  const toast = useStore((s) => s.toast);
  const [comparison, setComparison] = useState<BudgetComparison[] | null>(null);
  const [rates, setRates] = useState<Record<string, string>>({});

  useEffect(() => {
    refreshBudget().catch(() => undefined);
    api.get<{ comparison: BudgetComparison[] }>(`/api/projects/${projectId}/budget/compare`)
      .then((p) => setComparison(p.comparison)).catch(() => undefined);
  }, [projectId, refreshBudget, data.budget.finish_level]);

  if (!budget) return <div className="p-8 text-slate-400">Calculating estimate…</div>;

  const saveRates = async () => {
    const overrides: Record<string, number> = {};
    Object.entries(rates).forEach(([key, value]) => {
      const parsed = Number(value);
      if (value && Number.isFinite(parsed)) overrides[key] = parsed;
    });
    await api.post(`/api/projects/${projectId}/budget/rates`, { overrides, verified: true,
      basis: "user-entered local rates" });
    await mutate((draft) => { draft.budget.custom_rates = overrides; draft.budget.verified = true; }, "manual rates");
    await refreshBudget();
    toast("success", "Your rates are now used for this project's estimate.");
  };

  return (
    <div className="space-y-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-white">Indicative budget estimate</h2>
          <p className="text-[13px] text-slate-400">
            {formatMoney(budget.total_low, budget.currency)} – {formatMoney(budget.total_high, budget.currency)}
            {" "}(mid {formatMoney(budget.total_mid, budget.currency)}) ·{" "}
            {Math.round(budget.rate_per_sqft_low).toLocaleString("en-IN")}–{Math.round(budget.rate_per_sqft_high).toLocaleString("en-IN")}/sq ft ·{" "}
            {budget.built_up_m2.toFixed(1)} m² built-up
          </p>
        </div>
        <div className="flex gap-2">
          {(["economical", "standard", "premium"] as const).map((level) => (
            <Button key={level} size="sm" variant={data.budget.finish_level === level ? "primary" : "ghost"}
              onClick={async () => {
                await mutate((d) => { d.budget.finish_level = level; d.requirements.finish_level = level; }, `finish ${level}`);
                await refreshBudget();
              }}>{level}</Button>
          ))}
        </div>
      </div>

      <SafetyNote>
        {budget.disclaimer} Rates are parametric placeholders for {budget.city_tier} India, not live
        market prices. Enter your own rates below to make the estimate yours — or treat the range as
        a planning ball-park only.
      </SafetyNote>

      <Panel className="overflow-x-auto p-0">
        <table className="w-full text-left text-[12.5px]">
          <thead className="bg-ink-700/70 text-slate-300">
            <tr>
              {["Category", "Quantity", "Rate low–high", "Amount low", "Amount high", "Verified", "Your rate"].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {budget.lines.map((line) => (
              <tr key={line.category} className="border-t border-ink-700 text-slate-300">
                <td className="px-3 py-1.5">
                  <div className="font-medium text-slate-100">{line.label}</div>
                  <div className="text-[11px] text-slate-500">{line.source}{line.note ? ` · ${line.note}` : ""}</div>
                </td>
                <td className="px-3 py-1.5">{line.quantity.toFixed(1)} {line.unit}</td>
                <td className="px-3 py-1.5">{Math.round(line.rate_low).toLocaleString("en-IN")}–{Math.round(line.rate_high).toLocaleString("en-IN")}</td>
                <td className="px-3 py-1.5">{formatMoney(line.amount_low, budget.currency)}</td>
                <td className="px-3 py-1.5">{formatMoney(line.amount_high, budget.currency)}</td>
                <td className="px-3 py-1.5">{line.verified ? <Badge tone="ok">yes</Badge> : <Badge tone="warn">no</Badge>}</td>
                <td className="px-3 py-1.5">
                  <input className={`${inputClass} w-24`} placeholder="₹/unit"
                    value={rates[line.category] ?? (data.budget.custom_rates[line.category] ?? "")}
                    onChange={(e) => setRates((r) => ({ ...r, [line.category]: e.target.value }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <div className="flex gap-2">
        <Button onClick={saveRates}>Use my rates</Button>
        <Button variant="ghost" onClick={() => window.open(`/api/projects/${projectId}/exports`, "_self")}>
          Export budget CSV
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel className="p-4">
          <SectionTitle>Estimate assumptions</SectionTitle>
          <ul className="list-inside list-disc space-y-1 text-[12.5px] text-slate-300">
            {budget.assumptions.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
          {budget.warnings.length > 0 && (
            <ul className="mt-2 list-inside list-disc text-[12.5px] text-amber-300">
              {budget.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
        </Panel>
        <Panel className="p-4">
          <SectionTitle>Compare concepts (cost vs space)</SectionTitle>
          {comparison && (
            <table className="w-full text-left text-[12px]">
              <thead className="text-slate-400">
                <tr><th className="py-1">Concept</th><th>Built-up</th><th>Mid cost</th><th>₹/sq ft</th><th>Open space</th></tr>
              </thead>
              <tbody className="text-slate-300">
                {comparison.map((row) => (
                  <tr key={row.key} className={row.key === data.active_concept ? "text-accent-soft" : ""}>
                    <td className="py-1 capitalize">{row.label}{row.key === data.active_concept ? " ●" : ""}</td>
                    <td>{row.built_up_m2.toFixed(0)} m²</td>
                    <td>{formatMoney(row.total_mid, budget.currency)}</td>
                    <td>{Math.round(row.rate_per_sqft_low)}–{Math.round(row.rate_per_sqft_high)}</td>
                    <td>{row.open_space_pct.toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="mt-2 text-[11.5px] text-slate-500">
            Comparing options is safe; none of these numbers is a quotation or a sanction-ready cost.
          </p>
        </Panel>
      </div>
    </div>
  );
}
