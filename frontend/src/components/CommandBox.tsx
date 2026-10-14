/** Natural-language command box with preview → approve/reject flow. */

import { useState } from "react";
import type { ProjectData } from "../types/project";
import { api, ApiError } from "../lib/api";
import { useStore } from "../lib/store";
import { Button, Panel, SectionTitle } from "./ui";

interface PreviewPayload {
  status: string;
  preview_id?: string;
  clarification?: string;
  errors?: string[];
  notes?: string[];
  applied?: string[];
  rejected?: string[];
  area_delta_m2?: number;
  changed_rooms?: { id: string; name: string; change: string; delta?: number }[];
  new_issues?: { message: string; severity: string }[];
  warning?: string;
  structural?: boolean;
  candidate?: ProjectData;
  explanation?: string;
  help?: string;
}

export function CommandBox({ compact }: { compact?: boolean }) {
  const projectId = useStore((s) => s.projectId)!;
  const setData = useStore((s) => s.setData);
  const toast = useStore((s) => s.toast);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<PreviewPayload | null>(null);

  const run = async () => {
    if (!text.trim()) return;
    setBusy(true);
    setPreview(null);
    try {
      const payload = await api.post<PreviewPayload>(`/api/projects/${projectId}/command`, { text });
      setPreview(payload);
      if (payload.status === "clarification") toast("info", payload.clarification || "Could you rephrase that?");
      if (payload.status === "rejected") toast("warn", (payload.errors || []).join(" · ") || "That change is not possible as described.");
    } catch (error) {
      toast("error", error instanceof ApiError ? error.message_text : (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!preview?.preview_id) return;
    setBusy(true);
    try {
      const result = await api.post<{ data: ProjectData }>(
        `/api/projects/${projectId}/command/commit`, { preview_id: preview.preview_id });
      await setData(result.data, { label: `command: ${text}` });
      setPreview(null);
      setText("");
      toast("success", "Change applied and saved.");
    } catch (error) {
      toast("error", (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (preview?.preview_id) {
      await api.post(`/api/projects/${projectId}/command/reject`, { preview_id: preview.preview_id }).catch(() => undefined);
    }
    setPreview(null);
  };

  return (
    <Panel className={compact ? "p-2" : "p-3"}>
      {!compact && <SectionTitle>Ask in plain language</SectionTitle>}
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-md border border-ink-600 bg-ink-700 px-3 py-1.5 text-sm text-slate-100 placeholder-slate-500 focus:border-accent focus:outline-none"
          placeholder='e.g. "Make the kitchen more spacious", "Add a study near the master bedroom"'
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
        />
        <Button onClick={run} disabled={busy}>{busy ? "Thinking…" : "Preview"}</Button>
      </div>
      {preview && preview.status === "preview" && (
        <div className="mt-2 rounded-lg border border-accent/40 bg-ink-700/60 p-2 text-[12.5px]">
          <div className="font-medium text-accent-soft">
            {preview.explanation || "Preview ready"}
            {typeof preview.area_delta_m2 === "number" && preview.area_delta_m2 !== 0 &&
              <span className="ml-2 text-slate-300">({preview.area_delta_m2 > 0 ? "+" : ""}{preview.area_delta_m2.toFixed(2)} m²)</span>}
          </div>
          {!!preview.changed_rooms?.length && (
            <ul className="mt-1 list-inside list-disc text-slate-300">
              {preview.changed_rooms.slice(0, 6).map((room) => (
                <li key={room.id}>{room.name}: {room.change}{typeof room.delta === "number" ? ` (${room.delta > 0 ? "+" : ""}${room.delta.toFixed(2)} m²)` : ""}</li>
              ))}
            </ul>
          )}
          {!!preview.notes?.length && <p className="mt-1 text-slate-400">{preview.notes[0]}</p>}
          {!!preview.new_issues?.length && (
            <p className="mt-1 text-amber-300">New issues: {preview.new_issues.map((i) => i.message).join(" · ")}</p>
          )}
          {preview.structural && (
            <p className="mt-1 text-red-300">This touches walls. Which walls carry load is a structural question — an engineer must confirm before anything is built.</p>
          )}
          <div className="mt-2 flex gap-2">
            <Button size="sm" onClick={approve}>Approve & save</Button>
            <Button size="sm" variant="ghost" onClick={reject}>Reject</Button>
          </div>
        </div>
      )}
      {preview && preview.status === "clarification" && (
        <p className="mt-2 text-[12.5px] text-slate-300">{preview.clarification}</p>
      )}
      {preview && preview.status === "rejected" && (
        <div className="mt-2 text-[12.5px] text-amber-300">
          {(preview.errors || []).map((e, i) => <p key={i}>{e}</p>)}
          {(preview.notes || []).slice(0, 2).map((n, i) => <p key={`n${i}`} className="text-slate-400">{n}</p>)}
        </div>
      )}
    </Panel>
  );
}
