import { randomUUID } from 'node:crypto';
import type { AppConfig } from '../config.js';
import { all, get, nowIso, run } from '../db/index.js';
import { getAction, approvalPolicy, type RiskLevel } from '../actions/index.js';
import { logger } from '../logger.js';
import { writeAudit } from './audit.js';
import { recordEvent } from './runs.js';

export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'auto_executed'
  | 'failed'
  | 'expired';

export interface Approval {
  id: string;
  user_id: string;
  run_id: string | null;
  action_type: string;
  payload_json: string;
  risk: RiskLevel;
  status: ApprovalStatus;
  summary: string;
  result_json: string | null;
  error: string | null;
  created_at: string;
  decided_at: string | null;
  executed_at: string | null;
}

export interface ApprovalView extends Omit<Approval, 'payload_json' | 'result_json'> {
  payload: unknown;
  result: unknown;
  action_label: string;
  available: boolean;
}

function toView(row: Approval): ApprovalView {
  const action = getAction(row.action_type);
  const { payload_json, result_json, ...rest } = row;
  return {
    ...rest,
    payload: safeParse(payload_json),
    result: result_jsonRules(result_json),
    action_label: action?.label ?? row.action_type,
    available: Boolean(action),
  };
}

function result_jsonRules(value: string | null): unknown {
  return value ? safeParse(value) : null;
}

function safeParse(value: string): unknown {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

export function listApprovals(userId: string, status?: ApprovalStatus, limit = 100): ApprovalView[] {
  const rows = status
    ? all<Approval>(
        'SELECT * FROM approvals WHERE user_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?',
        [userId, status, Math.min(limit, 300)],
      )
    : all<Approval>('SELECT * FROM approvals WHERE user_id = ? ORDER BY created_at DESC LIMIT ?', [
        userId,
        Math.min(limit, 300),
      ]);
  return rows.map(toView);
}

export function countPendingApprovals(userId: string): number {
  return Number(
    get<{ c: number }>("SELECT COUNT(*) AS c FROM approvals WHERE user_id = ? AND status = 'pending'", [userId])?.c ?? 0,
  );
}

export interface ProposeResult {
  status: ApprovalStatus;
  approvalId: string;
  requiresApproval: boolean;
  reason: string;
  result: Record<string, unknown> | null;
  error: string | null;
}

/**
 * Single entry point for every proposed side effect: validate, apply policy,
 * then either queue for approval or execute immediately (low risk, Level 2+).
 */
export async function proposeAction(args: {
  userId: string;
  runId: string | null;
  actionType: string;
  payload: unknown;
  config: AppConfig;
  ip?: string | null;
}): Promise<ProposeResult> {
  const action = getAction(args.actionType);
  if (!action) {
    return {
      status: 'failed',
      approvalId: '',
      requiresApproval: false,
      reason: `Unknown action type "${args.actionType}".`,
      result: null,
      error: 'unknown_action',
    };
  }

  const parsed = action.schema.safeParse(args.payload);
  if (!parsed.success) {
    const issue = parsed.error.issues.map((i) => `${i.path.join('.') || 'payload'}: ${i.message}`).join('; ');
    writeAudit({
      userId: args.userId,
      actor: 'jarvis',
      action: 'action.rejected_invalid',
      target: args.actionType,
      detail: { issue },
      ip: args.ip,
    });
    return {
      status: 'failed',
      approvalId: '',
      requiresApproval: true,
      reason: `Payload rejected: ${issue}`,
      result: null,
      error: 'invalid_payload',
    };
  }

  const policy = approvalPolicy(action, args.config.autonomyLevel);
  const id = randomUUID();
  const summary = action.summarise(parsed.data as never);
  const ts = nowIso();

  run(
    `INSERT INTO approvals (id, user_id, run_id, action_type, payload_json, risk, status, summary, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      args.userId,
      args.runId,
      args.actionType,
      JSON.stringify(parsed.data),
      action.risk,
      policy.requiresApproval ? 'pending' : 'approved',
      summary,
      ts,
    ],
  );

  writeAudit({
    userId: args.userId,
    actor: 'jarvis',
    action: policy.requiresApproval ? 'approval.requested' : 'action.auto_approved',
    target: args.actionType,
    detail: { approvalId: id, reason: policy.reason, risk: action.risk },
    ip: args.ip,
  });

  if (args.runId) {
    recordEvent(
      args.runId,
      args.userId,
      'approval',
      policy.requiresApproval ? 'waiting' : 'info',
      policy.requiresApproval ? `Approval required: ${summary}` : `Auto-approved: ${summary}`,
    );
  }

  if (policy.requiresApproval) {
    return {
      status: 'pending',
      approvalId: id,
      requiresApproval: true,
      reason: policy.reason,
      result: null,
      error: null,
    };
  }

  const executed = await executeApprovalRow(args.userId, id, args.config, args.ip, 'auto_executed');
  return {
    status: executed.status,
    approvalId: id,
    requiresApproval: false,
    reason: policy.reason,
    result: executed.result,
    error: executed.error,
  };
}

async function executeApprovalRow(
  userId: string,
  approvalId: string,
  config: AppConfig,
  ip: string | null | undefined,
  successStatus: 'approved' | 'auto_executed',
): Promise<{ status: ApprovalStatus; result: Record<string, unknown> | null; error: string | null }> {
  const row = get<Approval>('SELECT * FROM approvals WHERE id = ? AND user_id = ?', [approvalId, userId]);
  if (!row) return { status: 'failed', result: null, error: 'not_found' };
  const action = getAction(row.action_type);
  if (!action) return { status: 'failed', result: null, error: 'unknown_action' };

  const parsed = action.schema.safeParse(safeParse(row.payload_json));
  if (!parsed.success) {
    run("UPDATE approvals SET status = 'failed', error = ? WHERE id = ?", ['invalid_payload', approvalId]);
    return { status: 'failed', result: null, error: 'invalid_payload' };
  }

  try {
    const { result } = await action.execute(
      { userId, config, ip: ip ?? null },
      parsed.data as never,
    );
    const executedAt = nowIso();
    run(
      `UPDATE approvals SET status = ?, result_json = ?, executed_at = ?, decided_at = COALESCE(decided_at, ?)
       WHERE id = ?`,
      [successStatus, JSON.stringify(result), executedAt, executedAt, approvalId],
    );
    writeAudit({
      userId,
      actor: successStatus === 'auto_executed' ? 'policy' : 'owner',
      action: 'action.executed',
      target: row.action_type,
      detail: { approvalId, result },
      ip,
    });
    if (row.run_id) {
      recordEvent(row.run_id, userId, 'approval', 'completed', `Executed: ${row.summary}`);
    }
    return { status: successStatus, result, error: null };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    run("UPDATE approvals SET status = 'failed', error = ? WHERE id = ?", [message.slice(0, 1000), approvalId]);
    writeAudit({
      userId,
      actor: 'jarvis',
      action: 'action.failed',
      target: row.action_type,
      detail: { approvalId, error: message },
      ip,
    });
    if (row.run_id) {
      recordEvent(row.run_id, userId, 'approval', 'failed', `Failed: ${message}`);
    }
    logger.warn('action execution failed', { actionType: row.action_type, error: message });
    return { status: 'failed', result: null, error: message };
  }
}

export async function decideApproval(args: {
  userId: string;
  approvalId: string;
  decision: 'approve' | 'reject';
  config: AppConfig;
  ip?: string | null;
}): Promise<{ ok: boolean; status: ApprovalStatus; error?: string; result?: Record<string, unknown> | null }> {
  const row = get<Approval>('SELECT * FROM approvals WHERE id = ? AND user_id = ?', [
    args.approvalId,
    args.userId,
  ]);
  if (!row) return { ok: false, status: 'failed', error: 'not_found' };
  if (row.status !== 'pending') {
    return { ok: false, status: row.status, error: 'already_decided' };
  }

  if (args.decision === 'reject') {
    const ts = nowIso();
    run("UPDATE approvals SET status = 'rejected', decided_at = ? WHERE id = ?", [ts, args.approvalId]);
    writeAudit({
      userId: args.userId,
      actor: 'owner',
      action: 'approval.rejected',
      target: row.action_type,
      detail: { approvalId: row.id, summary: row.summary },
      ip: args.ip,
    });
    if (row.run_id) {
      recordEvent(row.run_id, args.userId, 'approval', 'info', `Rejected by owner: ${row.summary}`);
    }
    return { ok: true, status: 'rejected' };
  }

  const decisionAt = nowIso();
  run('UPDATE approvals SET decided_at = ? WHERE id = ?', [decisionAt, args.approvalId]);
  writeAudit({
    userId: args.userId,
    actor: 'owner',
    action: 'approval.approved',
    target: row.action_type,
    detail: { approvalId: row.id, summary: row.summary },
    ip: args.ip,
  });
  const executed = await executeApprovalRow(args.userId, args.approvalId, args.config, args.ip, 'approved');
  return {
    ok: executed.status === 'approved',
    status: executed.status,
    error: executed.error ?? undefined,
    result: executed.result,
  };
}
