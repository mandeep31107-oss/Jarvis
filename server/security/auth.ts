import { createHash, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import type { NextFunction, Request, Response } from 'express';
import { get, nowIso, run } from '../db/index.js';
import { logger } from '../logger.js';

export const SESSION_COOKIE = 'jarvis_session';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
}

export interface Session {
  id: string;
  userId: string;
  expiresAt: string;
}

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      user?: AuthUser;
      session?: Session;
    }
  }
}

const SCRYPT_KEYLEN = 64;

export function hashPassword(password: string): { hash: string; salt: string } {
  const salt = randomBytes(16).toString('base64url');
  const hash = scryptSync(password, salt, SCRYPT_KEYLEN).toString('base64url');
  return { hash, salt };
}

export function verifyPassword(password: string, hash: string, salt: string): boolean {
  const expected = Buffer.from(hash, 'base64url');
  const actual = scryptSync(password, salt, SCRYPT_KEYLEN);
  if (expected.length !== actual.length) return false;
  return timingSafeEqual(expected, actual);
}

export function passwordProblems(password: string): string[] {
  const problems: string[] = [];
  if (password.length < 10) problems.push('Use at least 10 characters.');
  if (!/[a-zA-Z]/.test(password)) problems.push('Include at least one letter.');
  if (!/[0-9]/.test(password)) problems.push('Include at least one number.');
  return problems;
}

export function hashToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

export function createSession(
  userId: string,
  ttlHours: number,
  userAgent: string | undefined,
): { token: string; session: Session } {
  const token = randomBytes(32).toString('base64url');
  const id = randomBytes(16).toString('hex');
  const createdAt = nowIso();
  const expiresAt = new Date(Date.now() + ttlHours * 3600_000).toISOString();
  run(
    `INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, last_seen_at, user_agent)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [id, userId, hashToken(token), createdAt, expiresAt, createdAt, userAgent?.slice(0, 300) ?? null],
  );
  return { token, session: { id, userId, expiresAt } };
}

export function readSession(token: string | undefined): Session | null {
  if (!token) return null;
  const row = get<{ id: string; user_id: string; expires_at: string; last_seen_at: string }>(
    'SELECT id, user_id, expires_at, last_seen_at FROM sessions WHERE token_hash = ?',
    [hashToken(token)],
  );
  if (!row) return null;
  if (new Date(row.expires_at).getTime() <= Date.now()) {
    run('DELETE FROM sessions WHERE id = ?', [row.id]);
    return null;
  }
  // Refresh "last seen" at most once per 5 minutes to avoid write amplification.
  if (Date.now() - new Date(row.last_seen_at).getTime() > 5 * 60_000) {
    run('UPDATE sessions SET last_seen_at = ? WHERE id = ?', [nowIso(), row.id]);
  }
  return { id: row.id, userId: row.user_id, expiresAt: row.expires_at };
}

export function destroySession(sessionId: string): void {
  run('DELETE FROM sessions WHERE id = ?', [sessionId]);
}

export function countUsers(): number {
  return Number(get<{ c: number }>('SELECT COUNT(*) AS c FROM users')?.c ?? 0);
}

export function findUserByEmail(email: string): AuthUser & { password_hash: string; password_salt: string } | undefined {
  return get<AuthUser & { password_hash: string; password_salt: string }>(
    'SELECT id, email, name, password_hash, password_salt FROM users WHERE email = ?',
    [email.toLowerCase().trim()],
  );
}

export function findUserById(id: string): AuthUser | undefined {
  return get<AuthUser>('SELECT id, email, name FROM users WHERE id = ?', [id]);
}

export function createUser(user: {
  id: string;
  email: string;
  name: string;
  passwordHash: string;
  passwordSalt: string;
}): void {
  run(
    `INSERT INTO users (id, email, name, password_hash, password_salt, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [user.id, user.email.toLowerCase().trim(), user.name, user.passwordHash, user.passwordSalt, nowIso()],
  );
}

export function readCookie(req: Request, name: string): string | undefined {
  const header = req.headers.cookie;
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const idx = part.indexOf('=');
    if (idx === -1) continue;
    if (part.slice(0, idx).trim() === name) {
      return decodeURIComponent(part.slice(idx + 1).trim());
    }
  }
  return undefined;
}

export function sessionCookieOptions(isProduction: boolean): {
  httpOnly: true;
  sameSite: 'strict';
  secure: boolean;
  path: string;
  maxAge: number;
} {
  return {
    httpOnly: true,
    sameSite: 'strict',
    secure: isProduction,
    path: '/',
    maxAge: 7 * 24 * 3600_000,
  };
}

/** Attaches req.user when a valid session cookie is present. Never throws. */
export function attachUser(req: Request, _res: Response, next: NextFunction): void {
  try {
    const token = readCookie(req, SESSION_COOKIE);
    const session = readSession(token);
    if (session) {
      const user = findUserById(session.userId);
      if (user) {
        req.user = user;
        req.session = session;
      }
    }
  } catch (err) {
    logger.warn('session lookup failed', { error: err instanceof Error ? err.message : String(err) });
  }
  next();
}

export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  if (!req.user) {
    res.status(401).json({ error: 'authentication_required' });
    return;
  }
  next();
}
