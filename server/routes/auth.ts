import { randomUUID } from 'node:crypto';
import { Router } from 'express';
import { z } from 'zod';
import {
  SESSION_COOKIE,
  countUsers,
  createSession,
  createUser,
  destroySession,
  hashPassword,
  passwordProblems,
  sessionCookieOptions,
  verifyPassword,
  findUserByEmail,
  requireAuth,
} from '../security/auth.js';
import type { AppConfig } from '../config.js';
import { run as dbRun } from '../db/index.js';
import { writeAudit } from '../services/audit.js';

const setupSchema = z.object({
  email: z.string().email().max(200),
  name: z.string().min(1).max(120),
  password: z.string().min(1).max(200),
});

const loginSchema = z.object({
  email: z.string().email().max(200),
  password: z.string().min(1).max(200),
});

export function authRouter(config: AppConfig): Router {
  const router = Router();

  router.get('/status', (req, res) => {
    res.json({
      needsSetup: countUsers() === 0,
      authenticated: Boolean(req.user),
      user: req.user ?? null,
    });
  });

  /** First-run owner creation. Refuses once an owner exists. */
  router.post('/setup', (req, res) => {
    if (countUsers() > 0) {
      res.status(409).json({ error: 'already_configured', message: 'An owner account already exists. Please sign in.' });
      return;
    }
    const parsed = setupSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const problems = passwordProblems(parsed.data.password);
    if (problems.length > 0) {
      res.status(400).json({ error: 'weak_password', message: problems.join(' ') });
      return;
    }
    const { hash, salt } = hashPassword(parsed.data.password);
    const id = randomUUID();
    createUser({
      id,
      email: parsed.data.email,
      name: parsed.data.name.trim(),
      passwordHash: hash,
      passwordSalt: salt,
    });
    const { token } = createSession(id, config.sessionTtlHours, req.get('user-agent'));
    res.cookie(SESSION_COOKIE, token, sessionCookieOptions(config.isProduction));
    writeAudit({
      userId: id,
      actor: 'owner',
      action: 'auth.owner_created',
      target: parsed.data.email,
      ip: req.ip,
    });
    res.status(201).json({ user: { id, email: parsed.data.email.toLowerCase(), name: parsed.data.name } });
  });

  router.post('/login', (req, res) => {
    const parsed = loginSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request' });
      return;
    }
    const user = findUserByEmail(parsed.data.email);
    const ok = user ? verifyPassword(parsed.data.password, user.password_hash, user.password_salt) : false;
    if (!user || !ok) {
      writeAudit({
        userId: user?.id ?? null,
        actor: 'anonymous',
        action: 'auth.login_failed',
        target: parsed.data.email,
        ip: req.ip,
      });
      // Same response for unknown user and wrong password.
      res.status(401).json({ error: 'invalid_credentials', message: 'Email or password is incorrect.' });
      return;
    }
    const { token } = createSession(user.id, config.sessionTtlHours, req.get('user-agent'));
    res.cookie(SESSION_COOKIE, token, sessionCookieOptions(config.isProduction));
    writeAudit({ userId: user.id, actor: 'owner', action: 'auth.login', ip: req.ip });
    res.json({ user: { id: user.id, email: user.email, name: user.name } });
  });

  router.post('/logout', (req, res) => {
    if (req.session) {
      destroySession(req.session.id);
      writeAudit({ userId: req.user?.id ?? null, actor: 'owner', action: 'auth.logout', ip: req.ip });
    }
    res.clearCookie(SESSION_COOKIE, { path: '/' });
    res.json({ ok: true });
  });

  router.get('/me', requireAuth, (req, res) => {
    res.json({ user: req.user });
  });

  router.post('/change-password', requireAuth, (req, res) => {
    const parsed = z
      .object({ currentPassword: z.string().min(1), newPassword: z.string().min(10).max(200) })
      .safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request' });
      return;
    }
    const user = findUserByEmail(req.user!.email);
    if (!user || !verifyPassword(parsed.data.currentPassword, user.password_hash, user.password_salt)) {
      res.status(401).json({ error: 'invalid_credentials' });
      return;
    }
    const problems = passwordProblems(parsed.data.newPassword);
    if (problems.length > 0) {
      res.status(400).json({ error: 'weak_password', message: problems.join(' ') });
      return;
    }
    const { hash, salt } = hashPassword(parsed.data.newPassword);
    dbRun('UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?', [hash, salt, user.id]);
    // Invalidate every other session.
    dbRun('DELETE FROM sessions WHERE user_id = ? AND id != ?', [user.id, req.session?.id ?? '']);
    writeAudit({ userId: user.id, actor: 'owner', action: 'auth.password_changed', ip: req.ip });
    res.json({ ok: true, message: 'Password changed. Other sessions were signed out.', sessionsInvalidated: true });
  });

  return router;
}
