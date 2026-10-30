import { z } from 'zod';
import type { AppConfig } from '../config.js';
import { createFinanceEntry } from '../repo/finance.js';
import { createMemory, MEMORY_CATEGORIES } from '../repo/memory.js';
import { createResearchNote, type ResearchSource } from '../repo/research.js';
import { createTask } from '../repo/tasks.js';
import { fetchSource } from '../tools/research.js';

/**
 * The only way J.A.R.V.I.S. can change anything.
 *
 * Every side effect is a named action with a payload schema, a declared risk
 * level, an explicit "external" flag and an executor. Nothing else is reachable
 * from the conversation layer, which keeps permissions auditable.
 */

export type RiskLevel = 'low' | 'medium' | 'high';

export interface ActionExecutionContext {
  userId: string;
  config: AppConfig;
  ip?: string | null;
}

export interface ActionDefinition<P = unknown> {
  type: string;
  label: string;
  description: string;
  risk: RiskLevel;
  /** True when the action touches anything outside this application. */
  external: boolean;
  /** Agents allowed to propose this action. */
  agentIds: string[];
  schema: z.ZodType<P>;
  summarise: (payload: P) => string;
  execute: (ctx: ActionExecutionContext, payload: P) => Promise<{ result: Record<string, unknown> }>;
}

const taskSchema = z.object({
  title: z.string().min(3).max(300),
  description: z.string().max(4000).optional(),
  priority: z.enum(['low', 'medium', 'high', 'critical']).optional(),
  dueAt: z.string().datetime().nullable().optional(),
  project: z.string().max(120).nullable().optional(),
  estimateMinutes: z.number().int().min(5).max(10_000).nullable().optional(),
});

const memorySchema = z.object({
  category: z.enum(MEMORY_CATEGORIES as [string, ...string[]]),
  title: z.string().min(3).max(200),
  content: z.string().min(3).max(8000),
  confidence: z.enum(['high', 'medium', 'low']).optional(),
  project: z.string().max(120).nullable().optional(),
});

const financeSchema = z.object({
  kind: z.enum(['income', 'expense']),
  category: z.string().min(1).max(80),
  amountMinor: z.number().int().positive().max(1_000_000_000_00),
  currency: z.string().length(3).optional(),
  occurredOn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  recurring: z.boolean().optional(),
  note: z.string().max(500).optional(),
});

const researchSchema = z.object({
  question: z.string().min(3).max(500),
  urls: z.array(z.string().url()).min(1).max(3),
  jurisdiction: z.string().max(40).nullable().optional(),
});

export const ACTIONS: ActionDefinition<never>[] = [
  {
    type: 'create_task',
    label: 'Create a task',
    description: 'Adds a task to the owner\'s task list.',
    risk: 'low',
    external: false,
    agentIds: ['orchestrator', 'productivity', 'startup-operations', 'business-strategist', 'financial-planning', 'research', 'legal-compliance', 'marketing', 'coding', 'website-development'],
    schema: taskSchema as never,
    summarise: (p) => {
      const payload = p as z.infer<typeof taskSchema>;
      return `Create task "${payload.title}"${payload.priority ? ` (${payload.priority} priority)` : ''}`;
    },
    execute: async (ctx, payload) => {
      const input = payload as z.infer<typeof taskSchema>;
      const task = createTask(ctx.userId, {
        title: input.title,
        description: input.description,
        priority: input.priority,
        dueAt: input.dueAt ?? null,
        project: input.project ?? null,
        estimateMinutes: input.estimateMinutes ?? null,
        source: 'jarvis',
      });
      return { result: { taskId: task.id, title: task.title, status: task.status } };
    },
  },
  {
    type: 'save_memory',
    label: 'Save to memory',
    description: 'Stores a durable fact, preference or decision the owner wants remembered.',
    risk: 'low',
    external: false,
    agentIds: ['orchestrator', 'business-strategist', 'startup-operations', 'financial-planning', 'research', 'legal-compliance', 'marketing', 'productivity', 'coding'],
    schema: memorySchema as never,
    summarise: (p) => {
      const payload = p as z.infer<typeof memorySchema>;
      return `Remember (${payload.category}): ${payload.title}`;
    },
    execute: async (ctx, payload) => {
      const input = payload as z.infer<typeof memorySchema>;
      const entry = createMemory(ctx.userId, {
        category: input.category as never,
        title: input.title,
        content: input.content,
        sourceType: 'user',
        confidence: (input.confidence as never) ?? 'high',
        project: input.project ?? null,
      });
      return { result: { memoryId: entry.id, category: entry.category } };
    },
  },
  {
    type: 'add_finance_entry',
    label: 'Record a finance entry',
    description: 'Records an income or expense. Records data only; it can never move money.',
    risk: 'low',
    external: false,
    agentIds: ['orchestrator', 'financial-planning'],
    schema: financeSchema as never,
    summarise: (p) => {
      const payload = p as z.infer<typeof financeSchema>;
      return `Record ${payload.kind} of ${(payload.amountMinor / 100).toFixed(2)} ${payload.currency ?? 'INR'} in "${payload.category}"`;
    },
    execute: async (ctx, payload) => {
      const input = payload as z.infer<typeof financeSchema>;
      const entry = createFinanceEntry(ctx.userId, input);
      return { result: { entryId: entry.id, kind: entry.kind, amountMinor: entry.amount_minor } };
    },
  },
  {
    type: 'web_research',
    label: 'Fetch web sources',
    description:
      'Retrieves specific public URLs and stores them as a research note. No search API is connected, so the owner must supply the URLs.',
    risk: 'medium',
    external: true,
    agentIds: ['orchestrator', 'research', 'legal-compliance', 'business-strategist'],
    schema: researchSchema as never,
    summarise: (p) => {
      const payload = p as z.infer<typeof researchSchema>;
      return `Fetch ${payload.urls.length} URL(s) for research: ${payload.question.slice(0, 120)}`;
    },
    execute: async (ctx, payload) => {
      const input = payload as z.infer<typeof researchSchema>;
      const sources: ResearchSource[] = [];
      const failures: string[] = [];
      for (const url of input.urls) {
        const res = await fetchSource(url, {
          allowOutbound: ctx.config.allowOutboundFetch,
          allowedHosts: ctx.config.researchAllowedHosts,
        });
        if (res.ok) {
          sources.push({
            url: res.source.url,
            finalUrl: res.source.finalUrl,
            title: res.source.title,
            fetchedAt: res.source.fetchedAt,
            excerpt: res.source.text.slice(0, 1200),
          });
        } else {
          failures.push(`${url}: ${res.failure.error}`);
        }
      }
      if (sources.length === 0) {
        createResearchNote(ctx.userId, {
          question: input.question,
          jurisdiction: input.jurisdiction ?? null,
          summary: `No source could be retrieved. ${failures.join(' | ')}`,
          sources: [],
          status: 'failed',
          confidence: 'low',
        });
        throw new Error(`No source could be retrieved. ${failures.join(' | ')}`);
      }
      const note = createResearchNote(ctx.userId, {
        question: input.question,
        jurisdiction: input.jurisdiction ?? null,
        summary: sources
          .map((s, i) => `[${i + 1}] ${s.title} — ${s.url}\n${(s.excerpt ?? '').slice(0, 800)}`)
          .join('\n\n'),
        sources,
        status: failures.length > 0 ? 'partial' : 'sources_retrieved',
        confidence: 'medium',
      });
      return {
        result: {
          noteId: note.id,
          sourcesRetrieved: sources.length,
          failures,
          note: 'Excerpts are stored verbatim. They are not summarised by a model and must be read before being relied on.',
        },
      };
    },
  },
];

export function getAction(type: string): ActionDefinition<never> | undefined {
  return ACTIONS.find((a) => a.type === type);
}

export function actionCatalogue(): Array<{
  type: string;
  label: string;
  description: string;
  risk: RiskLevel;
  external: boolean;
}> {
  return ACTIONS.map(({ type, label, description, risk, external }) => ({
    type,
    label,
    description,
    risk,
    external,
  }));
}

export interface ApprovalPolicyResult {
  requiresApproval: boolean;
  reason: string;
}

/**
 * Approval policy is intentionally conservative:
 *  - anything external always needs the owner's approval;
 *  - Level 1 (suggest only) needs approval for every change;
 *  - high-risk actions always need approval.
 * Low-risk internal records execute immediately at Level 2+, and are still
 * written to the approval + audit history so the owner can see what happened.
 */
export function approvalPolicy(action: ActionDefinition<never>, autonomyLevel: number): ApprovalPolicyResult {
  if (action.external) {
    return { requiresApproval: true, reason: 'External action — always requires explicit approval.' };
  }
  if (autonomyLevel <= 1) {
    return { requiresApproval: true, reason: 'Autonomy level 1 (suggest only) — changes are queued for approval.' };
  }
  if (action.risk === 'high') {
    return { requiresApproval: true, reason: 'High-risk action — always requires explicit approval.' };
  }
  return {
    requiresApproval: false,
    reason: 'Low-risk internal record; executed immediately and logged in the audit trail.',
  };
}

/** Action types that exist in the product concept but are not implemented. */
export const UNAVAILABLE_ACTIONS = [
  {
    type: 'send_message',
    label: 'Send a platform message',
    reason: 'No WhatsApp/Telegram/Instagram/LinkedIn integration is connected. Drafts only.',
  },
  {
    type: 'publish_post',
    label: 'Publish public content',
    reason: 'No publishing API is connected and no auto-publish rules are configured.',
  },
  {
    type: 'move_money',
    label: 'Make a payment or transfer',
    reason: 'Deliberately not implemented. J.A.R.V.I.S. must never move money.',
  },
  {
    type: 'deploy_production',
    label: 'Deploy to production',
    reason: 'Deployment pipeline is not built yet.',
  },
];
