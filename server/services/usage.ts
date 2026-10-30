import { all, get, nowIso, run } from '../db/index.js';
import type { AppConfig } from '../config.js';
import type { TokenUsage } from '../ai/types.js';

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

export function estimateCostUsd(config: AppConfig, usage: TokenUsage): number {
  const prompt = usage.promptTokens ?? 0;
  const completion = usage.completionTokens ?? 0;
  return (
    (prompt / 1_000_000) * config.ai.priceInputPerMTok +
    (completion / 1_000_000) * config.ai.priceOutputPerMTok
  );
}

export function estimateTokensFromText(text: string): number {
  return Math.ceil(text.length / 4);
}

export function recordUsage(args: {
  userId: string;
  provider: string;
  model: string;
  usage: TokenUsage;
  latencyMs: number;
  ok: boolean;
  costUsdEstimate: number;
}): void {
  run(
    `INSERT INTO usage_events (user_id, provider, model, prompt_tokens, completion_tokens,
       cost_usd_estimate, latency_ms, ok, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      args.userId,
      args.provider,
      args.model,
      args.usage.promptTokens ?? null,
      args.usage.completionTokens ?? null,
      args.costUsdEstimate,
      args.latencyMs,
      args.ok ? 1 : 0,
      nowIso(),
    ],
  );
}

export function usageSummary(userId: string, config: AppConfig): UsageSummary {
  const totals = get<{
    requests: number;
    failures: number;
    p: number | null;
    c: number | null;
    cost: number | null;
    latency: number | null;
  }>(
    `SELECT COUNT(*) AS requests,
            SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) AS failures,
            SUM(prompt_tokens) AS p,
            SUM(completion_tokens) AS c,
            SUM(cost_usd_estimate) AS cost,
            AVG(latency_ms) AS latency
     FROM usage_events WHERE user_id = ?`,
    [userId],
  );
  const daily = all<{ day: string; requests: number; cost: number | null }>(
    `SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS requests, SUM(cost_usd_estimate) AS cost
     FROM usage_events WHERE user_id = ? AND created_at >= datetime('now', '-7 days')
     GROUP BY day ORDER BY day`,
    [userId],
  );
  return {
    requests: Number(totals?.requests ?? 0),
    failures: Number(totals?.failures ?? 0),
    promptTokens: Number(totals?.p ?? 0),
    completionTokens: Number(totals?.c ?? 0),
    costUsdEstimate: Number((Number(totals?.cost ?? 0)).toFixed(6)),
    avgLatencyMs: Math.round(Number(totals?.latency ?? 0)),
    last7Days: daily.map((d) => ({ day: d.day, requests: d.requests, costUsdEstimate: Number((d.cost ?? 0).toFixed(6)) })),
    pricing: {
      inputPerMTok: config.ai.priceInputPerMTok,
      outputPerMTok: config.ai.priceOutputPerMTok,
      model: config.ai.model,
    },
    note: 'Costs are estimates based on configured per-token prices and provider-reported usage. They are not invoices.',
  };
}
