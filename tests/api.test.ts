import type { Express } from 'express';
import request from 'supertest';
import { beforeAll, describe, expect, it } from 'vitest';
import { buildApp } from '../server/app.js';
import { OfflineProvider } from '../server/ai/providers/offline.js';
import { loadConfig } from '../server/config.js';
import { closeDatabase, initDatabase } from '../server/db/index.js';

let app: Express;
let agent: ReturnType<typeof request.agent>;

const STATE_CHANGING_HEADER = { 'x-jarvis-request': '1' };

beforeAll(async () => {
  const config = loadConfig({
    NODE_ENV: 'test',
    AI_PROVIDER: 'offline',
    SESSION_SECRET: 'test-secret-that-is-long-enough-for-validation',
    DATA_DIR: 'data',
    AUTONOMY_LEVEL: '2',
  } as NodeJS.ProcessEnv);
  initDatabase(':memory:');
  app = await buildApp({ config, provider: new OfflineProvider() }, { frontend: false });
  agent = request.agent(app);
  return () => closeDatabase();
});

describe('health and auth', () => {
  it('reports health with a database check', async () => {
    const res = await request(app).get('/api/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    expect(res.body.checks.database).toBe('ok');
    expect(res.body.checks.aiProvider).toBe('ok');
  });

  it('requires the request header on state-changing calls', async () => {
    const res = await request(app).post('/api/auth/setup').send({
      email: 'owner@example.com',
      name: 'Owner',
      password: 'goodpassword1',
    });
    expect(res.status).toBe(403);
    expect(res.body.error).toBe('missing_request_header');
  });

  it('rejects a weak password', async () => {
    const res = await request(app)
      .post('/api/auth/setup')
      .set(STATE_CHANGING_HEADER)
      .send({ email: 'owner@example.com', name: 'Owner', password: 'weak' });
    expect(res.status).toBe(400);
    expect(res.body.error).toBe('weak_password');
  });

  it('creates the owner once and starts a session', async () => {
    const res = await agent
      .post('/api/auth/setup')
      .set(STATE_CHANGING_HEADER)
      .send({ email: 'owner@example.com', name: 'Owner', password: 'goodpassword1' });
    expect(res.status).toBe(201);
    expect(res.headers['set-cookie']?.[0]).toContain('jarvis_session');
    expect(res.headers['set-cookie']?.[0]).toContain('HttpOnly');

    const again = await request(app)
      .post('/api/auth/setup')
      .set(STATE_CHANGING_HEADER)
      .send({ email: 'other@example.com', name: 'Other', password: 'goodpassword1' });
    expect(again.status).toBe(409);
  });

  it('blocks unauthenticated access to private data', async () => {
    const res = await request(app).get('/api/tasks');
    expect(res.status).toBe(401);
  });

  it('signs the owner in and out', async () => {
    const me = await agent.get('/api/auth/me');
    expect(me.status).toBe(200);
    expect(me.body.user.email).toBe('owner@example.com');

    const wrong = await request(app)
      .post('/api/auth/login')
      .set(STATE_CHANGING_HEADER)
      .send({ email: 'owner@example.com', password: 'nope-nope-nope' });
    expect(wrong.status).toBe(401);
    expect(wrong.body.error).toBe('invalid_credentials');
  });
});

describe('tasks', () => {
  it('creates, lists and completes a task', async () => {
    const created = await agent
      .post('/api/tasks')
      .set(STATE_CHANGING_HEADER)
      .send({ title: 'Interview five potential customers', priority: 'high' });
    expect(created.status).toBe(201);
    const id = created.body.task.id as string;

    const list = await agent.get('/api/tasks');
    expect(list.body.tasks.some((t: { id: string }) => t.id === id)).toBe(true);

    const done = await agent.patch(`/api/tasks/${id}`).set(STATE_CHANGING_HEADER).send({ status: 'done' });
    expect(done.status).toBe(200);
    expect(done.body.task.status).toBe('done');
    expect(done.body.task.completed_at).toBeTruthy();
  });

  it('blocks completing a task whose dependency is open', async () => {
    const dep = await agent
      .post('/api/tasks')
      .set(STATE_CHANGING_HEADER)
      .send({ title: 'Dependency task' });
    const child = await agent
      .post('/api/tasks')
      .set(STATE_CHANGING_HEADER)
      .send({ title: 'Dependent task', dependsOn: [dep.body.task.id] });
    const res = await agent
      .patch(`/api/tasks/${child.body.task.id}`)
      .set(STATE_CHANGING_HEADER)
      .send({ status: 'done' });
    expect(res.status).toBe(409);
    expect(res.body.error).toBe('dependency_blocked');
  });

  it('validates input', async () => {
    const res = await agent.post('/api/tasks').set(STATE_CHANGING_HEADER).send({ title: 'x' });
    expect(res.status).toBe(400);
  });
});

describe('chat orchestration', () => {
  it('streams a turn, records a run and produces no fabricated actions', async () => {
    const res = await agent
      .post('/api/chat/message')
      .set(STATE_CHANGING_HEADER)
      .set('accept', 'text/event-stream')
      .send({ text: 'I want to start an AI company but I do not know where to begin.' });

    expect(res.status).toBe(200);
    expect(res.headers['content-type']).toContain('text/event-stream');
    expect(res.text).toContain('event: run');
    expect(res.text).toContain('event: delta');
    expect(res.text).toContain('event: done');
    expect(res.text).toContain('offline');

    const doneLine = res.text
      .split('\n\n')
      .find((block) => block.startsWith('event: done'));
    expect(doneLine).toBeTruthy();
    const payload = JSON.parse(doneLine!.split('data: ')[1]!) as { status: string; runId: string; text: string };
    expect(payload.status).toBe('completed');
    expect(payload.text.length).toBeGreaterThan(50);

    const runs = await agent.get('/api/runs');
    const run = runs.body.runs.find((r: { id: string }) => r.id === payload.runId);
    expect(run.status).toBe('completed');
    expect(run.stage_done).toBeGreaterThan(2);

    const detail = await agent.get(`/api/runs/${payload.runId}`);
    const stages = detail.body.events.map((e: { stage: string }) => e.stage);
    expect(stages).toContain('routed');
    expect(stages).toContain('generating');
    expect(stages).toContain('validating');
  });

  it('creates a task from an imperative message at autonomy level 2', async () => {
    const before = (await agent.get('/api/tasks')).body.tasks.length;
    const res = await agent
      .post('/api/chat/message')
      .set(STATE_CHANGING_HEADER)
      .send({ text: 'create a task to validate the pricing hypothesis tomorrow' });
    expect(res.status).toBe(200);
    expect(res.text).toContain('event: action');
    const after = (await agent.get('/api/tasks')).body.tasks;
    expect(after.length).toBe(before + 1);
    expect(after.some((t: { title: string }) => t.title.includes('pricing hypothesis'))).toBe(true);
  });

  it('queues actions for approval at autonomy level 1', async () => {
    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 1 });
    const pendingBefore = (await agent.get('/api/approvals?status=pending')).body.pending;

    await agent
      .post('/api/chat/message')
      .set(STATE_CHANGING_HEADER)
      .send({ text: 'create a task to register the company name' });

    const pending = (await agent.get('/api/approvals?status=pending')).body;
    expect(pending.pending).toBe(pendingBefore + 1);
    const approval = pending.approvals.find((a: { summary: string }) => a.summary.includes('register the company name'));
    expect(approval).toBeTruthy();
    expect(approval.status).toBe('pending');

    const decision = await agent
      .post(`/api/approvals/${approval.id}/decide`)
      .set(STATE_CHANGING_HEADER)
      .send({ decision: 'approve' });
    expect(decision.status).toBe(200);
    expect(decision.body.status).toBe('approved');

    const tasks = (await agent.get('/api/tasks')).body.tasks;
    expect(tasks.some((t: { title: string }) => t.title.includes('register the company name'))).toBe(true);

    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 2 });
  });

  it('rejects an approval without executing it', async () => {
    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 1 });
    await agent
      .post('/api/chat/message')
      .set(STATE_CHANGING_HEADER)
      .send({ text: 'create a task to buy a domain name' });
    const pending = (await agent.get('/api/approvals?status=pending')).body.approvals as Array<{
      id: string;
      summary: string;
    }>;
    const approval = pending.find((a) => a.summary.includes('buy a domain name'));
    expect(approval).toBeTruthy();

    const res = await agent
      .post(`/api/approvals/${approval!.id}/decide`)
      .set(STATE_CHANGING_HEADER)
      .send({ decision: 'reject' });
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('rejected');

    const tasks = (await agent.get('/api/tasks')).body.tasks;
    expect(tasks.some((t: { title: string }) => t.title.includes('buy a domain name'))).toBe(false);

    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 2 });
  });

  it('refuses to decide the same approval twice', async () => {
    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 1 });
    await agent
      .post('/api/chat/message')
      .set(STATE_CHANGING_HEADER)
      .send({ text: 'create a task to write the privacy policy' });
    const pending = (await agent.get('/api/approvals?status=pending')).body.approvals as Array<{
      id: string;
      summary: string;
    }>;
    const approval = pending.find((a) => a.summary.includes('privacy policy'))!;
    await agent.post(`/api/approvals/${approval.id}/decide`).set(STATE_CHANGING_HEADER).send({ decision: 'approve' });
    const second = await agent
      .post(`/api/approvals/${approval.id}/decide`)
      .set(STATE_CHANGING_HEADER)
      .send({ decision: 'approve' });
    expect(second.status).toBe(409);
    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 2 });
  });
});

describe('research, finance and compliance', () => {
  it('fails honestly when outbound fetching is disabled', async () => {
    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 1 });
    await agent
      .post('/api/chat/message')
      .set(STATE_CHANGING_HEADER)
      .send({ text: 'research this https://example.com/page for me' });
    const pending = (await agent.get('/api/approvals?status=pending')).body.approvals as Array<{
      id: string;
      action_type: string;
    }>;
    const research = pending.find((a) => a.action_type === 'web_research');
    expect(research).toBeTruthy();

    const res = await agent
      .post(`/api/approvals/${research!.id}/decide`)
      .set(STATE_CHANGING_HEADER)
      .send({ decision: 'approve' });
    expect(res.status).toBe(400);
    const failed = (await agent.get('/api/approvals')).body.approvals.find(
      (a: { id: string }) => a.id === research!.id,
    );
    expect(failed.status).toBe('failed');
    expect(failed.error).toContain('Outbound network access is disabled');

    const notes = (await agent.get('/api/research/notes')).body.notes;
    expect(notes[0].status).toBe('failed');
    await agent.patch('/api/system/settings').set(STATE_CHANGING_HEADER).send({ autonomyLevel: 2 });
  });

  it('records finance entries and computes a forecast', async () => {
    const created = await agent
      .post('/api/finance/entries')
      .set(STATE_CHANGING_HEADER)
      .send({ kind: 'expense', category: 'hosting', amountMinor: 150000, currency: 'INR', recurring: true });
    expect(created.status).toBe(201);

    const summary = await agent.get('/api/finance/entries');
    expect(summary.body.summary.expenseMinor).toBe(150000);
    expect(summary.body.summary.recurringMonthlyMinor).toBe(150000);

    const forecast = await agent.post('/api/finance/forecast').set(STATE_CHANGING_HEADER).send({
      startingCashMinor: 1000000,
      monthlyRevenueMinor: 0,
      monthlyCostsMinor: 150000,
      months: 6,
    });
    expect(forecast.status).toBe(200);
    expect(forecast.body.rows).toHaveLength(6);
    expect(forecast.body.runwayMonths).toBeCloseTo(6.67, 1);
    expect(forecast.body.assumptions.join(' ')).toContain('INR');
  });

  it('validates a compliance item and keeps publication and effective dates separate', async () => {
    const res = await agent
      .post('/api/compliance')
      .set(STATE_CHANGING_HEADER)
      .send({
        jurisdiction: 'IN',
        area: 'privacy_data',
        title: 'Publish a consent notice',
        publishedOn: '2023-08-11',
        effectiveOn: '2025-11-13',
        sourceUrl: 'https://www.meity.gov.in/',
      });
    expect(res.status).toBe(201);
    expect(res.body.item.published_on).toBe('2023-08-11');
    expect(res.body.item.effective_on).toBe('2025-11-13');

    const bad = await agent
      .post('/api/compliance')
      .set(STATE_CHANGING_HEADER)
      .send({ jurisdiction: 'IN', area: 'not_a_real_area', title: 'Nope' });
    expect(bad.status).toBe(400);
  });
});

describe('rate limiting configuration', () => {
  it('does not throttle health checks', async () => {
    for (let i = 0; i < 5; i += 1) {
      const res = await request(app).get('/api/health');
      expect(res.status).toBe(200);
    }
  });
});

describe('secret exposure', () => {
  it('never returns provider credentials or the session secret to the client', async () => {
    const sentinel = 'sk-sentinel-must-never-leak-0123456789';
    const secret = 'session-secret-sentinel-value-0123456789abcdef';
    const config = loadConfig({
      NODE_ENV: 'test',
      AI_PROVIDER: 'openai',
      AI_BASE_URL: 'https://api.example.com/v1',
      AI_MODEL: 'test-model',
      AI_API_KEY: sentinel,
      SESSION_SECRET: secret,
      DATA_DIR: 'data',
    } as NodeJS.ProcessEnv);
    const { OpenAiCompatibleProvider } = await import('../server/ai/providers/openaiCompatible.js');
    const provider = new OpenAiCompatibleProvider({
      id: 'openai',
      baseUrl: config.ai.baseUrl,
      model: config.ai.model,
      apiKey: sentinel,
      timeoutMs: 5000,
      maxOutputTokens: 100,
    });
    const app2 = await buildApp({ config, provider }, { frontend: false });
    const login = await request(app2)
      .post('/api/auth/login')
      .set(STATE_CHANGING_HEADER)
      .send({ email: 'owner@example.com', password: 'goodpassword1' });
    expect(login.status).toBe(200);
    const cookie = login.headers['set-cookie']?.[0]?.split(';')[0] ?? '';
    expect(cookie).toContain('jarvis_session');

    for (const path of ['/api/system/capabilities', '/api/system/settings', '/api/health', '/api/system/briefing']) {
      const res = await request(app2).get(path).set('Cookie', cookie);
      expect(res.status).toBe(200);
      const body = JSON.stringify(res.body);
      expect(body).not.toContain(sentinel);
      expect(body).not.toContain(secret);
      expect(body).not.toMatch(/AI_API_KEY|SESSION_SECRET/);
    }

    // The provider is reported as configured without revealing why.
    const caps = await request(app2).get('/api/system/capabilities').set('Cookie', cookie);
    expect(caps.body.provider.id).toBe('openai');
    expect(caps.body.provider.configured).toBe(true);
  });
});

describe('memory lifecycle', () => {
  it('creates, lists, updates and deletes a memory entry', async () => {
    const created = await agent
      .post('/api/memory')
      .set(STATE_CHANGING_HEADER)
      .send({ category: 'preference', title: 'Default currency', content: 'All budgeting is in INR.' });
    expect(created.status).toBe(201);
    const id = created.body.entry.id as string;
    expect(created.body.entry.source_type).toBe('user');

    const listed = await agent.get('/api/memory?category=preference');
    expect(listed.body.entries.some((e: { id: string }) => e.id === id)).toBe(true);

    const updated = await agent
      .patch(`/api/memory/${id}`)
      .set(STATE_CHANGING_HEADER)
      .send({ content: 'All budgeting is in INR and USD is excluded.' });
    expect(updated.status).toBe(200);
    expect(updated.body.entry.content).toContain('USD');

    const deleted = await agent.del(`/api/memory/${id}`).set(STATE_CHANGING_HEADER);
    expect(deleted.status).toBe(200);
    const after = await agent.get('/api/memory');
    expect(after.body.entries.some((e: { id: string }) => e.id === id)).toBe(false);
  });
});
