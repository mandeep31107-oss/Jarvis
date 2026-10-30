import { useCallback, useEffect, useMemo, useState } from 'react';
import { AuthPage } from './components/AuthPage';
import { Shell, VIEW_META, type ViewId } from './components/Shell';
import { Notice, Pill, Spinner } from './components/ui';
import { api } from './lib/api';
import { useAsync, useInterval } from './lib/hooks';
import type { AuthStatus, Capabilities, User, VoiceSettings } from './lib/types';
import BriefingView from './views/BriefingView';
import ChatView from './views/ChatView';
import TasksView from './views/TasksView';
import ApprovalsView from './views/ApprovalsView';
import WorkflowsView from './views/WorkflowsView';
import AgentsView from './views/AgentsView';
import MemoryView from './views/MemoryView';
import ResearchView from './views/ResearchView';
import FinanceView from './views/FinanceView';
import ComplianceView from './views/ComplianceView';
import SecurityView from './views/SecurityView';

interface SettingsResponse {
  voice: VoiceSettings;
  autonomyLevel: 1 | 2 | 3;
  autonomyDefault: 1 | 2 | 3;
}

const DEFAULT_VOICE: VoiceSettings = {
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

const EMPTY_METHODS: AuthStatus['methods'] = {
  password: true,
  emailOtp: false,
  phoneOtp: false,
  emailOtpMode: 'not-configured',
  phoneOtpMode: 'not-configured',
  emailTransport: null,
  smsTransport: null,
  devCodesRevealed: false,
  googleOAuth: false,
};

export default function App() {
  const auth = useAsync<AuthStatus>(() => api.get<AuthStatus>('/auth/status'), []);

  if (auth.loading) {
    return (
      <div className="auth">
        <Spinner label="Establishing secure link…" />
      </div>
    );
  }

  if (auth.error) {
    return (
      <div className="auth">
        <div className="auth__card">
          <div className="auth__mark">
            <div className="brand__mark" aria-hidden="true">!</div>
            <div>
              <div className="auth__title">Connection problem</div>
              <div className="auth__sub">The server is not reachable.</div>
            </div>
          </div>
          <Notice tone="red" title="Cannot reach the backend">
            {auth.error}. Check that the server is running and reload the page.
          </Notice>
        </div>
      </div>
    );
  }

  const status = auth.data;

  if (status?.needsSetup) {
    return (
      <AuthPage
        initialTab="signup"
        allowSignup
        methods={status.methods}
        policy={status.otp}
        onSuccess={auth.reload}
      />
    );
  }

  if (!status?.authenticated || !status.user) {
    return (
      <AuthPage
        initialTab="login"
        allowSignup={false}
        methods={status?.methods ?? EMPTY_METHODS}
        policy={status?.otp}
        onSuccess={auth.reload}
      />
    );
  }

  return <Dashboard user={status.user} onSignedOut={auth.reload} />;
}

function Dashboard({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  const initialView = (): ViewId => {
    const hash = window.location.hash.replace('#/', '');
    return (hash && hash in VIEW_META ? hash : 'briefing') as ViewId;
  };
  const [view, setView] = useState<ViewId>(initialView);

  const settings = useAsync<SettingsResponse>(() => api.get<SettingsResponse>('/system/settings'), []);
  const capabilities = useAsync<Capabilities>(() => api.get<Capabilities>('/system/capabilities'), []);
  const approvals = useAsync<{ pending: number }>(() => api.get<{ pending: number }>('/approvals?status=pending'), []);

  useInterval(() => approvals.reload(), 25_000);

  const navigate = useCallback((next: ViewId) => {
    setView(next);
    window.location.hash = `#/${next}`;
  }, []);

  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.replace('#/', '');
      if (hash && hash in VIEW_META) setView(hash as ViewId);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const voice = settings.data?.voice ?? DEFAULT_VOICE;
  const autonomy = settings.data?.autonomyLevel ?? capabilities.data?.autonomyLevel ?? 2;

  useEffect(() => {
    document.body.classList.toggle('reduced-motion', voice.reducedMotion);
  }, [voice.reducedMotion]);

  const applySettings = useCallback(
    async (patch: { voice?: Partial<VoiceSettings>; autonomyLevel?: 1 | 2 | 3 }) => {
      await api.patch('/system/settings', patch);
      settings.reload();
      capabilities.reload();
    },
    [settings, capabilities],
  );

  const afterTurn = useCallback(() => {
    approvals.reload();
    capabilities.reload();
  }, [approvals, capabilities]);

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout');
    } finally {
      onSignedOut();
    }
  }, [onSignedOut]);

  const headerMeta = useMemo(
    () => (
      <>
        <Pill tone={capabilities.data?.provider.configured ? 'green' : 'amber'}>
          {capabilities.data ? (capabilities.data.provider.configured ? 'model ready' : 'model offline') : 'checking model'}
        </Pill>
        <Pill tone="cyan">autonomy L{autonomy}</Pill>
        {approvals.data?.pending ? <Pill tone="amber">{approvals.data.pending} to approve</Pill> : null}
        <Pill tone="muted">{new Date().toLocaleDateString(undefined, { day: '2-digit', month: 'short' })}</Pill>
      </>
    ),
    [capabilities.data, autonomy, approvals.data],
  );

  return (
    <Shell
      user={user}
      view={view}
      onNavigate={navigate}
      badge={approvals.data?.pending ?? 0}
      headerMeta={headerMeta}
      onLogout={logout}
    >
      {view === 'briefing' && <BriefingView onNavigate={navigate} />}
      {view === 'chat' && (
        <ChatView
          voice={voice}
          capabilities={capabilities.data}
          onVoiceChange={(patch) => applySettings({ voice: patch })}
          onAfterTurn={afterTurn}
        />
      )}
      {view === 'tasks' && <TasksView />}
      {view === 'approvals' && <ApprovalsView onChanged={() => { approvals.reload(); }} />}
      {view === 'workflows' && <WorkflowsView />}
      {view === 'agents' && <AgentsView capabilities={capabilities.data} />}
      {view === 'memory' && <MemoryView />}
      {view === 'research' && <ResearchView />}
      {view === 'finance' && <FinanceView />}
      {view === 'compliance' && <ComplianceView />}
      {view === 'security' && (
        <SecurityView
          user={user}
          capabilities={capabilities.data}
          voice={voice}
          autonomy={autonomy}
          onApplySettings={applySettings}
          onSettingsReload={() => { settings.reload(); capabilities.reload(); }}
        />
      )}
    </Shell>
  );
}
