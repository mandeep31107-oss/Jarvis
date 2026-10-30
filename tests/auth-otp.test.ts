/**
 * Email one-time codes, end to end.
 *
 * The development server uses the console transport, so the code is returned to
 * the browser on purpose (and logged). Everything else behaves exactly like
 * production: single use, expiring, attempt-limited and rate-limited.
 */
import type { Express } from 'express';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { buildApp } from '../server/app.js';
import { OfflineProvider } from '../server/ai/providers/offline.js';
import { loadConfig } from '../server/config.js';
import { closeDatabase, initDatabase, run } from '../server/db/index.js';
import { maskIdentifier, normalizeEmail, normalizePhone } from '../server/services/otp.js';

const HEADER = { 'x-jarvis-request': '1' };
const SECRET = 'test-secret-that-is-long-enough-for-validation';
const OWNER = 'owner@example.com';

let app: Express;
let agent: ReturnType<typeof request.agent>;

beforeAll(async () => {
  const config = loadConfig({
    NODE_ENV: 'test',
    AI_PROVIDER: 'offline',
    SESSION_SECRET: SECRET,
    DATA_DIR: 'data',
    AUTONOMY_LEVEL: '2',
    AUTH_RATE_LIMIT: '1000',
    DEFAULT_COUNTRY_CODE: '+91',
  } as NodeJS.ProcessEnv);
  initDatabase(':memory:');
  app = await buildApp({ config, provider: new OfflineProvider() }, { frontend: false });
  agent = request.agent(app);
});

afterAll(() => closeDatabase());

/** Moves every challenge for a target out of the rate-limit window. */
function clearWindow(target: string): void {
  run('UPDATE otp_challenges SET created_at = ? WHERE target = ?', [
    new Date(Date.now() - 20 * 60_000).toISOString(),
    target,
  ]);
}

describe('identifier normalisation', () => {
  it('normalises and rejects email addresses', () => {
    expect(normalizeEmail('  Owner@Example.COM ')).toBe('owner@example.com');
    expect(normalizeEmail('a@b.co')).toBe('a@b.co');
    expect(normalizeEmail('no-at-sign.com')).toBeNull();
    expect(normalizeEmail('two..dots@example.com')).toBeNull();
    expect(normalizeEmail('space in@example.com')).toBeNull();
    expect(normalizeEmail(42)).toBeNull();
  });

  it('normalises Indian and international mobile numbers to E.164', () => {
    expect(normalizePhone('9876543210')).toBe('+919876543210');
    expect(normalizePhone('09876543210')).toBe('+919876543210');
    expect(normalizePhone('+91 98765 43210')).toBe('+919876543210');
    expect(normalizePhone('+91 98765-43210')).toBe('+919876543210');
    expect(normalizePhone('+1 415 555 0132')).toBe('+14155550132');
    expect(normalizePhone('98765')).toBeNull();
    expect(normalizePhone('not a phone')).toBeNull();
    expect(normalizePhone(null)).toBeNull();
  });

  it('masks identifiers before they are shown or logged', () => {
    expect(maskIdentifier('email', 'owner@example.com')).toBe('o****@example.com');
    expect(maskIdentifier('phone', '+919876543210')).toBe('+91*******210');
  });
});

describe('owner sign-up with an email code', () => {
  let challengeId: string;
  let code: string;

  it('sends a code in development without pretending it is real mail', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: ' Owner@Example.com ', purpose: 'signup' })
      .expect(201);

    expect(res.body.transport).toBe('console');
    expect(res.body.maskedTarget).toBe('o****@example.com');
    expect(res.body.expiresInSeconds).toBe(300);
    expect(res.body.message).toContain('Development mode');
    expect(res.body.devCode).toMatch(/^\d{6}$/);
    challengeId = res.body.challengeId;
    code = res.body.devCode;

    // The challenge exists, but no account does yet.
    const status = await request(app).get('/api/auth/status').expect(200);
    expect(status.body.needsSetup).toBe(true);
  });

  it('rejects a wrong code and reports the attempts left', async () => {
    const res = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId, code: '000000' === code ? '111111' : '000000', name: 'Owner' })
      .expect(400);
    expect(res.body.error).toBe('invalid_code');
    expect(res.body.attemptsRemaining).toBe(4);
  });

  it('creates the owner when the code is right', async () => {
    const res = await agent
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId, code, name: 'Owner' })
      .expect(201);

    expect(res.body.user.email).toBe(OWNER);
    expect(res.body.user.emailVerified).toBe(true);
    expect(res.body.user.hasPassword).toBe(false);
    expect(res.body.nextStep).toBe('set_password');
    expect(res.headers['set-cookie']?.[0]).toContain('jarvis_session');
    expect(String(res.headers['set-cookie']?.[0])).toContain('HttpOnly');
  });

  it('cannot reuse a consumed code', async () => {
    const res = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId, code })
      .expect(410);
    expect(res.body.error).toBe('already_used');
  });

  it('treats a passwordless account honestly at the password form', async () => {
    const res = await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ identifier: OWNER, password: 'some password 1' })
      .expect(401);
    expect(res.body.message).toContain('no password');
  });
});

describe('password management for code-created accounts', () => {
  it('requires a session', async () => {
    await request(app)
      .post('/api/auth/change-password')
      .set(HEADER)
      .send({ newPassword: 'a long enough pass 1' })
      .expect(401);
  });

  it('sets a first password without asking for a current one', async () => {
    const res = await agent
      .post('/api/auth/change-password')
      .set(HEADER)
      .send({ newPassword: 'a long enough pass 1' })
      .expect(200);
    expect(res.body.message).toContain('Password set');
  });

  it('signs in with that password using either identifier field', async () => {
    await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ identifier: OWNER, password: 'a long enough pass 1' })
      .expect(200);
    await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ email: OWNER, password: 'a long enough pass 1' })
      .expect(200);
  });

  it('insists on the current password once one exists', async () => {
    const missing = await agent
      .post('/api/auth/change-password')
      .set(HEADER)
      .send({ newPassword: 'another long pass 2' })
      .expect(400);
    expect(missing.body.error).toBe('current_password_required');

    const wrong = await agent
      .post('/api/auth/change-password')
      .set(HEADER)
      .send({ currentPassword: 'not the password 0', newPassword: 'another long pass 2' })
      .expect(401);
    expect(wrong.body.error).toBe('invalid_credentials');

    const weak = await agent
      .post('/api/auth/change-password')
      .set(HEADER)
      .send({ currentPassword: 'a long enough pass 1', newPassword: 'short' })
      .expect(400);
    expect(weak.body.error).toBe('weak_password');
  });
});

describe('sign-in with an email code', () => {
  it('signs an existing owner in and marks the address verified', async () => {
    const requested = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: OWNER, purpose: 'login' })
      .expect(201);
    expect(requested.body.devCode).toMatch(/^\d{6}$/);

    const verified = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: requested.body.challengeId, code: requested.body.devCode })
      .expect(200);
    expect(verified.body.user.email).toBe(OWNER);
    expect(verified.body.nextStep).toBeNull();
    expect(verified.headers['set-cookie']?.[0]).toContain('jarvis_session');
  });

  it('never reveals an account that does not exist', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: 'nobody@example.com', purpose: 'login' })
      .expect(404);
    expect(res.body.error).toBe('no_account');
    expect(res.body.challengeId).toBeUndefined();
  });

  it('rejects malformed identifiers before sending anything', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: 'not-an-email', purpose: 'login' })
      .expect(400);
    expect(res.body.error).toBe('invalid_identifier');
  });
});

describe('abuse limits', () => {
  it('locks a challenge after five wrong attempts', async () => {
    clearWindow(OWNER);
    const requested = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: OWNER, purpose: 'login' })
      .expect(201);
    const wrong = requested.body.devCode === '000000' ? '111111' : '000000';

    for (let attempt = 1; attempt <= 4; attempt += 1) {
      const res = await request(app)
        .post('/api/auth/otp/verify')
        .set(HEADER)
        .send({ challengeId: requested.body.challengeId, code: wrong })
        .expect(400);
      expect(res.body.error).toBe('invalid_code');
      expect(res.body.attemptsRemaining).toBe(5 - attempt);
    }

    const locked = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: requested.body.challengeId, code: wrong })
      .expect(429);
    expect(locked.body.error).toBe('too_many_attempts');

    // Even the correct code cannot revive a locked challenge.
    const tooLate = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: requested.body.challengeId, code: requested.body.devCode })
      .expect(429);
    expect(tooLate.body.error).toBe('too_many_attempts');
  });

  it('allows three codes per identifier and then refuses', async () => {
    clearWindow(OWNER);
    for (let i = 0; i < 3; i += 1) {
      await request(app)
        .post('/api/auth/otp/request')
        .set(HEADER)
        .send({ channel: 'email', target: OWNER, purpose: 'login' })
        .expect(201);
    }
    const limited = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: OWNER, purpose: 'login' })
      .expect(429);
    expect(limited.body.error).toBe('rate_limited');
    expect(limited.body.retryAfterSeconds).toBeGreaterThan(0);
  });

  it('expires a code that was never used', async () => {
    clearWindow(OWNER);
    const requested = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: OWNER, purpose: 'login' })
      .expect(201);
    run('UPDATE otp_challenges SET expires_at = ? WHERE id = ?', [
      new Date(Date.now() - 1000).toISOString(),
      requested.body.challengeId,
    ]);
    const res = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: requested.body.challengeId, code: requested.body.devCode })
      .expect(410);
    expect(res.body.error).toBe('expired');
  });

  it('rejects an unknown challenge id', async () => {
    const res = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: '6f1c1a9e-2a3b-4c5d-8e9f-0a1b2c3d4e5f', code: '123456' })
      .expect(400);
    expect(res.body.error).toBe('invalid_challenge');
  });

  it('records every attempt in the audit trail', async () => {
    const res = await agent.get('/api/system/audit?limit=200').expect(200);
    const actions = res.body.entries.map((entry: { action: string }) => entry.action);
    expect(actions).toContain('auth.otp_requested');
    expect(actions).toContain('auth.otp_failed');
    expect(actions).toContain('auth.owner_created');
    expect(actions).toContain('auth.password_set');
  });
});

describe('no password material leaves the server', () => {
  it('never serialises the hash or salt on any auth response', async () => {
    const responses = [
      await request(app).get('/api/auth/status').expect(200),
      await agent.get('/api/auth/me').expect(200),
      await request(app)
        .post('/api/auth/login')
        .set(HEADER)
        .send({ identifier: OWNER, password: 'a long enough pass 1' })
        .expect(200),
    ];
    for (const response of responses) {
      const body = JSON.stringify(response.body);
      expect(body).not.toContain('password_hash');
      expect(body).not.toContain('password_salt');
      expect(body).not.toContain('passwordHash');
    }
  });
});
