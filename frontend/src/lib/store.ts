/** Application store: project document, undo/redo, autosave, walkthrough state,
 *  comfort/performance settings and transient UI state. */

import { create } from "zustand";
import type {
  BudgetEstimate, ProjectData, ProjectRecord, ValidationResult, VersionInfo,
} from "../types/project";
import { api } from "./api";

export interface Toast { id: number; kind: "info" | "success" | "error" | "warn"; text: string }

export type ViewMode =
  | "exterior" | "top" | "dollhouse" | "room" | "walk" | "elev_front" | "elev_side" | "elev_rear";

export interface WalkSettings {
  eyeHeight: number;
  speed: number;
  sprint: number;
  sensitivity: number;
  invertLook: boolean;
  bob: boolean;
  mode: "realistic" | "free";
  audio: boolean;
}

export interface QualitySettings {
  preset: "high" | "balanced" | "performance" | "mobile";
  shadows: boolean;
  dpr: number;
  lighting: "day" | "evening" | "night";
  exposure: number;
  sunAngle: number;
  temperature: "warm" | "neutral" | "cool";
}

export interface Visibility {
  roof: boolean; ceiling: boolean; walls: boolean; furniture: boolean; floors: boolean;
  cutaway: boolean; exploded: boolean; transparentWalls: boolean;
}

interface Store {
  projectId: string | null;
  record: ProjectRecord | null;
  data: ProjectData | null;
  validation: ValidationResult | null;
  budget: BudgetEstimate | null;
  versions: VersionInfo[];
  loading: boolean;
  saving: boolean;
  error: string | null;
  toasts: Toast[];
  progress: { stage: string; percent: number; message: string } | null;

  // editor
  selectedRoomId: string | null;
  selectedObjectId: string | null;
  activeFloor: number;
  undoStack: string[];
  redoStack: string[];

  // walkthrough
  viewMode: ViewMode;
  walk: WalkSettings;
  quality: QualitySettings;
  visibility: Visibility;
  doorStates: Record<string, number>;
  windowStates: Record<string, number>;
  tour: { active: boolean; routeKey: string; paused: boolean; speed: number; stopLabel: string | null };
  focusRoomId: string | null;
  measureMode: boolean;
  measurePoints: { x: number; y: number; z: number }[];
  compareSnapshot: string | null;

  set: (patch: Partial<Store>) => void;
  toast: (kind: Toast["kind"], text: string) => void;
  loadProject: (id: string) => Promise<void>;
  loadList: () => Promise<ProjectRecord[]>;
  setData: (data: ProjectData, options?: { snapshot?: boolean; save?: boolean; label?: string }) => Promise<void>;
  mutate: (fn: (draft: ProjectData) => void, label?: string) => Promise<void>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  save: (label?: string) => Promise<void>;
  refreshBudget: () => Promise<void>;
  refreshValidation: () => Promise<void>;
  loadVersions: () => Promise<void>;
  setDoor: (id: string, open: number) => void;
  setWindow: (id: string, open: number) => void;
}

let toastId = 1;
const MAX_UNDO = 40;

export const useStore = create<Store>((setState, getState) => ({
  projectId: null,
  record: null,
  data: null,
  validation: null,
  budget: null,
  versions: [],
  loading: false,
  saving: false,
  error: null,
  toasts: [],
  progress: null,
  selectedRoomId: null,
  selectedObjectId: null,
  activeFloor: 0,
  undoStack: [],
  redoStack: [],
  viewMode: "exterior",
  walk: { eyeHeight: 1.65, speed: 1.35, sprint: 3.1, sensitivity: 1, invertLook: false,
    bob: true, mode: "realistic", audio: false },
  quality: { preset: "balanced", shadows: true, dpr: 1.5, lighting: "day", exposure: 1,
    sunAngle: 135, temperature: "neutral" },
  visibility: { roof: true, ceiling: true, walls: true, furniture: true, floors: true,
    cutaway: false, exploded: false, transparentWalls: false },
  doorStates: {},
  windowStates: {},
  tour: { active: false, routeKey: "full", paused: false, speed: 1, stopLabel: null },
  focusRoomId: null,
  measureMode: false,
  measurePoints: [],
  compareSnapshot: null,

  set: (patch) => setState(patch),

  toast: (kind, text) => {
    const id = toastId++;
    setState((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
    window.setTimeout(() => {
      setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, kind === "error" ? 7000 : 4200);
  },

  loadProject: async (id) => {
    setState({ loading: true, error: null, projectId: id });
    try {
      const record = await api.get<ProjectRecord>(`/api/projects/${id}`);
      setState({ record, data: record.data, validation: record.validation as ValidationResult,
        loading: false, undoStack: [], redoStack: [], activeFloor: 0,
        selectedRoomId: null, doorStates: {}, windowStates: {} });
      getState().refreshBudget().catch(() => undefined);
      getState().loadVersions().catch(() => undefined);
    } catch (error) {
      setState({ loading: false, error: String((error as Error).message || error) });
    }
  },

  loadList: async () => {
    const payload = await api.get<{ projects: ProjectRecord[] }>("/api/projects");
    return payload.projects;
  },

  setData: async (data, options) => {
    const state = getState();
    const snapshot = options?.snapshot !== false;
    setState((s) => ({
      data,
      undoStack: snapshot ? [...s.undoStack.slice(-MAX_UNDO), JSON.stringify(s.data)] : s.undoStack,
      redoStack: [],
    }));
    if (options?.save !== false) await getState().save(options?.label || "edit");
  },

  mutate: async (fn, label) => {
    const state = getState();
    if (!state.data) return;
    const draft = structuredClone(state.data) as ProjectData;
    fn(draft);
    await getState().setData(draft, { label });
  },

  undo: async () => {
    const { undoStack, data } = getState();
    if (!undoStack.length || !data) return;
    const previous = undoStack[undoStack.length - 1];
    setState((s) => ({
      undoStack: s.undoStack.slice(0, -1),
      redoStack: [...s.redoStack, JSON.stringify(data)],
      data: JSON.parse(previous) as ProjectData,
    }));
    await getState().save("undo");
  },

  redo: async () => {
    const { redoStack, data } = getState();
    if (!redoStack.length || !data) return;
    const next = redoStack[redoStack.length - 1];
    setState((s) => ({
      redoStack: s.redoStack.slice(0, -1),
      undoStack: [...s.undoStack, JSON.stringify(data)],
      data: JSON.parse(next) as ProjectData,
    }));
    await getState().save("redo");
  },

  save: async (label) => {
    const { projectId, data } = getState();
    if (!projectId || !data) return;
    setState({ saving: true });
    try {
      await api.put(`/api/projects/${projectId}`, { data, label: label || "edit" });
      setState({ saving: false });
    } catch (error) {
      setState({ saving: false });
      getState().toast("error", `Save failed: ${(error as Error).message}. Your changes are kept in this session.`);
    }
  },

  refreshBudget: async () => {
    const { projectId } = getState();
    if (!projectId) return;
    const budget = await api.get<BudgetEstimate>(`/api/projects/${projectId}/budget`);
    setState({ budget });
  },

  refreshValidation: async () => {
    const { projectId } = getState();
    if (!projectId) return;
    const validation = await api.get<ValidationResult>(`/api/projects/${projectId}/validation`);
    setState({ validation });
  },

  loadVersions: async () => {
    const { projectId } = getState();
    if (!projectId) return;
    const payload = await api.get<{ versions: VersionInfo[] }>(`/api/projects/${projectId}/versions`);
    setState({ versions: payload.versions });
  },

  setDoor: (id, open) => setState((s) => ({ doorStates: { ...s.doorStates, [id]: open } })),
  setWindow: (id, open) => setState((s) => ({ windowStates: { ...s.windowStates, [id]: open } })),
}));

export function autosaveLoop() {
  window.setInterval(() => {
    const state = useStore.getState();
    if (state.projectId && state.data && !state.saving) {
      api.post(`/api/projects/${state.projectId}/autosave`, { data: state.data }).catch(() => undefined);
    }
  }, 45000);
}
