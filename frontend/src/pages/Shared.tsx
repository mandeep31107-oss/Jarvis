/** Read-only shared view (explicit user-created link only). */

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type { ProjectData } from "../types/project";
import { api } from "../lib/api";
import { Panel, SectionTitle, SafetyNote, Spinner } from "../components/ui";
import { formatArea } from "../lib/units";

export default function Shared() {
  const { token } = useParams();
  const [payload, setPayload] = useState<{ name: string; data: ProjectData } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<{ name: string; data: ProjectData }>(`/api/shared/${token}`)
      .then(setPayload)
      .catch(() => setError("This share link is not active (or was revoked)."));
  }, [token]);

  if (error) return <div className="p-10 text-slate-300">{error}</div>;
  if (!payload) return <div className="p-10"><Spinner label="Opening shared design…" /></div>;

  const data = payload.data;
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <h1 className="text-2xl font-semibold text-white">{payload.name} <span className="text-sm text-slate-400">(shared, read-only)</span></h1>
      <SafetyNote>
        You are viewing a shared preliminary design. It is a visualisation aid — not a construction
        drawing, not structurally certified, not code-compliant by itself.
      </SafetyNote>
      <Panel className="p-4">
        <SectionTitle>At a glance</SectionTitle>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] text-slate-300 md:grid-cols-3">
          <dt className="text-slate-500">Plot</dt>
          <dd>{formatArea(data.plot.width * data.plot.depth, "sqft")} · {data.plot.shape}</dd>
          <dt className="text-slate-500">Built-up</dt><dd>{data.metrics.total_built_up_m2.toFixed(1)} m²</dd>
          <dt className="text-slate-500">Floors</dt><dd>{data.floors.map((f) => f.name).join(", ")}</dd>
          <dt className="text-slate-500">Concept</dt><dd className="capitalize">{(data.active_concept || "—").replace(/_/g, " ")}</dd>
          <dt className="text-slate-500">Open space</dt><dd>{data.metrics.open_space_pct.toFixed(0)}%</dd>
          <dt className="text-slate-500">Style</dt><dd className="capitalize">{data.requirements.style.replace(/_/g, " ")}</dd>
        </dl>
      </Panel>
      {data.floors.map((floor) => (
        <Panel key={floor.id} className="p-4">
          <SectionTitle>{floor.name}</SectionTitle>
          <ul className="grid grid-cols-2 gap-1 text-[12.5px] text-slate-300 md:grid-cols-3">
            {floor.rooms.map((room) => (
              <li key={room.id}>{room.name} — {room.w.toFixed(1)}×{room.d.toFixed(1)} m ({formatArea(room.w * room.d)})</li>
            ))}
          </ul>
        </Panel>
      ))}
    </div>
  );
}
