/** Project history: saved versions (restore), activity log, share control and
 *  soft-deleted project recovery. */

import { useEffect, useState } from "react";
import type { ProjectData, VersionInfo } from "../types/project";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Badge, Button, Panel, SectionTitle } from "../components/ui";

interface HistoryEvent { at: string; kind: string; message: string }

export default function History() {
  const projectId = useStore((s) => s.projectId)!;
  const versions = useStore((s) => s.versions);
  const loadVersions = useStore((s) => s.loadVersions);
  const loadProject = useStore((s) => s.loadProject);
  const setData = useStore((s) => s.setData);
  const toast = useStore((s) => s.toast);
  const [events, setEvents] = useState<HistoryEvent[]>([]);
  const [sharing, setSharing] = useState<{ enabled: boolean; token: string | null }>({ enabled: false, token: null });

  useEffect(() => {
    loadVersions().catch(() => undefined);
    api.get<{ events: HistoryEvent[] }>(`/api/projects/${projectId}/history`).then((p) => setEvents(p.events)).catch(() => undefined);
    api.get<{ sharing_enabled: boolean; share_token: string | null }>(`/api/projects/${projectId}`)
      .then((p) => setSharing({ enabled: p.sharing_enabled, token: p.share_token })).catch(() => undefined);
  }, [projectId, loadVersions]);

  const restore = async (versionId: string) => {
    if (!window.confirm("Restore this version? Your current edits become a new version first, so nothing is lost.")) return;
    const payload = await api.post<{ data: ProjectData }>(`/api/projects/${projectId}/versions/${versionId}/restore`, {});
    await loadProject(projectId);
    setData(payload.data, { snapshot: false, save: false });
    toast("success", "Version restored.");
  };

  return (
    <div className="grid grid-cols-1 gap-4 p-5 lg:grid-cols-2">
      <Panel className="p-4">
        <div className="flex items-center justify-between">
          <SectionTitle>Saved versions ({versions.length})</SectionTitle>
          <Button size="sm" variant="ghost" onClick={async () => {
            await api.post(`/api/projects/${projectId}/versions`, { label: "checkpoint", note: "Manual checkpoint" });
            await loadVersions();
            toast("success", "Checkpoint saved.");
          }}>Save checkpoint</Button>
        </div>
        <ul className="mt-2 space-y-2">
          {versions.map((version: VersionInfo) => (
            <li key={version.id} className="flex items-center justify-between gap-2 rounded-lg bg-ink-700/60 px-3 py-2">
              <div>
                <div className="text-[13px] text-slate-100">{version.label} <Badge>{version.source}</Badge></div>
                <div className="text-[11.5px] text-slate-500">
                  {new Date(version.created_at).toLocaleString()} · {version.built_up_m2.toFixed(1)} m² · {version.rooms} rooms
                </div>
                {version.note && <div className="text-[11.5px] text-slate-400">{version.note}</div>}
              </div>
              <Button size="sm" variant="ghost" onClick={() => restore(version.id)}>Restore</Button>
            </li>
          ))}
          {versions.length === 0 && <li className="text-[13px] text-slate-500">No versions yet.</li>}
        </ul>
      </Panel>

      <div className="space-y-4">
        <Panel className="p-4">
          <SectionTitle>Sharing (off by default)</SectionTitle>
          {sharing.enabled ? (
            <div className="space-y-2 text-[13px] text-slate-300">
              <p>Read-only link: <code className="text-accent-soft">/shared/{sharing.token}</code></p>
              <Button size="sm" variant="danger" onClick={async () => {
                await api.post(`/api/projects/${projectId}/sharing`, { enabled: false });
                setSharing({ enabled: false, token: null });
                toast("success", "Share link revoked.");
              }}>Revoke link</Button>
            </div>
          ) : (
            <div className="space-y-2 text-[13px] text-slate-400">
              <p>Nothing is public. Create a link only if you want to share this design read-only.</p>
              <Button size="sm" onClick={async () => {
                const payload = await api.post<{ share_token: string }>(`/api/projects/${projectId}/sharing`, { enabled: true });
                setSharing({ enabled: true, token: payload.share_token });
              }}>Create share link</Button>
            </div>
          )}
        </Panel>
        <Panel className="max-h-[420px] overflow-y-auto p-4">
          <SectionTitle>Activity</SectionTitle>
          <ul className="space-y-1 text-[12.5px] text-slate-400">
            {events.slice(0, 60).map((event, i) => (
              <li key={i}><span className="text-slate-500">{new Date(event.at).toLocaleString()}</span> · {event.message}</li>
            ))}
            {events.length === 0 && <li>No activity recorded yet.</li>}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
