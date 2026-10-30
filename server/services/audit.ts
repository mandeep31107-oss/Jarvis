import { all, get, nowIso, run } from '../db/index.js';

export interface AuditEntry {
  id: number;
  user_id: string | null;
  actor: string;
  action: string;
  target: string | null;
  detail_json: string | null;
  ip: string | null;
  created_at: string;
}

export function writeAudit(entry: {
  userId: string | null;
  actor: string;
  action: string;
  target?: string | null;
  detail?: Record<string, unknown> | null;
  ip?: string | null;
}): void {
  run(
    `INSERT INTO audit_log (user_id, actor, action, target, detail_json, ip, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [
      entry.userId,
      entry.actor,
      entry.action,
      entry.target ?? null,
      entry.detail ? JSON.stringify(entry.detail) : null,
      entry.ip ?? null,
      nowIso(),
    ],
  );
}

export function listAudit(userId: string, limit = 100): AuditEntry[] {
  return all<AuditEntry>(
    `SELECT * FROM audit_log WHERE user_id = ? OR user_id IS NULL
     ORDER BY id DESC LIMIT ?`,
    [userId, Math.min(Math.max(limit, 1), 500)],
  );
}

export function auditCount(userId: string): number {
  return Number(
    get<{ c: number }>('SELECT COUNT(*) AS c FROM audit_log WHERE user_id = ? OR user_id IS NULL', [
      userId,
    ])?.c ?? 0,
  );
}
