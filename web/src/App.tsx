import { useCallback, useEffect, useMemo, useState } from 'react';
import { Shell, VIEW_META, type ViewId } from './components/Shell';
import { Notice, Pill, Spinner } from './components/ui';
import { api, ApiError } from './lib/api';
import { useAsync, useInterval } from './lib/hooks';
import type { Capabilities, User, VoiceSettings } from './lib/types';
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

interface AuthStatus {
  needsSetup: boolean;
  authenticated: boolean;
  user: User | null;
}

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

  if (auth.data?.needsSetup) {
    return <AuthPage mode="setup" onSuccess={auth.reload} />;
  }

  if (!auth.data?.authenticated || !auth.data.user) {
    return <AuthPage mode="login" onSuccess={auth.reload} />;
  }

  return <Dashboard user={auth.data.user} onSignedOut={auth.reload} />;
}

function AuthPage({ mode, onSuccess }: { mode: 'setup' | 'login'; onSuccess: () => void }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === 'setup') {
        await api.post('/auth/setup', { email, name, password });
      } else {
        await api.post('/auth/login', { email, password });
      }
      onSuccess();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed';
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="auth__mark">
          <div className="brand__mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="3.2" />
              <circle cx="12" cy="12" r="8" opacity="0.45" />
              <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" opacity="0.7" />
            </svg>
          </div>
          <div>
            <div className="auth__title">J.A.R.V.I.S.</div>
            <div className="auth__sub">
              {mode === 'setup' ? 'First-run owner enrolment' : 'Owner sign-in'}
            </div>
          </div>
        </div>

        {mode === 'setup' && (
          <Notice tone="cyan" title="This screen appears once">
            Create your owner account. It is stored locally in this installation and there is no
            recovery email or password reset — keep the password safe.
          </Notice>
        )}

        {error && <Notice tone="red" title="Could not continue">{error}</Notice>}

        <form className="auth__form" onSubmit={submit}>
          {mode === 'setup' && (
            <label className="field">
              <span className="field__label">Your name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} required minLength={1} autoComplete="name" />
            </label>
          )}
          <label className="field">
            <span className="field__label">Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="username"
              placeholder="you@example.com"
            />
          </label>
          <label className="field">
            <span className="field__label">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === 'setup' ? 10 : 1}
              autoComplete={mode === 'setup' ? 'new-password' : 'current-password'}
            />
            {mode === 'setup' && (
              <span className="field__hint">At least 10 characters, with a letter and a number.</span>
            )}
          </label>
          <button className="btn btn--primary btn--md" type="submit" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'setup' ? 'Create owner account' : 'Sign in'}
          </button>
        </form>

        <p className="auth__foot">
          Credentials stay on this server. Passwords are hashed with scrypt; the session cookie is
          HTTP-only and SameSite=Strict. J.A.R.V.I.S. never stores passwords in memory or logs.
        </p>
      </div>
    </div>
  );
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
