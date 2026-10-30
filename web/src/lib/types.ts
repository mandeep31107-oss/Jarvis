export interface User {
  id: string;
  email: string | null;
  phone: string | null;
  name: string;
  emailVerified?: boolean;
  phoneVerified?: boolean;
  hasPassword?: boolean;
}

export type DeliveryMode = 'ready' | 'console' | 'not-configured';

export interface AuthMethods {
  password: boolean;
  emailOtp: boolean;
  phoneOtp: boolean;
  emailOtpMode: DeliveryMode;
  phoneOtpMode: DeliveryMode;
  emailTransport: string | null;
  smsTransport: string | null;
  devCodesRevealed: boolean;
  googleOAuth: boolean;
}

export interface AuthStatus {
  needsSetup: boolean;
  authenticated: boolean;
  user: User | null;
  methods: AuthMethods;
  otp: { ttlSeconds: number; maxAttempts: number };
}

export interface OtpChallenge {
  challengeId: string;
  channel: 'email' | 'phone';
  maskedTarget: string;
  expiresInSeconds: number;
  transport: string;
  devCode?: string;
  message: string;
}

export type AgentStatus = 'active' | 'limited' | 'planned';

export interface Agent {
  id: string;
  name: string;
  role: string;
  mission: string;
  tools: string[];
  permissions: string[];
  outputFormat: string;
  status: AgentStatus;
  statusNote: string;
  accent: string;
}

export type FeatureStatus = 'available' | 'limited' | 'needs_config' | 'planned';

export interface CapabilityFeature {
  id: string;
  name: string;
  status: FeatureStatus;
  note: string;
}

export interface IntegrationStatus {
  platform: string;
  status: string;
  officialRoute: string;
  limitation: string;
}

export interface Capabilities {
  provider: { id: string; model: string; configured: boolean; description: string; streaming: boolean };
  autonomyLevel: 1 | 2 | 3;
  config: { researchEnabled: boolean; researchAllowedHosts: string[]; env: string; ephemeralSessionSecret: boolean };
  features: CapabilityFeature[];
  integrations: IntegrationStatus[];
  actions: {
    available: Array<{ type: string; label: string; description: string; risk: string; external: boolean }>;
    unavailable: Array<{ type: string; label: string; reason: string }>;
  };
}

export interface VoiceSettings {
  mode: 'text' | 'voice' | 'both';
  language: 'en-IN' | 'hi-IN' | 'en-GB' | 'en-US';
  rate: number;
  pitch: number;
  wakeEnabled: boolean;
  wakePhrase: string;
  readNotifications: boolean;
  privacyMode: boolean;
  reducedMotion: boolean;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  status: 'pending' | 'in_progress' | 'blocked' | 'done' | 'cancelled';
  priority: 'low' | 'medium' | 'high' | 'critical';
  due_at: string | null;
  estimate_minutes: number | null;
  project: string | null;
  source: string;
  created_at: string;
  completed_at: string | null;
  depends_on: string[];
}

export interface TaskStats {
  total: number;
  open: number;
  done: number;
  overdue: number;
  dueToday: number;
  blocked: number;
  byStatus: Record<string, number>;
}

export type RunStatus =
  | 'planned'
  | 'waiting'
  | 'running'
  | 'needs_approval'
  | 'blocked'
  | 'failed'
  | 'completed'
  | 'verified'
  | 'cancelled';

export interface Run {
  id: string;
  agent_id: string;
  intent: string;
  language: string;
  status: RunStatus;
  prompt_preview: string;
  stage_total: number;
  stage_done: number;
  progress: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface RunEvent {
  id: number;
  run_id: string;
  seq: number;
  stage: string;
  status: string;
  detail: string | null;
  created_at: string;
  user_id?: string;
}

export interface Approval {
  id: string;
  run_id: string | null;
  action_type: string;
  payload: Record<string, unknown>;
  risk: 'low' | 'medium' | 'high';
  status: 'pending' | 'approved' | 'rejected' | 'auto_executed' | 'failed' | 'expired';
  summary: string;
  result: unknown;
  error: string | null;
  created_at: string;
  decided_at: string | null;
  executed_at: string | null;
  action_label: string;
  available: boolean;
}

export interface ActionOutcome {
  actionType: string;
  summary: string;
  risk: string;
  requiresApproval: boolean;
  status: string;
  approvalId: string | null;
  reason: string;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  agent_id: string | null;
  language: string | null;
  meta_json: string | null;
  created_at: string;
}

export interface ChatMeta {
  runId?: string;
  confidence?: number;
  routingReasons?: string[];
  warnings?: string[];
  actions?: ActionOutcome[];
  provider?: string;
  model?: string;
  latencyMs?: number;
  usage?: { promptTokens?: number; completionTokens?: number };
  costUsdEstimate?: number;
}

export interface Conversation {
  id: string;
  title: string;
  agent_id: string | null;
  created_at: string;
  updated_at: string;
  message_count?: number;
  last_message?: string | null;
}

export interface MemoryEntry {
  id: string;
  category: string;
  title: string;
  content: string;
  source_type: 'user' | 'ai' | 'tool';
  source_ref: string | null;
  confidence: 'high' | 'medium' | 'low';
  project: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchNote {
  id: string;
  question: string;
  jurisdiction: string | null;
  summary: string;
  sources: Array<{ url: string; finalUrl?: string; title: string; fetchedAt: string; excerpt?: string }>;
  status: string;
  confidence: string;
  created_at: string;
}

export interface FinanceEntry {
  id: string;
  kind: 'income' | 'expense';
  category: string;
  amount_minor: number;
  currency: string;
  occurred_on: string;
  recurring: number;
  note: string;
  created_at: string;
}

export interface FinanceSummary {
  currency: string;
  incomeMinor: number;
  expenseMinor: number;
  netMinor: number;
  recurringMonthlyMinor: number;
  byCategory: Array<{ category: string; kind: 'income' | 'expense'; totalMinor: number; count: number }>;
  periodFrom: string | null;
  periodTo: string | null;
  entryCount: number;
}

export interface ForecastRow {
  month: number;
  label: string;
  revenueMinor: number;
  costsMinor: number;
  netMinor: number;
  closingCashMinor: number;
}

export interface ForecastResult {
  currency: string;
  months: number;
  rows: ForecastRow[];
  runwayMonths: number | null;
  breakEvenMonth: number | null;
  assumptions: string[];
  warnings: string[];
  note: string;
}

export interface ComplianceItem {
  id: string;
  jurisdiction: string;
  area: string;
  title: string;
  status: string;
  source_url: string | null;
  publisher: string | null;
  published_on: string | null;
  effective_on: string | null;
  notes: string;
  updated_at: string;
}

export interface Briefing {
  generatedAt: string;
  owner: { name: string };
  priorities: Array<{ id: string; title: string; priority: string; dueAt: string | null; status: string }>;
  dueToday: Array<{ id: string; title: string; dueAt: string | null }>;
  overdue: Array<{ id: string; title: string; dueAt: string | null }>;
  stats: TaskStats;
  pendingApprovals: number;
  finance: {
    currency: string;
    income: string;
    expense: string;
    net: string;
    recurringMonthly: string;
    entryCount: number;
  };
  memoryCount: number;
  recentRuns: Run[];
  warnings: string[];
  provider: string;
  autonomyLevel: number;
}

export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  target: string | null;
  detail_json: string | null;
  ip: string | null;
  created_at: string;
}

export interface UsageSummary {
  requests: number;
  failures: number;
  promptTokens: number;
  completionTokens: number;
  costUsdEstimate: number;
  avgLatencyMs: number;
  last7Days: Array<{ day: string; requests: number; costUsdEstimate: number }>;
  pricing: { inputPerMTok: number; outputPerMTok: number; model: string };
  note: string;
}
