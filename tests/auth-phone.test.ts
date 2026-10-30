/**
 * Mobile-number sign-up and sign-in with an SMS one-time code.
 *
 * Development uses the console transport, so the code is shown to the browser.
 * The account itself is real: an E.164 phone number, a verified flag, a session
 * and an optional password that also works with the number as the identifier.
 */
import type { Express } from 'express';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { buildApp } from '../server/app.js';
import { OfflineProvider } from '../server/ai/providers/offline.js';
import { loadConfig } from '../server/config.js';
import { closeDatabase, initDatabase } from '../server/db/index.js';

const HEADER = { 'x-jarvis-request': '1' };
const PHONE = '+919876543210';

let app: Express;
let agent: ReturnType<typeof request.agent>;

beforeAll(async () => {
  const config = loadConfig({
    NODE_ENV: 'test',
    AI_PROVIDER: 'offline',
    SESSION_SECRET: 'test-secret-that-is-long-enough-for-validation',
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

describe('owner sign-up with a mobile code', () => {
  it('rejects a number that cannot exist', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'phone', target: '12345', purpose: 'signup' })
      .expect(400);
    expect(res.body.error).toBe('invalid_identifier');
    expect(res.body.message).toContain('98765 43210');
  });

  it('accepts a locally typed number and normalises it to E.164', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'phone', target: '98765 43210', purpose: 'signup' })
      .expect(201);
    expect(res.body.transport).toBe('console');
    expect(res.body.maskedTarget).toBe('+91*******210');
    expect(res.body.devCode).toMatch(/^\d{6}$/);

    const verified = await agent
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: res.body.challengeId, code: res.body.devCode, name: 'Owner' })
      .expect(201);

    expect(verified.body.user.phone).toBe(PHONE);
    expect(verified.body.user.email).toBeNull();
    expect(verified.body.user.phoneVerified).toBe(true);
    expect(verified.body.user.hasPassword).toBe(false);
    expect(verified.body.nextStep).toBe('set_password');
  });

  it('reports the phone-only account through the status endpoint', async () => {
    const status = await request(app).get('/api/auth/status').expect(200);
    expect(status.body.needsSetup).toBe(false);
    expect(status.body.methods.phoneOtp).toBe(true);
    expect(status.body.methods.phoneOtpMode).toBe('console');
  });
});

describe('signing back in', () => {
  it('sets a password from the signed-in session', async () => {
    const res = await agent
      .post('/api/auth/change-password')
      .set(HEADER)
      .send({ newPassword: 'a long enough pass 1' })
      .expect(200);
    expect(res.body.message).toContain('Password set');
  });

  it('accepts the mobile number as the password identifier', async () => {
    await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ identifier: '98765 43210', password: 'a long enough pass 1' })
      .expect(200);
    await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ identifier: PHONE, password: 'a long enough pass 1' })
      .expect(200);
  });

  it('signs in with a fresh SMS code', async () => {
    const requested = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'phone', target: PHONE, purpose: 'login' })
      .expect(201);
    const verified = await request(app)
      .post('/api/auth/otp/verify')
      .set(HEADER)
      .send({ challengeId: requested.body.challengeId, code: requested.body.devCode })
      .expect(200);
    expect(verified.body.user.phone).toBe(PHONE);
  });

  it('does not reveal numbers that are not linked to an account', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'phone', target: '9000000001', purpose: 'login' })
      .expect(404);
    expect(res.body.error).toBe('no_account');
    expect(res.body.message).toContain('Sign in with your email');
  });

  it('refuses a second account once the owner exists', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'phone', target: '9000000002', purpose: 'signup' })
      .expect(409);
    expect(res.body.error).toBe('already_configured');
  });
});
