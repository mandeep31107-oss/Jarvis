/** Progressive setup wizard: plot, programme, household preferences, optional
 *  photos (always optional), review. Every optional question is skippable and
 *  "I am not sure — help me decide" explains what the choice changes. */

import { useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { Plot, Requirements } from "../types/project";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Button, Field, Panel, SafetyNote, SectionTitle, Toggle, inputClass } from "../components/ui";
import { parseDimension, formatArea } from "../lib/units";

const STYLES = ["modern", "contemporary", "minimalist", "traditional_indian", "scandinavian",
  "industrial", "luxury", "rustic", "mediterranean", "japanese", "budget_modern"];

const PRESETS: { key: string; label: string; patch: Partial<Requirements>; blurb: string }[] = [
  { key: "compact_urban", label: "Compact urban home", blurb: "2 floors, tight footprint, parking for one car.", patch: { floors: 2, bedrooms: 2, bathrooms: 2, wants_parking: "one_car", wants_utility: true } },
  { key: "independent", label: "Independent house", blurb: "Single floor plus terrace, garden and parking.", patch: { floors: 1, bedrooms: 3, bathrooms: 2, wants_terrace: true, wants_garden: true, wants_parking: "one_car" } },
  { key: "village", label: "Village home", blurb: "Single floor, courtyard, storage and space for animals later.", patch: { floors: 1, bedrooms: 2, bathrooms: 1, wants_courtyard: true, wants_store: true, future_expansion: "One more bedroom block later" } },
  { key: "duplex", label: "Duplex", blurb: "Two full floors with an internal stair and balcony.", patch: { floors: 2, bedrooms: 4, bathrooms: 3, attached_baths: 1, wants_balcony: true } },
  { key: "small_family", label: "Small family", blurb: "3 bedrooms, modularity first, budget conscious.", patch: { floors: 2, bedrooms: 3, bathrooms: 2, finish_level: "economical" } },
  { key: "joint_family", label: "Joint family", blurb: "4+ bedrooms, two living areas, puja and plenty of storage.", patch: { floors: 2, bedrooms: 5, bathrooms: 4, attached_baths: 2, wants_pooja: true, wants_store: true, wants_living: true, wants_dining: true } },
  { key: "shop", label: "Shop + workspace", blurb: "Ground floor shop facing the road, home above.", patch: { floors: 2, bedrooms: 2, bathrooms: 2, wants_shop: true } },
  { key: "terrace_home", label: "Terrace + balcony home", blurb: "Outdoor living: big terrace, balconies, garden.", patch: { floors: 2, bedrooms: 3, bathrooms: 3, wants_terrace: true, wants_balcony: true, wants_garden: true } },
];

const HELP: Record<string, string> = {
  floors: "More floors fit a bigger programme on a small plot but add stair area (about 8–12 m² per floor) and cost. Single floor is cheaper and step-free.",
  setbacks: "Setbacks are the gaps your municipality forces between the building and the plot boundary. We assume typical values and flag them; your local byelaws decide the real numbers.",
  road_side: "The road side drives the main entrance, parking and which rooms get street light. Vastu and privacy choices hang off it too.",
  attached_baths: "An attached bath adds privacy but needs a plumbing shaft next to the bedroom; shared baths keep plumbing together and cost less.",
  parking: "Open parking is cheap and simple; a covered garage costs like built-up area. One car needs about 3.0 × 5.0 m including manoeuvring.",
  vastu: "Vastu guidance is a cultural preference layer (kitchen SE, master SW, puja NE). It never overrides safety, code or your brief.",
  finish_level: "Economical ≈ basic finishes and local materials; standard ≈ branded mid-range; premium ≈ imported stone, wood and fixtures. This scales the budget estimate, not the geometry.",
};

interface FormState {
  name: string;
  startWith: "dims" | "images" | "both";
  unit: "ft" | "m";
  width: string; depth: string;
  shape: Plot["shape"]; road_side: Plot["road_side"]; road_width: string;
  country: string; state: string; city: string; city_tier: Plot["city_tier"]; climate: Plot["climate"];
  req: Requirements;
}

const DEFAULT_REQ: Requirements = {
  floors: 2, bedrooms: 3, bathrooms: 3, attached_baths: 1, kitchen_type: "modular",
  wants_living: true, wants_dining: true, wants_pooja: false, wants_study: false,
  wants_store: true, wants_utility: true, wants_parking: "one_car", wants_balcony: true,
  wants_terrace: true, wants_garden: false, wants_courtyard: false, wants_servant_room: false,
  wants_shop: false, family_size: 4, accessibility: false, style: "modern", vastu: false,
  budget_target: null, currency: "INR", finish_level: "standard", preferred_materials: [],
  preferred_colors: [], future_expansion: "", extra_notes: "", concept: "family_comfort",
};

export default function Setup() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const toast = useStore((s) => s.toast);
  const [step, setStep] = useState(0);
  const [help, setHelp] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [form, setForm] = useState<FormState>({
    name: params.get("sample") ? "Sample Independent House" : "My House Project",
    startWith: params.get("sample") ? "dims" : "dims",
    unit: "ft", width: params.get("sample") ? "30" : "", depth: params.get("sample") ? "50" : "",
    shape: "rectangular", road_side: "S", road_width: "",
    country: "India", state: "", city: "", city_tier: "tier2", climate: "composite",
    req: { ...DEFAULT_REQ },
  });
  const fileInput = useRef<HTMLInputElement>(null);

  const patchReq = (patch: Partial<Requirements>) => setForm((f) => ({ ...f, req: { ...f.req, ...patch } }));
  const areaM2 = useMemo(() => {
    const w = parseDimension(form.width) ?? 0;
    const d = parseDimension(form.depth) ?? 0;
    const wm = form.unit === "ft" ? w * 0.3048 : w;
    const dm = form.unit === "ft" ? d * 0.3048 : d;
    return wm * dm;
  }, [form.width, form.depth, form.unit]);

  const steps = ["Start", "Plot", "Rooms & programme", "Household & style", "Photos (optional)", "Review & generate"];

  const submit = async () => {
    setBusy(true);
    try {
      const widthRaw = parseDimension(form.width);
      const depthRaw = parseDimension(form.depth);
      if (!widthRaw || !depthRaw) throw new Error("Enter the plot length and width (numbers only is fine).");
      const plot: Partial<Plot> = {
        width: form.unit === "ft" ? widthRaw * 0.3048 : widthRaw,
        depth: form.unit === "ft" ? depthRaw * 0.3048 : depthRaw,
        input_width: widthRaw, input_depth: depthRaw, input_unit: form.unit,
        shape: form.shape, road_side: form.road_side,
        road_width_m: form.road_width ? Number(form.road_width) : null,
        country: form.country, state: form.state, city: form.city,
        city_tier: form.city_tier, climate: form.climate,
      };
      const created = await api.post<{ id: string }>("/api/projects", {
        name: form.name || "My House Project", plot, requirements: form.req,
      });
      if (files.length) {
        const fd = new FormData();
        files.forEach((file) => fd.append("files", file));
        fd.append("room_type", "other");
        await api.upload(`/api/projects/${created.id}/images`, fd);
      }
      await useStore.getState().loadProject(created.id);
      toast("success", "Concepts generated. Open the AI Design Studio to compare them.");
      navigate("/studio");
    } catch (error) {
      const detail = (error as { detail?: unknown }).detail;
      toast("error", typeof detail === "string" ? detail : (error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <div className="flex flex-wrap gap-1">
        {steps.map((label, index) => (
          <button key={label} onClick={() => setStep(index)}
            className={`rounded-full px-3 py-1 text-[12px] ${index === step ? "bg-accent text-white" : index < step ? "bg-accent-dim text-accent-soft" : "bg-ink-700 text-slate-400"}`}>
            {index + 1}. {label}
          </button>
        ))}
      </div>

      {help && (
        <Panel className="border-accent/50 p-3 text-[13px] text-slate-200">
          <strong className="text-accent-soft">Help me decide:</strong> {HELP[help]}
          <button className="ml-2 text-slate-400 hover:text-white" onClick={() => setHelp(null)}>close</button>
        </Panel>
      )}

      {step === 0 && (
        <Panel className="space-y-4 p-5">
          <SectionTitle>How do you want to start?</SectionTitle>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {([["dims", "Plot dimensions", "Length × width plus your room wishlist. The engine does the rest."],
               ["images", "Photos only", "Upload photos of your current home; we analyse what is visible and build a brief from it."],
               ["both", "Both", "Dimensions plus photos — best accuracy for redesigns of existing homes."]] as const).map(([key, label, blurb]) => (
              <button key={key} onClick={() => setForm((f) => ({ ...f, startWith: key }))}
                className={`rounded-lg border p-3 text-left transition ${form.startWith === key ? "border-accent bg-accent-dim/40" : "border-ink-600 bg-ink-700/50 hover:border-accent/50"}`}>
                <div className="text-sm font-semibold text-white">{label}</div>
                <div className="mt-1 text-[12px] text-slate-400">{blurb}</div>
              </button>
            ))}
          </div>
          <Field label="Project name">
            <input className={inputClass} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          </Field>
          <SafetyNote>Photos are optional at every step and are never sent anywhere unless you press Analyse or Redesign later.</SafetyNote>
        </Panel>
      )}

      {step === 1 && (
        <Panel className="space-y-4 p-5">
          <SectionTitle>Plot</SectionTitle>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label={`Length (${form.unit})`}>
              <input className={inputClass} value={form.width} placeholder={form.unit === "ft" ? "50" : "15.2"}
                onChange={(e) => setForm((f) => ({ ...f, width: e.target.value }))} />
            </Field>
            <Field label={`Width (${form.unit})`}>
              <input className={inputClass} value={form.depth} placeholder={form.unit === "ft" ? "30" : "9.1"}
                onChange={(e) => setForm((f) => ({ ...f, depth: e.target.value }))} />
            </Field>
            <Field label="Units">
              <select className={inputClass} value={form.unit} onChange={(e) => setForm((f) => ({ ...f, unit: e.target.value as "ft" | "m" }))}>
                <option value="ft">feet</option><option value="m">metres</option>
              </select>
            </Field>
            <Field label="Shape">
              <select className={inputClass} value={form.shape} onChange={(e) => setForm((f) => ({ ...f, shape: e.target.value as Plot["shape"] }))}>
                <option value="rectangular">rectangular</option><option value="square">square</option>
                <option value="l_shape">L-shape</option><option value="irregular">irregular</option>
                <option value="custom">custom</option>
              </select>
            </Field>
            <Field label="Road side">
              <select className={inputClass} value={form.road_side} onChange={(e) => setForm((f) => ({ ...f, road_side: e.target.value as Plot["road_side"] }))}>
                <option value="S">South</option><option value="N">North</option>
                <option value="E">East</option><option value="W">West</option>
              </select>
            </Field>
            <Field label="Road width (m, optional)" hint="Leave empty if unknown — we will ask later.">
              <input className={inputClass} value={form.road_width} onChange={(e) => setForm((f) => ({ ...f, road_width: e.target.value }))} />
            </Field>
            <Field label="City"><input className={inputClass} value={form.city} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} /></Field>
            <Field label="State"><input className={inputClass} value={form.state} onChange={(e) => setForm((f) => ({ ...f, state: e.target.value }))} /></Field>
            <Field label="City tier">
              <select className={inputClass} value={form.city_tier} onChange={(e) => setForm((f) => ({ ...f, city_tier: e.target.value as Plot["city_tier"] }))}>
                <option value="metro">metro</option><option value="tier2">tier 2</option>
                <option value="tier3">tier 3</option><option value="rural">rural</option>
              </select>
            </Field>
            <Field label="Climate">
              <select className={inputClass} value={form.climate} onChange={(e) => setForm((f) => ({ ...f, climate: e.target.value as Plot["climate"] }))}>
                <option value="composite">composite</option><option value="hot_dry">hot & dry</option>
                <option value="hot_humid">hot & humid</option><option value="temperate">temperate</option>
                <option value="cold">cold</option>
              </select>
            </Field>
          </div>
          {areaM2 > 0 && (
            <p className="text-[13px] text-slate-400">
              Plot area ≈ <strong className="text-slate-200">{formatArea(areaM2, "sqft")}</strong> ({areaM2.toFixed(1)} m²).
              Setbacks will be assumed and flagged — never treated as verified byelaw values.
            </p>
          )}
          <Button variant="subtle" onClick={() => setHelp("setbacks")}>I am not sure — help me decide</Button>
        </Panel>
      )}

      {step === 2 && (
        <Panel className="space-y-4 p-5">
          <SectionTitle>Quick presets</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {PRESETS.map((preset) => (
              <button key={preset.key} title={preset.blurb}
                onClick={() => patchReq(preset.patch)}
                className="rounded-full border border-ink-600 bg-ink-700/60 px-3 py-1 text-[12px] text-slate-200 hover:border-accent">
                {preset.label}
              </button>
            ))}
          </div>
          <SectionTitle>Programme</SectionTitle>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Floors"><input type="number" min={1} max={4} className={inputClass} value={form.req.floors}
              onChange={(e) => patchReq({ floors: Number(e.target.value) })} /></Field>
            <Field label="Bedrooms"><input type="number" min={1} max={8} className={inputClass} value={form.req.bedrooms}
              onChange={(e) => patchReq({ bedrooms: Number(e.target.value) })} /></Field>
            <Field label="Bathrooms"><input type="number" min={1} max={8} className={inputClass} value={form.req.bathrooms}
              onChange={(e) => patchReq({ bathrooms: Number(e.target.value) })} /></Field>
            <Field label="…of which attached"><input type="number" min={0} max={6} className={inputClass} value={form.req.attached_baths}
              onChange={(e) => patchReq({ attached_baths: Number(e.target.value) })} /></Field>
            <Field label="Kitchen">
              <select className={inputClass} value={form.req.kitchen_type} onChange={(e) => patchReq({ kitchen_type: e.target.value as Requirements["kitchen_type"] })}>
                <option value="modular">modular</option><option value="open">open</option>
                <option value="closed">closed</option><option value="platform_only">platform only</option>
              </select>
            </Field>
            <Field label="Parking">
              <select className={inputClass} value={form.req.wants_parking} onChange={(e) => patchReq({ wants_parking: e.target.value as Requirements["wants_parking"] })}>
                <option value="none">none</option><option value="two_wheeler">two-wheeler</option>
                <option value="one_car">one car</option><option value="two_car">two cars</option>
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-1 md:grid-cols-3">
            <Toggle label="Living room" checked={form.req.wants_living} onChange={(v) => patchReq({ wants_living: v })} />
            <Toggle label="Dining" checked={form.req.wants_dining} onChange={(v) => patchReq({ wants_dining: v })} />
            <Toggle label="Puja room" checked={form.req.wants_pooja} onChange={(v) => patchReq({ wants_pooja: v })} />
            <Toggle label="Study" checked={form.req.wants_study} onChange={(v) => patchReq({ wants_study: v })} />
            <Toggle label="Store room" checked={form.req.wants_store} onChange={(v) => patchReq({ wants_store: v })} />
            <Toggle label="Utility / wash" checked={form.req.wants_utility} onChange={(v) => patchReq({ wants_utility: v })} />
            <Toggle label="Balcony" checked={form.req.wants_balcony} onChange={(v) => patchReq({ wants_balcony: v })} />
            <Toggle label="Terrace" checked={form.req.wants_terrace} onChange={(v) => patchReq({ wants_terrace: v })} />
            <Toggle label="Garden" checked={form.req.wants_garden} onChange={(v) => patchReq({ wants_garden: v })} />
            <Toggle label="Courtyard" checked={form.req.wants_courtyard} onChange={(v) => patchReq({ wants_courtyard: v })} />
            <Toggle label="Servant room" checked={form.req.wants_servant_room} onChange={(v) => patchReq({ wants_servant_room: v })} />
            <Toggle label="Shop / workspace" checked={form.req.wants_shop} onChange={(v) => patchReq({ wants_shop: v })} />
          </div>
          <div className="flex gap-2 text-[12px]">
            <Button variant="subtle" onClick={() => setHelp("floors")}>Not sure about floors?</Button>
            <Button variant="subtle" onClick={() => setHelp("parking")}>Not sure about parking?</Button>
            <Button variant="subtle" onClick={() => setHelp("attached_baths")}>Attached baths?</Button>
          </div>
        </Panel>
      )}

      {step === 3 && (
        <Panel className="space-y-4 p-5">
          <SectionTitle>Household, style & budget</SectionTitle>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Family size"><input type="number" min={1} max={20} className={inputClass}
              value={form.req.family_size} onChange={(e) => patchReq({ family_size: Number(e.target.value) })} /></Field>
            <Field label="Style">
              <select className={inputClass} value={form.req.style} onChange={(e) => patchReq({ style: e.target.value })}>
                {STYLES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
              </select>
            </Field>
            <Field label="Finish level">
              <select className={inputClass} value={form.req.finish_level} onChange={(e) => patchReq({ finish_level: e.target.value as Requirements["finish_level"] })}>
                <option value="economical">economical</option><option value="standard">standard</option>
                <option value="premium">premium</option>
              </select>
            </Field>
            <Field label="Budget target (₹, optional)">
              <input className={inputClass} value={form.req.budget_target ?? ""}
                onChange={(e) => patchReq({ budget_target: e.target.value ? Number(e.target.value) : null })} />
            </Field>
          </div>
          <div className="grid grid-cols-1 gap-1 md:grid-cols-3">
            <Toggle label="Step-free / accessibility needs" checked={form.req.accessibility} onChange={(v) => patchReq({ accessibility: v })} />
            <Toggle label="Vastu guidance (cultural only)" checked={form.req.vastu} onChange={(v) => patchReq({ vastu: v })} />
          </div>
          <Field label="Future expansion (optional)" hint="e.g. 'add a first floor later' — the engine reserves stair position and structure zones.">
            <input className={inputClass} value={form.req.future_expansion} onChange={(e) => patchReq({ future_expansion: e.target.value })} />
          </Field>
          <Field label="Anything else we should know? (optional)">
            <textarea className={inputClass} rows={2} value={form.req.extra_notes} onChange={(e) => patchReq({ extra_notes: e.target.value })} />
          </Field>
          <div className="flex gap-2">
            <Button variant="subtle" onClick={() => setHelp("vastu")}>What does Vastu change?</Button>
            <Button variant="subtle" onClick={() => setHelp("finish_level")}>What does finish level change?</Button>
          </div>
        </Panel>
      )}

      {step === 4 && (
        <Panel className="space-y-4 p-5">
          <SectionTitle>Photos of your current home or plot — always optional</SectionTitle>
          <p className="text-[13px] text-slate-400">
            JPG, PNG or WebP up to 12 MB each. We keep originals on this server, create thumbnails,
            and only send an image to an AI provider if you later press Analyse/Redesign.
          </p>
          <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden
            onChange={(e) => setFiles((list) => [...list, ...(Array.from(e.target.files || []))])} />
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => fileInput.current?.click()}>Choose images…</Button>
            {files.length > 0 && <Button variant="subtle" onClick={() => setFiles([])}>Clear all</Button>}
          </div>
          <div className="grid grid-cols-3 gap-3 md:grid-cols-5">
            {files.map((file, index) => (
              <div key={`${file.name}${index}`} className="relative overflow-hidden rounded-lg border border-ink-600">
                <img src={URL.createObjectURL(file)} alt={file.name} className="h-24 w-full object-cover" />
                <button className="absolute right-1 top-1 rounded bg-ink-900/80 px-1.5 text-[11px] text-red-300"
                  onClick={() => setFiles((list) => list.filter((_, i) => i !== index))}>remove</button>
                <div className="truncate px-1 py-0.5 text-[10px] text-slate-400">{file.name}</div>
              </div>
            ))}
          </div>
          {form.startWith === "images" && files.length === 0 && (
            <p className="text-[12px] text-amber-300">You chose to start from photos — add at least one, or go back and pick dimensions.</p>
          )}
        </Panel>
      )}

      {step === 5 && (
        <Panel className="space-y-4 p-5">
          <SectionTitle>Review</SectionTitle>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] text-slate-300 md:grid-cols-3">
            <dt className="text-slate-500">Name</dt><dd>{form.name}</dd>
            <dt className="text-slate-500">Plot</dt><dd>{form.width} × {form.depth} {form.unit}, {form.shape}</dd>
            <dt className="text-slate-500">Road</dt><dd>{form.road_side} side{form.road_width ? `, ${form.road_width} m wide` : " (width unknown)"}</dd>
            <dt className="text-slate-500">Location</dt><dd>{[form.city, form.state].filter(Boolean).join(", ") || "not set"} ({form.city_tier})</dd>
            <dt className="text-slate-500">Floors</dt><dd>{form.req.floors}</dd>
            <dt className="text-slate-500">Bed / bath</dt><dd>{form.req.bedrooms} / {form.req.bathrooms} ({form.req.attached_baths} attached)</dd>
            <dt className="text-slate-500">Parking</dt><dd>{form.req.wants_parking.replace(/_/g, " ")}</dd>
            <dt className="text-slate-500">Style</dt><dd className="capitalize">{form.req.style.replace(/_/g, " ")}</dd>
            <dt className="text-slate-500">Finish</dt><dd>{form.req.finish_level}</dd>
            <dt className="text-slate-500">Photos</dt><dd>{files.length || "none"}</dd>
          </dl>
          <SafetyNote>
            The engine will assume typical setbacks and flag them as unverified. Nothing it produces
            is a construction drawing; structural and legal checks stay with licensed professionals.
          </SafetyNote>
          <div className="flex gap-2">
            <Button size="lg" disabled={busy} onClick={submit}>{busy ? "Generating concepts…" : "Generate design concepts"}</Button>
            <Button variant="ghost" onClick={() => setStep(4)}>Back</Button>
          </div>
        </Panel>
      )}

      <div className="flex justify-between">
        <Button variant="ghost" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>Back</Button>
        {step < 5 && <Button onClick={() => setStep((s) => s + 1)}>Continue</Button>}
      </div>
    </div>
  );
}
