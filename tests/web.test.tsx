// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities, VoiceSettings } from '../web/src/lib/types';
import { views } from './view-fixtures';

/**
 * Render smoke tests for every view.
 *
 * The API module is mocked with fixtures that mirror the real server responses.
 * These tests catch the failure mode that matters most in a dashboard: a view
 * crashing because a response shape was assumed instead of handled.
 */

const capabilities: Capabilities = {
  provider: { id: 'offline', model: 'jarvis-offline-templates', configured: true, description: 'offline: templates', streaming: false },
  autonomyLevel: 2,
  config: { researchEnabled: false, researchAllowedHosts: [], env: 'test', ephemeralSessionSecret: false },
  features: [{ id: 'chat', name: 'Chat', status: 'available', note: 'works' }],
  integrations: [
    {
      platform: 'whatsapp',
      status: 'not_connected',
      officialRoute: 'WhatsApp Business Platform',
      limitation: 'Personal accounts cannot be automated',
    },
  ],
  actions: { available: [], unavailable: [] },
};

const voice: VoiceSettings = {
  mode: 'both',
  language: 'en-IN',
  rate: 1,
  pitch: 1,
  wakeEnabled: false,
  wakePhrase: 'jarvis',
  readNotifications: false,
  privacyMode: true,
  reducedMotion: false,
};

const fixtures: Record<string, unknown> = {
  '/system/briefing': {
    generatedAt: new Date().toISOString(),
    owner: { name: 'Mander' },
    priorities: [{ id: 't1', title: 'Validate the customer problem', priority: 'high', dueAt: null, status: 'pending' }],
    dueToday: [],
    overdue: [],
    stats: { total: 1, open: 1, done: 0, overdue: 0, dueToday: 0, blocked: 0, byStatus: { pending: 1 } },
    pendingApprovals: 1,
    finance: { currency: 'INR', income: '₹0', expense: '₹1,500', net: '-₹1,500', recurringMonthly: '₹1,500', entryCount: 1 },
    memoryCount: 2,
    recentRuns: [],
    warnings: ['Internet research is disabled.'],
    provider: 'offline',
    autonomyLevel: 2,
  },
  '/tasks': {
    tasks: [
      {
        id: 't1',
        title: 'Validate the customer problem',
        description: '',
        status: 'pending',
        priority: 'high',
        due_at: null,
        estimate_minutes: null,
        project: null,
        source: 'manual',
        created_at: new Date().toISOString(),
        completed_at: null,
        depends_on: [],
      },
    ],
    stats: { total: 1, open: 1, done: 0, overdue: 0, dueToday: 0, blocked: 0, byStatus: { pending: 1 } },
  },
  '/approvals': { approvals: [], pending: 0 },
  '/approvals/catalogue': { available: [], unavailable: [] },
  '/runs': { runs: [], pendingApprovals: 0 },
  '/system/agents': { agents: [] },
  '/memory': { entries: [], categories: ['preference', 'project'] },
  '/research/notes': { notes: [] },
  '/finance/entries': {
    entries: [],
    summary: {
      currency: 'INR',
      incomeMinor: 0,
      expenseMinor: 0,
      netMinor: 0,
      recurringMonthlyMinor: 0,
      byCategory: [],
      periodFrom: null,
      periodTo: null,
      entryCount: 0,
    },
    currencies: ['INR'],
  },
  '/compliance': { items: [], areas: ['privacy_data'] },
  '/system/audit?limit=120': { entries: [] },
  '/system/usage': {
    requests: 0,
    failures: 0,
    promptTokens: 0,
    completionTokens: 0,
    costUsdEstimate: 0,
    avgLatencyMs: 0,
    last7Days: [],
    pricing: { inputPerMTok: 0.15, outputPerMTok: 0.6, model: 'offline' },
    note: 'Estimates only.',
  },
  '/system/settings': { voice, autonomyLevel: 2, autonomyDefault: 2 },
  '/chat/conversations': { conversations: [] },
  '/auth/status': { needsSetup: false, authenticated: true, user: { id: 'u1', email: 'o@example.com', name: 'Mander' } },
};

vi.mock('../web/src/lib/api', async () => {
  const actual = await vi.importActual<typeof import('../web/src/lib/api')>('../web/src/lib/api');
  return {
    ...actual,
    api: {
      get: vi.fn(async (path: string) => {
        if (path in fixtures) return fixtures[path];
        throw new Error(`Unexpected GET ${path}`);
      }),
      post: vi.fn(async () => ({ ok: true })),
      patch: vi.fn(async () => ({ ok: true })),
      del: vi.fn(async () => ({ ok: true })),
    },
    streamChat: vi.fn(async () => undefined),
    subscribeRunEvents: vi.fn(() => () => undefined),
  };
});

afterEach(() => cleanup());

describe('view rendering with real response shapes', () => {
  it('renders the daily briefing', async () => {
    render(<views.default.Briefing onNavigate={() => undefined} />);
    expect(await screen.findByText('Validate the customer problem')).toBeTruthy();
    expect(screen.getAllByText(/Internet research is disabled/).length).toBeGreaterThan(0);
  });

  it('renders tasks', async () => {
    render(<views.default.Tasks />);
    expect(await screen.findByText('Validate the customer problem')).toBeTruthy();
    expect(screen.getByText('New task')).toBeTruthy();
  });

  it('renders the approval centre', async () => {
    render(<views.default.Approvals onChanged={() => undefined} />);
    expect(await screen.findByText('Waiting for you')).toBeTruthy();
    expect(screen.getByText('Deliberately unavailable')).toBeTruthy();
  });

  it('renders workflows and shows the honest empty state', async () => {
    render(<views.default.Workflows />);
    expect(await screen.findByText('No runs yet')).toBeTruthy();
  });

  it('renders agents with planning honesty', async () => {
    render(<views.default.Agents capabilities={capabilities} />);
    await waitFor(() => expect(screen.getByText('Not implemented')).toBeTruthy());
  });

  it('renders memory', async () => {
    render(<views.default.Memory />);
    expect(await screen.findByText('Nothing remembered yet')).toBeTruthy();
  });

  it('renders research', async () => {
    render(<views.default.Research />);
    expect(await screen.findByText('No research stored')).toBeTruthy();
  });

  it('renders finance', async () => {
    render(<views.default.Finance />);
    expect(await screen.findByText('No entries')).toBeTruthy();
    expect(screen.getByText('Cash-flow & runway forecast')).toBeTruthy();
  });

  it('renders compliance', async () => {
    render(<views.default.Compliance />);
    expect(await screen.findByText('Nothing tracked yet')).toBeTruthy();
  });

  it('renders settings and security', async () => {
    render(
      <views.default.Security
        capabilities={capabilities}
        voice={voice}
        autonomy={2}
        onApplySettings={async () => undefined}
        onSettingsReload={() => undefined}
      />,
    );
    expect(await screen.findByText('Audit trail')).toBeTruthy();
    expect(screen.getByText(/Speaker verification is not implemented/)).toBeTruthy();
  });

  it('renders the conversation view and reports voice limits', async () => {
    render(
      <views.default.Chat
        voice={voice}
        capabilities={capabilities}
        onVoiceChange={async () => undefined}
        onAfterTurn={() => undefined}
      />,
    );
    expect(await screen.findByText('Say something, or type')).toBeTruthy();
    expect(screen.getByText(/Speaker verification is not implemented/)).toBeTruthy();
  });
});
