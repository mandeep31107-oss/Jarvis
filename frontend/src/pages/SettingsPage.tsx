/** AI provider configuration: honest status, setup instructions per provider,
 *  server-side key storage (never echoed back), connection test. */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Panel, SafetyNote, SectionTitle, inputClass } from "../components/ui";

interface ProvidersPayload {
  status: Record<string, { provider: string; configured: boolean; model?: string; requires_key: boolean }>;
  help: Record<string, { label: string; setup: string[]; fields: string[]; privacy: string }>;
  runtime: Record<string, unknown>;
}

const KEY_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "openai_api_key", label: "OpenAI API key", placeholder: "sk-…" },
  { key: "anthropic_api_key", label: "Anthropic API key", placeholder: "sk-ant-…" },
  { key: "gemini_api_key", label: "Google Gemini API key", placeholder: "AIza…" },
  { key: "stability_api_key", label: "Stability API key", placeholder: "sk-…" },
];

export default function SettingsPage() {
  const [payload, setPayload] = useState<ProvidersPayload | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [choices, setChoices] = useState({ llm_provider: "local", vision_provider: "local", image_provider: "none" });
  const [testing, setTesting] = useState(false);
  const toast = useStore((s) => s.toast);

  const load = () => api.get<ProvidersPayload>("/api/settings/providers").then((p) => {
    setPayload(p);
    api.get<{ llm_provider: string; vision_provider: string; image_provider: string }>("/api/settings")
      .then((current) => setChoices({ ...current }))
      .catch(() => undefined);
  });

  useEffect(() => { load(); }, []);

  const save = async () => {
    const patch: Record<string, unknown> = { ...choices };
    Object.entries(values).forEach(([k, v]) => { if (v) patch[k] = v; });
    await api.put("/api/settings", { values: patch });
    setValues({});
    await load();
    toast("success", "Provider settings saved on the server.");
  };

  const test = async (role: string) => {
    setTesting(true);
    try {
      const result = await api.post<{ ok: boolean; provider: string; message: string }>("/api/settings/test", { role });
      toast(result.ok ? "success" : "warn", `${result.provider}: ${result.message}`);
    } catch (error) {
      toast("error", (error as Error).message);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <SafetyNote>
        Keys are stored only in this server's settings file (permissions 600) and are never sent to
        the browser. The built-in local engine needs no key and never calls the network — geometry,
        explanations, vision notes and command parsing all work offline with it.
      </SafetyNote>

      <Panel className="p-4">
        <SectionTitle>Capability status</SectionTitle>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {payload && Object.entries(payload.status).map(([role, info]) => (
            <div key={role} className="rounded-lg border border-ink-600 bg-ink-700/50 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-medium capitalize text-slate-100">{role.replace(/_/g, " ")}</span>
                <Badge tone={info.configured ? "ok" : "warn"}>{info.configured ? "ready" : "needs key"}</Badge>
              </div>
              <div className="mt-1 text-[12px] text-slate-400">provider: {info.provider}{info.model ? ` · ${info.model}` : ""}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <Button size="sm" variant="ghost" disabled={testing} onClick={() => test("llm")}>Test assistant</Button>
          <Button size="sm" variant="ghost" disabled={testing} onClick={() => test("vision")}>Test vision</Button>
        </div>
      </Panel>

      <Panel className="space-y-3 p-4">
        <SectionTitle>Choose providers</SectionTitle>
        {(["llm_provider", "vision_provider", "image_provider"] as const).map((role) => (
          <label key={role} className="block">
            <span className="mb-1 block text-[12px] text-slate-300 capitalize">{role.replace(/_/g, " ")}</span>
            <select className={inputClass} value={choices[role]}
              onChange={(e) => setChoices((c) => ({ ...c, [role]: e.target.value }))}>
              {role === "image_provider"
                ? ["none", "openai", "gemini", "stability"].map((o) => <option key={o} value={o}>{o}</option>)
                : ["local", "openai", "anthropic", "gemini"].map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
        ))}
        <SectionTitle className="mt-2">API keys (optional)</SectionTitle>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {KEY_FIELDS.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-[12px] text-slate-300">{field.label}
                {payload?.runtime[field.key] ? <Badge tone="ok"> stored</Badge> : null}
              </span>
              <input type="password" className={inputClass} placeholder={field.placeholder}
                value={values[field.key] || ""}
                onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))} />
            </label>
          ))}
        </div>
        <Button onClick={save}>Save provider settings</Button>
      </Panel>

      <Panel className="p-4">
        <SectionTitle>Setup guides</SectionTitle>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {payload && Object.entries(payload.help).map(([key, help]) => (
            <div key={key} className="rounded-lg border border-ink-600 bg-ink-700/40 p-3">
              <h4 className="text-[13px] font-semibold text-slate-100">{help.label}</h4>
              <ol className="mt-1 list-inside list-decimal space-y-0.5 text-[12px] text-slate-400">
                {help.setup.map((line, i) => <li key={i}>{line}</li>)}
              </ol>
              <p className="mt-1 text-[11.5px] text-slate-500">{help.privacy}</p>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
