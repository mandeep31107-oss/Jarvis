import { AGENTS, type AgentDefinition } from '../agents/registry.js';

export type Language = 'en' | 'hi' | 'hinglish';

const HINGLISH_MARKERS = [
  'kya', 'hai', 'haan', 'nahi', 'mujhe', 'mera', 'meri', 'aap', 'tum', 'bhai', 'karo',
  'kar', 'kaise', 'kyun', 'kyu', 'chahiye', 'batao', 'bana', 'banao', 'yaad', 'kal',
  'aaj', 'abhi', 'sakte', 'sakta', 'hoga', 'karna', 'wala', 'bahut', 'thoda', 'accha',
  'theek', 'matlab', 'paise', 'kharcha', 'kaam', 'jarvis',
];

/**
 * Language detection is intentionally simple and explainable: Devanagari script
 * means Hindi; otherwise we look for romanised Hindi markers. The detected
 * language is passed to the model as an explicit instruction, so a wrong guess
 * is recoverable by the owner simply replying in the language they want.
 */
export function detectLanguage(text: string): Language {
  const devanagari = (text.match(/[\u0900-\u097F]/g) ?? []).length;
  const latin = (text.match(/[A-Za-z]/g) ?? []).length;
  if (devanagari > 0) {
    // Mostly Devanagari → Hindi; mixed script with plenty of English → Hinglish.
    return devanagari / Math.max(devanagari + latin, 1) > 0.6 ? 'hi' : 'hinglish';
  }
  const words = text.toLowerCase().split(/[^a-z]+/).filter(Boolean);
  const hits = words.filter((w) => HINGLISH_MARKERS.includes(w)).length;
  if (words.length > 0 && hits >= 2 && hits / words.length >= 0.15) return 'hinglish';
  if (hits >= 1 && words.length <= 6) return 'hinglish';
  return 'en';
}

export interface RouteResult {
  agent: AgentDefinition;
  agentId: string;
  confidence: number; // 0..1, derived from matched keyword weight
  reasons: string[];
  alternatives: Array<{ agentId: string; score: number }>;
}

function scoreAgent(text: string, agent: AgentDefinition): { score: number; hits: string[] } {
  const lower = ` ${text.toLowerCase()} `;
  let score = 0;
  const hits: string[] = [];
  for (const keyword of agent.keywords) {
    const k = keyword.toLowerCase();
    const asWord = new RegExp(`(^|[^a-z])${escapeRegex(k)}([^a-z]|$)`, 'g');
    const wordMatches = lower.match(asWord)?.length ?? 0;
    if (wordMatches > 0) {
      score += wordMatches * (2 + k.split(' ').length);
      hits.push(keyword);
      continue;
    }
    if (k.length >= 5 && lower.includes(k)) {
      score += 1;
      hits.push(keyword);
    }
  }
  return { score, hits };
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Deterministic, auditable intent routing. A language model could classify too,
 * but a keyword router is inspectable, free, works offline and never invents a
 * routing decision the owner cannot see.
 */
export function routeIntent(text: string): RouteResult {
  const scored = AGENTS.map((agent) => {
    const { score, hits } = scoreAgent(text, agent);
    return { agent, score, hits };
  }).sort((a, b) => b.score - a.score);

  const best = scored[0];
  const fallback = AGENTS.find((a) => a.id === 'orchestrator');
  if (!fallback) throw new Error('orchestrator agent missing from registry');

  if (!best || best.score === 0) {
    return {
      agent: fallback,
      agentId: fallback.id,
      confidence: 0.2,
      reasons: ['No specialist keyword matched — handled by the coordinator.'],
      alternatives: [],
    };
  }

  const total = scored.reduce((sum, s) => sum + s.score, 0) || 1;
  const confidence = Math.min(0.95, 0.45 + (best.score / total) * 0.5);
  return {
    agent: best.agent,
    agentId: best.agent.id,
    confidence: Number(confidence.toFixed(2)),
    reasons: [`Matched keywords: ${best.hits.slice(0, 6).join(', ')}`],
    alternatives: scored
      .slice(1, 4)
      .filter((s) => s.score > 0)
      .map((s) => ({ agentId: s.agent.id, score: s.score })),
  };
}

/** Rough intent label used for run telemetry, not for routing decisions. */
export function intentLabel(text: string): string {
  const t = text.toLowerCase();
  if (/(create|add|make|banao|bana do).{0,20}(task|todo)/.test(t) || /^(task|todo)\b/.test(t)) return 'create_task';
  if (/\b(remember|yaad rakho|note karo)\b/.test(t)) return 'save_memory';
  if (/\b(research|search|find out|khoj|dhundo)\b/.test(t)) return 'research';
  if (/\b(budget|runway|cash ?flow|expense|kharcha)\b/.test(t)) return 'finance';
  if (/\b(plan|roadmap|strategy|yojana)\b/.test(t)) return 'plan';
  if (/\b(hello|hi|hey|namaste|jarvis)\b/.test(t) && t.split(/\s+/).length <= 4) return 'greeting';
  return 'question';
}
