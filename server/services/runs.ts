import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { all, get, nowIso, run } from '../db/index.js';
import { logger } from '../logger.js';

/**
 * Workflow runs and their real execution events.
 *
 * Progress is never invented: it is completed stages / total stages, derived
 * from events that were actually written while the turn executed.
 */

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

export interface RunRecord {
  id: string;
  user_id: string;
  conversation_id: string | null;
  agent_id: string;
  intent: string;
  language: string;
  status: RunStatus;
  prompt_preview: string;
  stage_total: number;
  stage_done: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface RunEventRecord {
  id: number;
  run_id: string;
  seq: number;
  stage: string;
  status: string;
  detail: string | null;
  created_at: string;
  user_id?: string;
}

export const RUN_STAGES = ['received', 'context', 'routed', 'generating', 'validating', 'finalized'] as const;
export type RunStage = (typeof RUN_STAGES)[number];

const bus = new EventEmitter();
bus.setMaxListeners(500);

export function onRunEvent(listener: (event: RunEventRecord) => void): () => void {
  bus.on('event', listener);
  return () => bus.off('event', listener);
}

export function createRun(input: {
  userId: string;
  conversationId: string | null;
  agentId: string;
  intent: string;
  language: string;
  prompt: string;
}): RunRecord {
  const id = randomUUID();
  run(
    `INSERT INTO runs (id, user_id, conversation_id, agent_id, intent, language, status,
       prompt_preview, stage_total, stage_done, started_at)
     VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?, 0, ?)`,
    [
      id,
      input.userId,
      input.conversationId,
      input.agentId,
      input.intent,
      input.language,
      input.prompt.slice(0, 240),
      RUN_STAGES.length,
      nowIso(),
    ],
  );
  return getRun(input.userId, id)!;
}

export function recordEvent(
  runId: string,
  userId: string,
  stage: RunStage | string,
  status: 'started' | 'completed' | 'failed' | 'waiting' | 'info',
  detail?: string,
): RunEventRecord {
  const seqRow = get<{ s: number | null }>('SELECT MAX(seq) AS s FROM run_events WHERE run_id = ?', [runId]);
  const seq = Number(seqRow?.s ?? 0) + 1;
  const createdAt = nowIso();
  run(
    'INSERT INTO run_events (run_id, seq, stage, status, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)',
    [runId, seq, stage, status, detail?.slice(0, 2000) ?? null, createdAt],
  );
  if (status === 'completed' && RUN_STAGES.includes(stage as RunStage)) {
    run(
      'UPDATE runs SET stage_done = MIN(stage_total, stage_done + 1) WHERE id = ?',
      [runId],
    );
  }
  const record: RunEventRecord = {
    id: 0,
    run_id: runId,
    seq,
    stage,
    status,
    detail: detail ?? null,
    created_at: createdAt,
    user_id: userId,
  };
  try {
    bus.emit('event', record);
  } catch (err) {
    logger.warn('run event listener failed', { error: err instanceof Error ? err.message : String(err) });
  }
  return record;
}

export function setRunStatus(runId: string, userId: string, status: RunStatus, error?: string | null): void {
  const finished = ['completed', 'verified', 'failed', 'cancelled'].includes(status);
  run('UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE id = ?', [
    status,
    error ?? null,
    finished ? nowIso() : null,
    runId,
  ]);
  recordEvent(runId, userId, 'finalized', 'info', `Run status: ${status}`);
}

export function getRun(userId: string, id: string): RunRecord | undefined {
  return get<RunRecord>('SELECT * FROM runs WHERE id = ? AND user_id = ?', [id, userId]);
}

export function listRuns(userId: string, limit = 30): RunRecord[] {
  return all<RunRecord>(
    'SELECT * FROM runs WHERE user_id = ? ORDER BY started_at DESC LIMIT ?',
    [userId, Math.min(Math.max(limit, 1), 200)],
  );
}

export function listRunEvents(userId: string, runId: string): RunEventRecord[] {
  return all<RunEventRecord>(
    `SELECT e.*, r.user_id FROM run_events e JOIN runs r ON r.id = e.run_id
     WHERE e.run_id = ? AND r.user_id = ? ORDER BY e.seq ASC`,
    [runId, userId],
  );
}

export function recentRunEvents(userId: string, limit = 40): RunEventRecord[] {
  return all<RunEventRecord>(
    `SELECT e.*, r.user_id FROM run_events e JOIN runs r ON r.id = e.run_id
     WHERE r.user_id = ? ORDER BY e.id DESC LIMIT ?`,
    [userId, Math.min(Math.max(limit, 1), 200)],
  );
}

export function progressPercent(runRecord: RunRecord): number {
  if (runRecord.stage_total <= 0) return 0;
  return Math.round((runRecord.stage_done / runRecord.stage_total) * 100);
}

// ---------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------

const activeAborts = new Map<string, AbortController>();

export function registerRunAbort(runId: string, controller: AbortController): void {
  activeAborts.set(runId, controller);
}

export function unregisterRunAbort(runId: string): void {
  activeAborts.delete(runId);
}

/** Aborts the provider request for a run if it is still executing. */
export function cancelRun(userId: string, runId: string): { cancelled: boolean; reason: string } {
  const runRecord = getRun(userId, runId);
  if (!runRecord) return { cancelled: false, reason: 'not_found' };
  if (['completed', 'verified', 'failed', 'cancelled'].includes(runRecord.status)) {
    return { cancelled: false, reason: `already_${runRecord.status}` };
  }
  const controller = activeAborts.get(runId);
  if (!controller) {
    return { cancelled: false, reason: 'not_running_in_this_process' };
  }
  controller.abort();
  recordEvent(runId, userId, 'finalized', 'info', 'Cancellation requested by owner');
  return { cancelled: true, reason: 'abort_signal_sent' };
}
