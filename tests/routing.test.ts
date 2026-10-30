import { describe, expect, it } from 'vitest';
import { detectLanguage, intentLabel, routeIntent } from '../server/ai/routing.js';
import { proposeActions } from '../server/ai/orchestrator.js';
import { getAgent } from '../server/agents/registry.js';
import { loadConfig } from '../server/config.js';

const config = loadConfig({ NODE_ENV: 'test', AI_PROVIDER: 'offline', AUTONOMY_LEVEL: '2' } as NodeJS.ProcessEnv);

describe('language detection', () => {
  it('detects Devanagari Hindi', () => {
    expect(detectLanguage('मुझे एक नया व्यवसाय शुरू करना है')).toBe('hi');
  });

  it('detects Hinglish', () => {
    expect(detectLanguage('mujhe ek startup shuru karna hai, kya karna chahiye')).toBe('hinglish');
  });

  it('detects English', () => {
    expect(detectLanguage('I want to start a company and I need a plan')).toBe('en');
  });
});

describe('intent routing', () => {
  it('routes legal questions to the compliance specialist', () => {
    const route = routeIntent('What GST registration do I need for my company in India?');
    expect(route.agentId).toBe('legal-compliance');
    expect(route.confidence).toBeGreaterThan(0.4);
    expect(route.reasons.length).toBeGreaterThan(0);
  });

  it('routes runway questions to finance', () => {
    expect(routeIntent('calculate my runway and monthly cash flow').agentId).toBe('financial-planning');
  });

  it('falls back to the orchestrator when nothing matches', () => {
    const route = routeIntent('hmm');
    expect(route.agentId).toBe('orchestrator');
    expect(route.confidence).toBeLessThanOrEqual(0.3);
  });

  it('always returns a registered agent', () => {
    for (const text of ['hello', 'fix my react bug', 'write an instagram post', 'check my audit log']) {
      expect(getAgent(routeIntent(text).agentId)).toBeDefined();
    }
  });

  it('labels intents for telemetry', () => {
    expect(intentLabel('create task to call the CA')).toBe('create_task');
    expect(intentLabel('remember that I prefer INR')).toBe('save_memory');
    expect(intentLabel('what is my runway')).toBe('finance');
  });
});

describe('action proposals', () => {
  const orchestrator = getAgent('orchestrator')!;

  it('proposes a task only for imperative phrasing', () => {
    const drafts = proposeActions('Jarvis, create a task to interview 5 customers tomorrow', orchestrator, config);
    expect(drafts).toHaveLength(1);
    expect(drafts[0]?.actionType).toBe('create_task');
    const payload = drafts[0]?.payload as { title: string; dueAt?: string };
    expect(payload.title).toContain('interview 5 customers');
    expect(payload.dueAt).toBeTruthy();
  });

  it('does not create a task from a passing mention', () => {
    const drafts = proposeActions('I have a lot of tasks today, which one matters most?', orchestrator, config);
    expect(drafts.filter((d) => d.actionType === 'create_task')).toHaveLength(0);
  });

  it('proposes memory for remember phrasing (English and Hinglish)', () => {
    expect(proposeActions('remember that all invoices are in INR', orchestrator, config)[0]?.actionType).toBe('save_memory');
    expect(proposeActions('yaad rakho ki main Hisar se hoon', orchestrator, config)[0]?.actionType).toBe('save_memory');
  });

  it('proposes a finance entry with an integer minor-unit amount', () => {
    const drafts = proposeActions('spent 1499.50 on api credits', orchestrator, config);
    expect(drafts).toHaveLength(1);
    const payload = drafts[0]?.payload as { amountMinor: number; kind: string };
    expect(payload.amountMinor).toBe(149950);
    expect(payload.kind).toBe('expense');
  });

  it('proposes research only when a URL is present', () => {
    const withUrl = proposeActions('research this https://example.com/article please', orchestrator, config);
    expect(withUrl[0]?.actionType).toBe('web_research');
    expect(withUrl[0]?.requiresApproval).toBe(true);
    expect(proposeActions('research the AI market for me', orchestrator, config)).toHaveLength(0);
  });

  it('never proposes actions an agent is not allowed to use', () => {
    const social = getAgent('social-media')!;
    const drafts = proposeActions('create a task to post tomorrow', social, config);
    expect(drafts).toHaveLength(0);
  });

  it('queues every change at autonomy level 1', () => {
    const strict = loadConfig({ NODE_ENV: 'test', AI_PROVIDER: 'offline', AUTONOMY_LEVEL: '1' } as NodeJS.ProcessEnv);
    const drafts = proposeActions('create a task to call the accountant', orchestrator, strict);
    expect(drafts[0]?.requiresApproval).toBe(true);
  });
});
