import { createHmac, randomInt, randomUUID, timingSafeEqual } from 'node:crypto';
import { all, get, nowIso, run } from '../db/index.js';

/**
 * One-time passcodes for email and phone sign-in.
 *
 * Rules that matter:
 *  - the code is never stored in plaintext (HMAC with the server secret);
 *  - a code expires, is single-use, and dies after N failed attempts;
 *  - requesting a new code invalidates the previous one for that target;
 *  - the number of codes per identifier is limited per time window.
 */

export type OtpChannel = 'email' | 'phone';
export type OtpPurpose = 'signup' | 'login';

const EMAIL_RE = /^[^\s@"'<>,;]{1,64}@[^\s@"'<>,;]+\.[A-Za-z]{2,}$/;

export interface OtpChallengeRow {
  id: string;
  user_id: string | null;
  channel: OtpChannel;
  target: string;
  purpose: OtpPurpose;
  code_hash: string;
  expires_at: string;
  attempts: number;
  consumed_at: string | null;
  ip: string | null;
  created_at: string;
}

export function normalizeEmail(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const value = raw.trim().toLowerCase();
  if (value.length < 6 || value.length > 254) return null;
  if (!EMAIL_RE.test(value)) return null;
  if (value.includes('..')) return null;
  return value;
}

/**
 * Accepts "+919876543210", "09876543210", "9876543210" (default country) and
 * returns strict E.164. Rejects anything that cannot be a real mobile number.
 */
export function normalizePhone(raw: unknown, defaultCountryCode = '+91'): string | null {
  if (typeof raw !== 'string') return null;
  const stripped = raw.trim().replace(/[\s()-]/g, '');
  if (stripped.length === 0) return null;
  let value: string;
  if (stripped.startsWith('+')) {
    value = `+${stripped.slice(1).replace(/\D/g, '')}`;
  } else {
    const digits = stripped.replace(/\D/g, '');
    if (digits.length === 0) return null;
    if (digits.length === 10) value = `${defaultCountryCode}${digits}`;
    else if (digits.length === 11 && digits.startsWith('0')) value = `${defaultCountryCode}${digits.slice(1)}`;
    else if (digits.length >= 11 && digits.length <= 15) value = `+${digits}`;
    else return null;
  }
  if (!/^\+[1-9]\d{6,14}$/.test(value)) return null;
  return value;
}

/** Never log or return a full identifier: the owner sees a masked hint. */
export function maskIdentifier(channel: OtpChannel, target: string): string {
  if (channel === 'email') {
    const [local = '', domain = ''] = target.split('@');
    const head = local.slice(0, 1) || '*';
    return `${head}${'*'.repeat(Math.max(2, Math.min(6, local.length - 1)))}@${domain}`;
  }
  return `${target.slice(0, 3)}${'*'.repeat(Math.max(2, target.length - 6))}${target.slice(-3)}`;
}

function hashCode(secret: string, challengeId: string, target: string, code: string): string {
  return createHmac('sha256', secret).update(`${challengeId}:${target}:${code}`).digest('hex');
}

export function createChallenge(args: {
  channel: OtpChannel;
  target: string;
  purpose: OtpPurpose;
  userId?: string | null;
  ttlSeconds: number;
  secret: string;
  ip?: string | null;
}): { id: string; code: string; expiresAt: string } {
  const id = randomUUID();
  const code = String(randomInt(0, 1_000_000)).padStart(6, '0');
  const now = nowIso();
  // Only the newest code for a target can be used.
  run(
    `UPDATE otp_challenges SET consumed_at = ?
     WHERE target = ? AND purpose = ? AND consumed_at IS NULL`,
    [now, args.target, args.purpose],
  );
  run(
    `INSERT INTO otp_challenges (id, user_id, channel, target, purpose, code_hash, expires_at,
       attempts, consumed_at, ip, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)`,
    [
      id,
      args.userId ?? null,
      args.channel,
      args.target,
      args.purpose,
      hashCode(args.secret, id, args.target, code),
      new Date(Date.now() + args.ttlSeconds * 1000).toISOString(),
      args.ip ?? null,
      now,
    ],
  );
  return { id, code, expiresAt: new Date(Date.now() + args.ttlSeconds * 1000).toISOString() };
}

export type VerifyFailure = 'invalid_challenge' | 'expired' | 'already_used' | 'too_many_attempts' | 'invalid_code';

export function verifyChallenge(args: {
  challengeId: string;
  code: string;
  secret: string;
  maxAttempts: number;
}): { ok: true; challenge: OtpChallengeRow } | { ok: false; reason: VerifyFailure; attemptsRemaining?: number } {
  const row = get<OtpChallengeRow>('SELECT * FROM otp_challenges WHERE id = ?', [args.challengeId]);
  if (!row) return { ok: false, reason: 'invalid_challenge' };
  if (row.consumed_at) return { ok: false, reason: 'already_used' };
  if (new Date(row.expires_at).getTime() <= Date.now()) return { ok: false, reason: 'expired' };
  if (row.attempts >= args.maxAttempts) return { ok: false, reason: 'too_many_attempts' };

  // Count the attempt before comparing, so a crash cannot grant unlimited tries.
  run('UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = ?', [row.id]);

  const expected = Buffer.from(row.code_hash, 'hex');
  const actual = Buffer.from(hashCode(args.secret, row.id, row.target, args.code), 'hex');
  const matches = expected.length === actual.length && timingSafeEqual(expected, actual);

  if (!matches) {
    const attempts = row.attempts + 1;
    if (attempts >= args.maxAttempts) {
      return { ok: false, reason: 'too_many_attempts', attemptsRemaining: 0 };
    }
    return { ok: false, reason: 'invalid_code', attemptsRemaining: args.maxAttempts - attempts };
  }

  run('UPDATE otp_challenges SET consumed_at = ? WHERE id = ?', [nowIso(), row.id]);
  return { ok: true, challenge: { ...row, attempts: row.attempts + 1, consumed_at: nowIso() } };
}

/** Requests allowed per identifier inside the window (brute-force / spam guard). */
/**
 * Requests are limited per identifier, not per purpose: sign-up and sign-in
 * share the same budget so the limit cannot be doubled by switching tabs.
 */
export function recentRequestCount(target: string, windowMinutes = 15): number {
  const since = new Date(Date.now() - windowMinutes * 60_000).toISOString();
  const row = get<{ c: number }>(
    'SELECT COUNT(*) AS c FROM otp_challenges WHERE target = ? AND created_at >= ?',
    [target, since],
  );
  return Number(row?.c ?? 0);
}

export function secondsUntilNextRequest(target: string, windowMinutes = 15): number {
  const since = new Date(Date.now() - windowMinutes * 60_000).toISOString();
  const row = get<{ created_at: string }>(
    `SELECT created_at FROM otp_challenges WHERE target = ? AND created_at >= ?
     ORDER BY created_at ASC LIMIT 1`,
    [target, since],
  );
  if (!row) return 0;
  const freeAt = new Date(row.created_at).getTime() + windowMinutes * 60_000;
  return Math.max(0, Math.ceil((freeAt - Date.now()) / 1000));
}

export function purgeOldChallenges(olderThanHours = 24): void {
  run('DELETE FROM otp_challenges WHERE created_at < ?', [
    new Date(Date.now() - olderThanHours * 3600_000).toISOString(),
  ]);
}

export function listChallengesForTest(target: string): OtpChallengeRow[] {
  return all<OtpChallengeRow>('SELECT * FROM otp_challenges WHERE target = ? ORDER BY created_at DESC', [
    target,
  ]);
}
