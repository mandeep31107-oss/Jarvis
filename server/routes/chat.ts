import { Router } from 'express';
import { z } from 'zod';
import { generateAnswer, prepareTurn } from '../ai/orchestrator.js';
import { AiError } from '../ai/types.js';
import type { AppConfig } from '../config.js';
import type { AiProvider } from '../ai/types.js';
import { SseStream } from '../http/sse.js';
import { logger } from '../logger.js';
import {
  addMessage,
  createConversation,
  deleteConversation,
  getConversation,
  historyForModel,
  listConversations,
  listMessages,
} from '../repo/chat.js';
import { relevantMemory } from '../repo/memory.js';
import { effectiveConfig, getVoiceSettings } from '../repo/settings.js';
import { listTasks } from '../repo/tasks.js';
import { requireAuth } from '../security/auth.js';
import { proposeAction } from '../services/approvals.js';
import { recordUsage, estimateCostUsd, estimateTokensFromText } from '../services/usage.js';
import { missingSourceWarning, validateCompletion } from '../ai/validation.js';
import { createRun, recordEvent, RUN_STAGES, setRunStatus } from '../services/runs.js';
import { writeAudit } from '../services/audit.js';

export interface RouterDeps {
  config: AppConfig;
  provider: AiProvider;
}

const messageSchema = z.object({
  conversationId: z.string().uuid().nullish(),
  text: z.string().min(1).max(6000),
});

export function chatRouter(deps: RouterDeps): Router {
  const router = Router();

  router.get('/conversations', requireAuth, (req, res) => {
    res.json({ conversations: listConversations(req.user!.id) });
  });

  router.post('/conversations', requireAuth, (req, res) => {
    const title = typeof req.body?.title === 'string' ? req.body.title : 'New conversation';
    const conversation = createConversation(req.user!.id, title);
    res.status(201).json({ conversation });
  });

  router.delete('/conversations/:id', requireAuth, (req, res) => {
    const deleted = deleteConversation(req.user!.id, req.params.id);
    if (!deleted) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'conversation.deleted', target: req.params.id, ip: req.ip });
    res.json({ ok: true });
  });

  router.get('/conversations/:id/messages', requireAuth, (req, res) => {
    const conversation = getConversation(req.user!.id, req.params.id);
    if (!conversation) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    res.json({ conversation, messages: listMessages(req.user!.id, conversation.id) });
  });

  /**
   * POST /api/chat/message — streams the turn as Server-Sent Events.
   *
   * Event types: run, stage, delta, validated, action, action_result, done, error.
   * Every stage and delta is produced by real work; nothing is simulated.
   */
  router.post('/message', requireAuth, async (req, res) => {
    const parsed = messageSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const userId = req.user!.id;
    const { text } = parsed.data;

    let conversation = parsed.data.conversationId
      ? getConversation(userId, parsed.data.conversationId)
      : undefined;
    if (!conversation) {
      conversation = createConversation(userId, text.slice(0, 60));
    }
    const conversationId = conversation.id;

    addMessage(conversationId, 'user', text);
    const history = historyForModel(userId, conversationId, 12);
    // historyForModel includes the message we just stored; drop the final entry.
    const priorHistory = history.slice(0, -1);

    // Owner-level overrides (autonomy) must apply to every turn.
    const config = effectiveConfig(userId, deps.config);
    const prepared = prepareTurn({
      ownerName: req.user!.name,
      text,
      history: priorHistory,
      memories: relevantMemory(userId, text, 8),
      tasks: listTasks(userId, { limit: 20 }).filter((t) => t.status !== 'done' && t.status !== 'cancelled'),
      config,
      provider: deps.provider,
    });

    const runRecord = createRun({
      userId,
      conversationId,
      agentId: prepared.agent.id,
      intent: prepared.intent,
      language: prepared.language,
      prompt: text,
    });

    const sse = new SseStream(res);
    const abort = new AbortController();
    res.on('close', () => {
      if (!res.writableEnded) abort.abort();
    });

    let clientGone = false;
    sse.send('run', {
      runId: runRecord.id,
      conversationId,
      stages: RUN_STAGES,
      agent: { id: prepared.agent.id, name: prepared.agent.name, accent: prepared.agent.accent },
      language: prepared.language,
      provider: deps.provider.describe(),
    });

    recordEvent(runRecord.id, userId, 'received', 'completed', `Message received (${text.length} characters)`);
    recordEvent(
      runRecord.id,
      userId,
      'context',
      'completed',
      `Retrieved ${prepared.messages.length - 2 > 0 ? prepared.messages.length - 2 : 0} history messages, ` +
        `${relevantMemory(userId, text, 8).length} memories, open tasks snapshot`,
    );
    recordEvent(
      runRecord.id,
      userId,
      'routed',
      'completed',
      `${prepared.agent.name} · confidence ${prepared.confidence} · ${prepared.routingReasons.join(' ')}`,
    );
    recordEvent(runRecord.id, userId, 'generating', 'started', deps.provider.describe());

    sse.send('stage', {
      stage: 'routed',
      status: 'completed',
      detail: `${prepared.agent.name} (confidence ${prepared.confidence})`,
    });

    let streamed = '';
    try {
      const completion = await generateAnswer(
        prepared,
        deps.provider,
        config,
        {
          onDelta: (chunk) => {
            streamed += chunk;
            sse.send('delta', { text: chunk });
          },
        },
        abort.signal,
      );
      recordEvent(
        runRecord.id,
        userId,
        'generating',
        'completed',
        `${completion.provider}/${completion.model} in ${completion.latencyMs} ms`,
      );
      recordEvent(runRecord.id, userId, 'validating', 'started', 'Checking for fabricated actions and leaked credentials');

      const validated = validateCompletion(completion.text, { executedActionTypes: [] });
      const sourceWarning = missingSourceWarning(validated.text, prepared.agent.id);
      const warnings = sourceWarning ? [...validated.warnings, sourceWarning] : validated.warnings;
      recordEvent(
        runRecord.id,
        userId,
        'validating',
        'completed',
        warnings.length > 0 ? warnings.join(' | ') : 'No issues found',
      );

      const usage = {
        promptTokens: completion.usage.promptTokens ?? estimateTokensFromText(prepared.messages.map((m) => m.content).join(' ')),
        completionTokens: completion.usage.completionTokens ?? estimateTokensFromText(validated.text),
      };
      const cost = estimateCostUsd(config, usage);
      recordUsage({
        userId,
        provider: completion.provider,
        model: completion.model,
        usage,
        latencyMs: completion.latencyMs,
        ok: true,
        costUsdEstimate: cost,
      });

      // Execute or queue the deterministic action proposals.
      const actionResults: Array<Record<string, unknown>> = [];
      const executedTypes: string[] = [];
      let pendingApprovals = 0;
      for (const proposal of prepared.proposals) {
        if (abort.signal.aborted) break;
        const result = await proposeAction({
          userId,
          runId: runRecord.id,
          actionType: proposal.actionType,
          payload: proposal.payload,
          config,
          ip: req.ip,
        });
        if (result.status === 'pending') pendingApprovals += 1;
        if (result.status === 'approved' || result.status === 'auto_executed') {
          executedTypes.push(proposal.actionType);
        }
        const payload = {
          actionType: proposal.actionType,
          summary: proposal.summary,
          risk: proposal.risk,
          requiresApproval: proposal.requiresApproval,
          status: result.status,
          approvalId: result.approvalId || null,
          reason: result.reason,
          result: result.result,
          error: result.error,
        };
        actionResults.push(payload);
        sse.send('action', payload);
      }

      const assistantMessage = addMessage(conversationId, 'assistant', validated.text, {
        agentId: prepared.agent.id,
        language: prepared.language,
        meta: {
          runId: runRecord.id,
          confidence: prepared.confidence,
          routingReasons: prepared.routingReasons,
          warnings,
          actions: actionResults,
          provider: completion.provider,
          model: completion.model,
          latencyMs: completion.latencyMs,
          usage,
          costUsdEstimate: Number(cost.toFixed(8)),
        },
      });

      if (executedTypes.length > 0 && validated.fabricatedActionClaims.length > 0) {
        recordEvent(runRecord.id, userId, 'validating', 'info', 'Fabricated action claim corrected after execution check.');
      }

      const finalStatus = pendingApprovals > 0 ? 'needs_approval' : 'completed';
      setRunStatus(runRecord.id, userId, finalStatus);
      writeAudit({
        userId,
        actor: 'jarvis',
        action: 'chat.turn_completed',
        target: runRecord.id,
        detail: {
          agent: prepared.agent.id,
          language: prepared.language,
          actions: actionResults.map((a) => ({ type: a.actionType, status: a.status })),
          provider: completion.provider,
        },
        ip: req.ip,
      });

      sse.send('validated', { warnings, corrections: validated.fabricatedActionClaims, redactions: validated.redactions });
      sse.send('done', {
        runId: runRecord.id,
        conversationId,
        messageId: assistantMessage.id,
        text: validated.text,
        status: finalStatus,
        agent: {
          id: prepared.agent.id,
          name: prepared.agent.name,
          role: prepared.agent.role,
          accent: prepared.agent.accent,
        },
        language: prepared.language,
        warnings,
        usage,
        costUsdEstimate: Number(cost.toFixed(8)),
        actions: actionResults,
        pendingApprovals,
      });
      sse.close();
    } catch (err) {
      clientGone = abort.signal.aborted;
      const isAi = err instanceof AiError;
      const message = err instanceof Error ? err.message : String(err);
      if (!clientGone) {
        logger.warn('chat turn failed', { error: message, code: isAi ? err.code : 'unknown' });
        recordEvent(runRecord.id, userId, 'generating', 'failed', message);
        setRunStatus(runRecord.id, userId, clientGone ? 'cancelled' : 'failed', message.slice(0, 1000));
        recordUsage({
          userId,
          provider: deps.provider.id,
          model: deps.provider.model,
          usage: {},
          latencyMs: 0,
          ok: false,
          costUsdEstimate: 0,
        });
        writeAudit({
          userId,
          actor: 'jarvis',
          action: 'chat.turn_failed',
          target: runRecord.id,
          detail: { error: message },
          ip: req.ip,
        });
        sse.send('error', {
          code: isAi ? err.code : 'internal_error',
          message,
          hint: isAi
            ? 'Check AI_PROVIDER, AI_BASE_URL, AI_MODEL and AI_API_KEY in .env, then restart the server. Nothing was fabricated as a fallback answer.'
            : 'The turn did not complete. See the server log for details.',
          runId: runRecord.id,
        });
      } else {
        setRunStatus(runRecord.id, userId, 'cancelled', 'Client disconnected');
      }
      sse.close();
    }
  });

  return router;
}

export function voiceSettingsFor(userId: string, config: AppConfig) {
  return getVoiceSettings(userId, config);
}
