import { Router } from 'express';
import { z } from 'zod';
import { AGENTS } from '../agents/registry.js';
import { actionCatalogue, UNAVAILABLE_ACTIONS } from '../actions/index.js';
import type { AppConfig } from '../config.js';
import type { AiProvider } from '../ai/types.js';
import { financeSummary } from '../repo/finance.js';
import { listMemory } from '../repo/memory.js';
import { getVoiceSettings, getEffectiveAutonomy, getAllSettings, setSetting } from '../repo/settings.js';
import { listTasks, taskStats } from '../repo/tasks.js';
import { requireAuth } from '../security/auth.js';
import { countPendingApprovals } from '../services/approvals.js';
import { listAudit } from '../services/audit.js';
import { listRuns, progressPercent } from '../services/runs.js';
import { usageSummary } from '../services/usage.js';
import { formatMoney } from '../tools/finance.js';

export interface SystemDeps {
  config: AppConfig;
  provider: AiProvider;
}

const settingsPatchSchema = z.object({
  voice: z
    .object({
      mode: z.enum(['text', 'voice', 'both']).optional(),
      language: z.enum(['en-IN', 'hi-IN', 'en-GB', 'en-US']).optional(),
      rate: z.number().min(0.5).max(2).optional(),
      pitch: z.number().min(0.5).max(2).optional(),
      wakeEnabled: z.boolean().optional(),
      wakePhrase: z.string().min(2).max(40).optional(),
      readNotifications: z.boolean().optional(),
      privacyMode: z.boolean().optional(),
      reducedMotion: z.boolean().optional(),
    })
    .optional(),
  autonomyLevel: z.union([z.literal(1), z.literal(2), z.literal(3)]).optional(),
});

export function systemRouter(deps: SystemDeps): Router {
  const router = Router();
  const { config, provider } = deps;

  router.get('/capabilities', requireAuth, (req, res) => {
    const autonomy = getEffectiveAutonomy(req.user!.id, config);
    const features = [
      {
        id: 'chat',
        name: 'Chat with J.A.R.V.I.S.',
        status: 'available',
        note: provider.describe(),
      },
      {
        id: 'streaming',
        name: 'Live token streaming',
        status: provider.id === 'offline' ? 'limited' : 'available',
        note: provider.id === 'offline' ? 'Offline templates are returned in one block.' : 'Streamed from the provider as tokens arrive.',
      },
      {
        id: 'voice_input',
        name: 'Voice input',
        status: 'available',
        note: 'Browser Web Speech API. Works in Chrome and Edge; accuracy depends on your microphone and browser.',
      },
      {
        id: 'voice_output',
        name: 'Voice output (English/Hindi/Hinglish)',
        status: 'available',
        note: 'Browser SpeechSynthesis. Available voices depend on your operating system.',
      },
      {
        id: 'wake_phrase',
        name: 'Wake phrase',
        status: 'available',
        note: 'Browser-side keyword spotting while the mic is enabled. Not speaker verification.',
      },
      {
        id: 'speaker_verification',
        name: 'Speaker verification / voice enrolment',
        status: 'planned',
        note: 'Not implemented. Voice alone is never treated as authorization for sensitive actions.',
      },
      {
        id: 'tasks',
        name: 'Tasks, priorities, dependencies',
        status: 'available',
        note: 'Stored in SQLite with audit trail.',
      },
      {
        id: 'approvals',
        name: 'Approval centre',
        status: 'available',
        note: 'External actions always require explicit approval.',
      },
      {
        id: 'memory',
        name: 'Persistent memory',
        status: 'available',
        note: 'Owner-stated facts are labelled separately from inferences.',
      },
      {
        id: 'research_fetch',
        name: 'Fetch specific URLs',
        status: config.allowOutboundFetch ? 'available' : 'needs_config',
        note: config.allowOutboundFetch
          ? 'Approved URLs are fetched and stored verbatim as sources.'
          : 'Set ALLOW_OUTBOUND_FETCH=true to enable outbound retrieval.',
      },
      {
        id: 'web_search',
        name: 'Web search',
        status: 'planned',
        note: 'No search API is connected. J.A.R.V.I.S. cannot discover URLs by itself yet.',
      },
      {
        id: 'finance',
        name: 'Budget, cash-flow and runway calculator',
        status: 'available',
        note: 'Deterministic maths with stated assumptions. Records only; never moves money.',
      },
      {
        id: 'compliance',
        name: 'Compliance register',
        status: 'limited',
        note: 'Manual tracking works; automated regulation monitoring is planned.',
      },
      {
        id: 'code_execution',
        name: 'Code execution and file editing',
        status: 'planned',
        note: 'Sandboxed execution is a later milestone.',
      },
      {
        id: 'deployment',
        name: 'Website preview and deployment',
        status: 'planned',
        note: 'Not implemented.',
      },
      {
        id: 'messaging',
        name: 'WhatsApp / Telegram / Instagram / LinkedIn',
        status: 'planned',
        note: 'Requires official API access and credentials. None are connected.',
      },
    ];

    res.json({
      provider: {
        id: provider.id,
        model: provider.model,
        configured: provider.isConfigured(),
        description: provider.describe(),
        streaming: Boolean(provider.completeStream),
      },
      autonomyLevel: autonomy,
      config: {
        researchEnabled: config.allowOutboundFetch,
        researchAllowedHosts: config.researchAllowedHosts,
        env: config.env,
        ephemeralSessionSecret: config.ephemeralSessionSecret,
      },
      features,
      integrations: [
        {
          platform: 'whatsapp',
          status: 'not_connected',
          officialRoute: 'WhatsApp Business Platform (Cloud API) with a verified Meta business account.',
          limitation: 'Personal WhatsApp accounts cannot be automated this way.',
        },
        {
          platform: 'telegram',
          status: 'not_connected',
          officialRoute: 'Telegram Bot API with a bot token from @BotFather.',
          limitation: 'Bot access only; it cannot act as your personal account.',
        },
        {
          platform: 'instagram',
          status: 'not_connected',
          officialRoute: 'Meta Graph API messaging for eligible business/creator accounts.',
          limitation: 'No access to arbitrary personal inboxes; app review required.',
        },
        {
          platform: 'linkedin',
          status: 'not_connected',
          officialRoute: 'LinkedIn Marketing/Share APIs with approved access.',
          limitation: 'No bulk connections or unsolicited automation.',
        },
      ],
      actions: { available: actionCatalogue(), unavailable: UNAVAILABLE_ACTIONS },
    });
  });

  router.get('/agents', requireAuth, (req, res) => {
    const agents = AGENTS.map((agent) => {
      if (agent.id === 'research' && config.allowOutboundFetch) {
        return {
          ...agent,
          status: 'active' as const,
          statusNote: 'Outbound fetch enabled. Still no search API: the owner must supply URLs.',
        };
      }
      return agent;
    });
    res.json({ agents });
  });

  router.get('/settings', requireAuth, (req, res) => {
    res.json({
      voice: getVoiceSettings(req.user!.id, config),
      autonomyLevel: getEffectiveAutonomy(req.user!.id, config),
      autonomyDefault: config.autonomyLevel,
      raw: getAllSettings(req.user!.id),
      limits: {
        sessionTtlHours: config.sessionTtlHours,
        providerTimeoutMs: config.ai.timeoutMs,
        maxOutputTokens: config.ai.maxOutputTokens,
      },
    });
  });

  router.patch('/settings', requireAuth, (req, res) => {
    const parsed = settingsPatchSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const userId = req.user!.id;
    if (parsed.data.voice) {
      const current = getVoiceSettings(userId, config);
      setSetting(userId, 'voice', { ...current, ...parsed.data.voice });
    }
    if (parsed.data.autonomyLevel) {
      setSetting(userId, 'autonomyLevel', parsed.data.autonomyLevel);
    }
    res.json({
      voice: getVoiceSettings(userId, config),
      autonomyLevel: getEffectiveAutonomy(userId, config),
    });
  });

  router.get('/audit', requireAuth, (req, res) => {
    res.json({ entries: listAudit(req.user!.id, Number(req.query.limit ?? 100)) });
  });

  router.get('/usage', requireAuth, (req, res) => {
    res.json(usageSummary(req.user!.id, config));
  });

  router.get('/briefing', requireAuth, (req, res) => {
    const userId = req.user!.id;
    const tasks = listTasks(userId, { limit: 100 });
    const stats = taskStats(userId);
    const openTasks = tasks.filter((t) => t.status !== 'done' && t.status !== 'cancelled');
    const today = new Date().toISOString().slice(0, 10);
    const dueToday = openTasks.filter((t) => t.due_at?.slice(0, 10) === today);
    const overdue = openTasks.filter((t) => t.due_at && t.due_at.slice(0, 10) < today);
    const finance = financeSummary(userId, 'INR');
    const runs = listRuns(userId, 5).map((r) => ({ ...r, progress: progressPercent(r) }));
    const pendingApprovals = countPendingApprovals(userId);
    const memories = listMemory(userId);

    const warnings: string[] = [];
    if (provider.id === 'offline') {
      warnings.push('No language model configured — replies come from offline templates.');
    }
    if (config.ephemeralSessionSecret) {
      warnings.push('SESSION_SECRET is not set; sessions are invalidated on every restart.');
    }
    if (!config.allowOutboundFetch) {
      warnings.push('Internet research is disabled (ALLOW_OUTBOUND_FETCH=false).');
    }
    if (pendingApprovals > 0) {
      warnings.push(`${pendingApprovals} action(s) are waiting for your approval.`);
    }

    res.json({
      generatedAt: new Date().toISOString(),
      owner: { name: req.user!.name },
      priorities: openTasks.slice(0, 5).map((t) => ({
        id: t.id,
        title: t.title,
        priority: t.priority,
        dueAt: t.due_at,
        status: t.status,
      })),
      dueToday: dueToday.map((t) => ({ id: t.id, title: t.title, dueAt: t.due_at })),
      overdue: overdue.map((t) => ({ id: t.id, title: t.title, dueAt: t.due_at })),
      stats,
      pendingApprovals,
      finance: {
        currency: finance.currency,
        income: formatMoney(finance.incomeMinor, finance.currency),
        expense: formatMoney(finance.expenseMinor, finance.currency),
        net: formatMoney(finance.netMinor, finance.currency),
        recurringMonthly: formatMoney(finance.recurringMonthlyMinor, finance.currency),
        entryCount: finance.entryCount,
      },
      memoryCount: memories.length,
      recentRuns: runs,
      warnings,
      provider: provider.describe(),
      autonomyLevel: getEffectiveAutonomy(userId, config),
    });
  });

  return router;
}

export function healthRouter(config: AppConfig, provider: AiProvider, startedAt: number): Router {
  const router = Router();
  router.get('/health', (_req, res) => {
    let dbOk = false;
    try {
      dbOk = Boolean(listTasks('__health_probe__', { limit: 1 }));
    } catch {
      // The call above reaches the database; any throw means it is unhealthy.
      dbOk = false;
    }
    res.status(dbOk ? 200 : 503).json({
      status: dbOk ? 'ok' : 'degraded',
      checks: {
        database: dbOk ? 'ok' : 'failed',
        aiProvider: provider.isConfigured() ? 'ok' : 'not_configured',
      },
      provider: provider.describe(),
      env: config.env,
      uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
      time: new Date().toISOString(),
      version: process.env.npm_package_version ?? '0.1.0',
    });
  });
  return router;
}
