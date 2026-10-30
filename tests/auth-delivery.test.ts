/**
 * Honesty rules for one-time code delivery.
 *
 * A production server with no SMTP/Twilio credentials must say so: the start
 * screen reports the method as unavailable and the API refuses with 503. No
 * code is created, nothing is "sent", and no development code is ever revealed.
 */
import type { Express } from 'express';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { buildApp } from '../server/app.js';
import { OfflineProvider } from '../server/ai/providers/offline.js';
import { loadConfig, type AppConfig } from '../server/config.js';
import { closeDatabase, initDatabase } from '../server/db/index.js';
import { deliverySummary, emailDeliveryMode, smsDeliveryMode } from '../server/services/delivery.js';

const HEADER = { 'x-jarvis-request': '1' };

let app: Express;
let config: AppConfig;

beforeAll(async () => {
  config = loadConfig({
    NODE_ENV: 'production',
    AI_PROVIDER: 'offline',
    SESSION_SECRET: 'production-secret-that-is-long-enough-for-validation',
    DATA_DIR: 'data',
    AUTONOMY_LEVEL: '2',
    AUTH_RATE_LIMIT: '1000',
    PORT: '5199',
  } as NodeJS.ProcessEnv);
  initDatabase(':memory:');
  app = await buildApp({ config, provider: new OfflineProvider() }, { frontend: false });
});

afterAll(() => closeDatabase());

describe('delivery modes', () => {
  it('treats console delivery as development-only', () => {
    expect(emailDeliveryMode(config)).toBe('not-configured');
    expect(smsDeliveryMode(config)).toBe('not-configured');
    expect(deliverySummary(config)).toEqual({
      email: 'not-configured',
      sms: 'not-configured',
      emailTransport: null,
      smsTransport: null,
    });
  });

  it('reports ready when a provider is configured', () => {
    const smtp = { ...config, mail: { ...config.mail, provider: 'smtp' as const } };
    const twilio = { ...config, sms: { ...config.sms, provider: 'twilio' as const } };
    expect(deliverySummary(smtp).email).toBe('ready');
    expect(deliverySummary(smtp).emailTransport).toBe('smtp');
    expect(deliverySummary(twilio).sms).toBe('ready');
    expect(deliverySummary(twilio).smsTransport).toBe('twilio');
  });

  it('keeps console delivery in development', () => {
    const dev = { ...config, isProduction: false };
    expect(emailDeliveryMode(dev)).toBe('console');
    expect(smsDeliveryMode(dev)).toBe('console');
  });
});

describe('production start screen without mail credentials', () => {
  it('advertises only methods that can actually work', async () => {
    const res = await request(app).get('/api/auth/status').expect(200);
    expect(res.body.needsSetup).toBe(true);
    expect(res.body.methods.password).toBe(true);
    expect(res.body.methods.emailOtp).toBe(false);
    expect(res.body.methods.phoneOtp).toBe(false);
    expect(res.body.methods.emailOtpMode).toBe('not-configured');
    expect(res.body.methods.phoneOtpMode).toBe('not-configured');
    expect(res.body.methods.devCodesRevealed).toBe(false);
    expect(res.body.methods.googleOAuth).toBe(false);
  });

  it('refuses to email a code instead of pretending', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: 'owner@example.com', purpose: 'signup' })
      .expect(503);
    expect(res.body.error).toBe('delivery_not_configured');
    expect(res.body.message).toContain('MAIL_PROVIDER=smtp');
    expect(res.body.devCode).toBeUndefined();
  });

  it('refuses to text a code instead of pretending', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'phone', target: '9876543210', purpose: 'signup' })
      .expect(503);
    expect(res.body.error).toBe('delivery_not_configured');
    expect(res.body.message).toContain('SMS_PROVIDER=twilio');
  });

  it('never returns a development code from a production build', async () => {
    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: 'owner@example.com', purpose: 'signup' });
    expect(JSON.stringify(res.body)).not.toContain('devCode":"');
    expect(res.body.challengeId).toBeUndefined();
  });
});

describe('password sign-in still works in production', () => {
  it('creates the owner with a password', async () => {
    const res = await request(app)
      .post('/api/auth/setup')
      .set(HEADER)
      .send({ email: 'Owner@Example.com', name: 'Owner', password: 'correct horse battery 9' })
      .expect(201);
    expect(res.body.user.email).toBe('owner@example.com');
    expect(res.body.user.hasPassword).toBe(true);
    expect(res.headers['set-cookie']?.[0]).toContain('HttpOnly');
  });

  it('accepts that password and rejects a wrong one', async () => {
    await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ identifier: 'owner@example.com', password: 'correct horse battery 9' })
      .expect(200);

    const bad = await request(app)
      .post('/api/auth/login')
      .set(HEADER)
      .send({ identifier: 'OWNER@example.com', password: 'wrong password here 1' })
      .expect(401);
    expect(bad.body.error).toBe('invalid_credentials');
  });

  it('refuses to create a second owner by code or password', async () => {
    await request(app)
      .post('/api/auth/setup')
      .set(HEADER)
      .send({ email: 'second@example.com', name: 'Second', password: 'another password 7' })
      .expect(409);

    const res = await request(app)
      .post('/api/auth/otp/request')
      .set(HEADER)
      .send({ channel: 'email', target: 'second@example.com', purpose: 'signup' })
      .expect(409);
    expect(res.body.error).toBe('already_configured');
  });
});
