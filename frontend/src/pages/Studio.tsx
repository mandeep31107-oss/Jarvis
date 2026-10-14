/** AI Design Studio: concept comparison, assumptions, missing information,
 *  assistant chat, image upload + honest vision analysis, suggestions. */

import { useEffect, useMemo, useRef, useState } from "react";
import type { ChatMessage, ConceptSummary, ProjectData } from "../types/project";
import { api, ApiError } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Panel, SafetyNote, SectionTitle, Spinner, inputClass } from "../components/ui";
import { CommandBox } from "../components/CommandBox";
import { formatArea } from "../lib/units";

export default function Studio() {
  const data = useStore((s) => s.data) as ProjectData;
  const projectId = useStore((s) => s.projectId)!;
  const setData = useStore((s) => s.setData);
  const toast = useStore((s) => s.toast);
  const validation = useStore((s) => s.validation);
  const [missing, setMissing] = useState<{ field: string; question: string; why: string }[]>([]);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [providerNote, setProviderNote] = useState("");
  const [images, setImages] = useState<Record<string, unknown>[]>([]);
  const [suggestions, setSuggestions] = useState<Record<string, unknown>[]>([]);
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<{ questions: typeof missing }>(`/api/projects/${projectId}/missing-information`)
      .then((p) => setMissing(p.questions)).catch(() => undefined);
    api.get<{ messages: ChatMessage[] }>(`/api/projects/${projectId}/chat`)
      .then((p) => setChat(p.messages)).catch(() => undefined);
    api.get<{ images: Record<string, unknown>[] }>(`/api/projects/${projectId}/images`)
      .then((p) => setImages(p.images)).catch(() => undefined);
    api.get<{ alternatives: Record<string, unknown>[] }>(`/api/projects/${projectId}/alternatives`)
      .then((p) => setSuggestions(p.alternatives)).catch(() => undefined);
    useStore.getState().refreshValidation().catch(() => undefined);
  }, [projectId]);

  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [chat]);

  const ask = async () => {
    if (!question.trim()) return;
    setAsking(true);
    try {
      const payload = await api.post<{ reply: string; provider: string; provider_note?: string; messages: ChatMessage[] }>(
        `/api/projects/${projectId}/chat`, { text: question });
      setChat(payload.messages);
      setProviderNote(payload.provider_note || "");
      setQuestion("");
    } catch (error) {
      const detail = (error as ApiError).detail as { message?: string; setup_required?: boolean } | undefined;
      toast(detail?.setup_required ? "warn" : "error", detail?.message || (error as Error).message);
    } finally {
      setAsking(false);
    }
  };

  const activate = async (key: string) => {
    try {
      const payload = await api.post<{ data: ProjectData }>(`/api/projects/${projectId}/concept`, { concept: key });
      await setData(payload.data, { label: `concept ${key}`, snapshot: false });
      toast("success", `Concept "${key.replace(/_/g, " ")}" is now the active design.`);
    } catch (error) {
      toast("error", (error as Error).message);
    }
  };

  const regenerate = async () => {
    try {
      const payload = await api.post<{ data: ProjectData }>(`/api/projects/${projectId}/generate`, {});
      await setData(payload.data, { label: "regenerate", snapshot: false });
      toast("success", "Concepts regenerated from the current brief.");
    } catch (error) {
      toast("error", (error as Error).message);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-[1fr_380px]">
      <div className="space-y-4">
        <Panel className="p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionTitle>Design concepts (all generated from your brief)</SectionTitle>
            <Button size="sm" variant="ghost" onClick={regenerate}>Regenerate</Button>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {(data.concepts as ConceptSummary[]).map((concept) => (
              <div key={concept.key}
                className={`rounded-lg border p-3 ${data.active_concept === concept.key ? "border-accent bg-accent-dim/30 shadow-glow" : "border-ink-600 bg-ink-700/40"}`}>
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold text-white">{concept.label}</h4>
                  {data.active_concept === concept.key && <Badge tone="ok">active</Badge>}
                </div>
                <p className="mt-1 text-[12px] leading-relaxed text-slate-400">{concept.blurb}</p>
                <dl className="mt-2 grid grid-cols-2 gap-x-2 text-[12px] text-slate-300">
                  <dt className="text-slate-500">Built-up</dt>
                  <dd>{concept.metrics.total_built_up_m2.toFixed(1)} m² ({formatArea(concept.metrics.total_built_up_m2, "sqft")})</dd>
                  <dt className="text-slate-500">Coverage</dt><dd>{concept.metrics.ground_coverage_pct.toFixed(0)}%</dd>
                  <dt className="text-slate-500">Open space</dt><dd>{concept.metrics.open_space_pct.toFixed(0)}%</dd>
                  <dt className="text-slate-500">Circulation</dt><dd>{concept.metrics.circulation_pct.toFixed(0)}%</dd>
                </dl>
                <ul className="mt-2 list-inside list-disc text-[11.5px] text-slate-400">
                  {concept.trade_offs.slice(0, 3).map((t, i) => <li key={i}>{t}</li>)}
                </ul>
                {data.active_concept !== concept.key && (
                  <Button size="sm" variant="ghost" className="mt-2" onClick={() => activate(concept.key)}>Use this concept</Button>
                )}
              </div>
            ))}
          </div>
        </Panel>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel className="p-4">
            <SectionTitle>Assumptions the engine made</SectionTitle>
            <ul className="space-y-1.5 text-[12.5px]">
              {data.assumptions.map((a, i) => (
                <li key={i} className={a.severity === "critical" ? "text-red-300" : a.severity === "warning" ? "text-amber-300" : "text-slate-300"}>
                  <Badge tone={a.severity === "critical" ? "error" : a.severity === "warning" ? "warn" : "info"}>{a.topic}</Badge>{" "}
                  {a.statement}
                </li>
              ))}
            </ul>
          </Panel>
          <Panel className="p-4">
            <SectionTitle>Information still missing</SectionTitle>
            {missing.length === 0 ? (
              <p className="text-[13px] text-emerald-300">Nothing material is missing. You can still refine anything.</p>
            ) : (
              <ul className="space-y-2 text-[12.5px] text-slate-300">
                {missing.map((m) => (
                  <li key={m.field}>
                    <strong className="text-slate-100">{m.question}</strong>
                    <span className="block text-slate-500">{m.why}</span>
                  </li>
                ))}
              </ul>
            )}
            <SectionTitle className="mt-3">Automated checks</SectionTitle>
            <p className="text-[12.5px] text-slate-400">
              {validation ? `${validation.counts.error || 0} blocking · ${validation.counts.warning || 0} warnings · ${validation.counts.info || 0} notes. ${validation.disclaimer}` : "…"}
            </p>
          </Panel>
        </div>

        <Panel className="p-4">
          <SectionTitle>Photos (optional) — upload, analyse, redesign</SectionTitle>
          <ImagePanel images={images} refresh={() => api.get<{ images: Record<string, unknown>[] }>(`/api/projects/${projectId}/images`).then((p) => setImages(p.images))} />
        </Panel>
      </div>

      <div className="space-y-4">
        <CommandBox />
        <Panel className="flex max-h-[520px] flex-col p-3">
          <SectionTitle>Design assistant</SectionTitle>
          {providerNote && <p className="mb-2 rounded bg-ink-700 p-2 text-[11.5px] text-amber-200">{providerNote}</p>}
          <div className="flex-1 space-y-2 overflow-y-auto pr-1">
            {chat.length === 0 && (
              <p className="text-[12.5px] text-slate-500">
                Ask anything about this project — sizes, costs, why a room is where it is. The assistant
                only uses the project's real numbers and will say when something needs a professional.
              </p>
            )}
            {chat.map((message) => (
              <div key={message.id} className={`rounded-lg px-2.5 py-1.5 text-[12.5px] leading-relaxed ${message.role === "user" ? "bg-accent-dim/50 text-slate-100" : "bg-ink-700/70 text-slate-200"}`}>
                {message.content}
              </div>
            ))}
            <div ref={chatEnd} />
          </div>
          <div className="mt-2 flex gap-2">
            <input className={inputClass} placeholder="Ask about your design…" value={question}
              onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} />
            <Button onClick={ask} disabled={asking}>{asking ? "…" : "Ask"}</Button>
          </div>
        </Panel>
        <Panel className="p-3">
          <SectionTitle>Suggested improvements</SectionTitle>
          <ul className="space-y-1.5 text-[12.5px] text-slate-300">
            {suggestions.slice(0, 6).map((s, i) => (
              <li key={i}>• {String(s.title || s.message || JSON.stringify(s))}</li>
            ))}
            {suggestions.length === 0 && <li className="text-slate-500">No open suggestions.</li>}
          </ul>
        </Panel>
        <SafetyNote>
          The language model explains and edits — it never generates geometry or prices. All numbers you
          see come from the deterministic engine and the parametric budget tables.
        </SafetyNote>
      </div>
    </div>
  );
}

function ImagePanel({ images, refresh }: { images: Record<string, unknown>[]; refresh: () => Promise<void> }) {
  const projectId = useStore((s) => s.projectId)!;
  const toast = useStore((s) => s.toast);
  const input = useRef<HTMLInputElement>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Record<string, unknown> | null>(null);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    form.append("room_type", "other");
    try {
      const result = await api.upload<{ errors: string[] }>(`/api/projects/${projectId}/images`, form);
      if (result.errors?.length) toast("warn", result.errors.join(" · "));
      await refresh();
    } catch (error) {
      toast("error", (error as Error).message);
    }
  };

  const analyse = async (id: string) => {
    setBusyId(id);
    try {
      const payload = await api.post<{ analysis: Record<string, unknown>; provider_note?: string }>(
        `/api/projects/${projectId}/images/${id}/analyse`, {});
      setAnalysis(payload.analysis);
      if (payload.provider_note) toast("info", payload.provider_note);
      await refresh();
    } catch (error) {
      const detail = (error as ApiError).detail as { message?: string } | undefined;
      toast("warn", detail?.message || (error as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-3">
      <input ref={input} type="file" multiple accept="image/jpeg,image/png,image/webp" hidden
        onChange={(e) => upload(e.target.files)} />
      <Button size="sm" variant="ghost" onClick={() => input.current?.click()}>Upload images…</Button>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {images.map((image) => (
          <div key={String(image.id)} className="overflow-hidden rounded-lg border border-ink-600 bg-ink-700/40">
            <img src={`/api/images/${String(image.id)}/thumb`} alt={String(image.filename)} className="h-28 w-full object-cover" />
            <div className="space-y-1 p-2">
              <div className="truncate text-[11px] text-slate-300">{String(image.filename)}</div>
              <div className="flex gap-1">
                <Button size="sm" variant="ghost" disabled={busyId === String(image.id)} onClick={() => analyse(String(image.id))}>
                  {busyId === String(image.id) ? "…" : "Analyse"}
                </Button>
                <Button size="sm" variant="subtle" onClick={async () => {
                  try {
                    await api.post(`/api/projects/${projectId}/images/${String(image.id)}/redesign`, {});
                    toast("success", "Redesign image ready in the image list.");
                  } catch (error) {
                    const detail = (error as ApiError).detail as { message?: string } | undefined;
                    toast("warn", detail?.message || (error as Error).message);
                  }
                }}>Redesign</Button>
              </div>
            </div>
          </div>
        ))}
        {images.length === 0 && (
          <p className="col-span-full text-[12.5px] text-slate-500">
            No photos yet — that is completely fine. The design engine never needs them.
          </p>
        )}
      </div>
      {analysis && (
        <div className="rounded-lg border border-ink-600 bg-ink-700/50 p-3 text-[12.5px] text-slate-300">
          <SectionTitle>Vision analysis (pixel-derived facts only)</SectionTitle>
          <p className="text-amber-200">{String((analysis as { disclaimer?: string }).disclaimer || "")}</p>
          <ul className="mt-1 list-inside list-disc">
            {((analysis as { findings?: { label: string; value: string; detail: string }[] }).findings || []).slice(0, 8).map((f, i) => (
              <li key={i}><strong>{f.label}:</strong> {f.value} — {f.detail}</li>
            ))}
          </ul>
          <p className="mt-1 text-slate-400">
            {String((analysis as { structural_note?: string }).structural_note || "")}
          </p>
        </div>
      )}
    </div>
  );
}
