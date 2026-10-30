import { useState } from 'react';
import { Button, Field, Notice, Panel, Pill, Spinner, Stat, StatusBadge, Toggle, formatDate } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { relativeTime, useAsync } from '../lib/hooks';
import type { AuditEntry, Capabilities, UsageSummary, VoiceSettings } from '../lib/types';

const AUTONOMY: Record<1 | 2 | 3, { title: string; body: string }> = {
  1: {
    title: 'Level 1 — Suggest',
    body: 'Researches, plans and drafts. Every change is queued for your approval, including tasks.',
  },
  2: {
    title: 'Level 2 — Approved execution',
    body: 'Low-risk internal records (tasks, memories, finance entries) are created immediately and audited. External actions still need approval.',
  },
  3: {
    title: 'Level 3 — Bounded automation',
    body: 'Intended for scoped recurring actions. In this build no automation schedule exists yet, so it behaves like level 2 for external actions.',
  },
};

export default function SecurityView({
  capabilities,
  voice,
  autonomy,
  onApplySettings,
  onSettingsReload,
}: {
  capabilities: Capabilities | null;
  voice: VoiceSettings;
  autonomy: 1 | 2 | 3;
  onApplySettings: (patch: { voice?: Partial<VoiceSettings>; autonomyLevel?: 1 | 2 | 3 }) => Promise<void>;
  onSettingsReload: () => void;
}) {
  const audit = useAsync<{ entries: AuditEntry[] }>(() => api.get('/system/audit?limit=120'), []);
  const usage = useAsync<UsageSummary>(() => api.get('/usage'), []);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [passwordMessage, setPasswordMessage] = useState<{ tone: 'green' | 'red'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setPasswordMessage(null);
    try {
      await api.post('/auth/change-password', { currentPassword, newPassword });
      setCurrentPassword('');
      setNewPassword('');
      setPasswordMessage({ tone: 'green', text: 'Password changed. All other sessions were signed out.' });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Could not change the password';
      setPasswordMessage({ tone: 'red', text: message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Settings, security & permissions</h2>
          <p>
            The server holds every credential. The browser never receives an API key, and each
            permission boundary is enforced on the backend.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Button variant="subtle" size="sm" onClick={() => { audit.reload(); usage.reload(); onSettingsReload(); }}>
          Refresh
        </Button>
      </div>

      <div className="grid grid--2">
        <Panel title="Model provider" subtitle="Configured in .env on the server">
          {capabilities ? (
            <>
              <div className="row" style={{ marginBottom: 10 }}>
                <StatusBadge status={capabilities.provider.configured ? 'available' : 'needs_config'} label={capabilities.provider.configured ? 'configured' : 'not configured'} />
                <Pill tone="cyan">{capabilities.provider.id}</Pill>
                <Pill tone="muted">{capabilities.provider.model}</Pill>
                {capabilities.provider.streaming && <Pill tone="green">streaming</Pill>}
              </div>
              <p className="small muted">{capabilities.provider.description}</p>
              <dl className="kv" style={{ marginTop: 12 }}>
                <dt>Environment</dt>
                <dd>{capabilities.config.env}</dd>
                <dt>Outbound research</dt>
                <dd>{capabilities.config.researchEnabled ? 'enabled' : 'disabled'}</dd>
                <dt>Allowed hosts</dt>
                <dd>{capabilities.config.researchAllowedHosts.length > 0 ? capabilities.config.researchAllowedHosts.join(', ') : 'any public host (private IPs blocked)'}</dd>
                <dt>Session secret</dt>
                <dd>
                  {capabilities.config.ephemeralSessionSecret ? (
                    <span style={{ color: 'var(--amber)' }}>ephemeral — sessions reset on restart</span>
                  ) : (
                    'configured'
                  )}
                </dd>
              </dl>
            </>
          ) : (
            <Spinner label="Loading provider status…" />
          )}
        </Panel>

        <Panel title="Autonomy level" subtitle="Applies immediately to new actions">
          <div className="stack">
            {([1, 2, 3] as const).map((level) => (
              <label
                key={level}
                className="agent-card"
                style={{
                  cursor: 'pointer',
                  borderColor: autonomy === level ? 'rgba(56,232,255,0.4)' : undefined,
                  background: autonomy === level ? 'rgba(56,232,255,0.06)' : undefined,
                }}
              >
                <div className="row">
                  <input
                    type="radio"
                    name="autonomy"
                    checked={autonomy === level}
                    onChange={() => void onApplySettings({ autonomyLevel: level })}
                    style={{ width: 'auto' }}
                  />
                  <strong style={{ fontSize: 13 }}>{AUTONOMY[level].title}</strong>
                </div>
                <p className="small muted">{AUTONOMY[level].body}</p>
              </label>
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid grid--2">
        <Panel title="Voice & privacy" subtitle="Stored per owner">
          <div className="stack">
            <Field label="Interaction mode">
              <select
                value={voice.mode}
                onChange={(e) => void onApplySettings({ voice: { mode: e.target.value as VoiceSettings['mode'] } })}
              >
                <option value="both">Voice + text</option>
                <option value="voice">Voice first</option>
                <option value="text">Text only</option>
              </select>
            </Field>
            <Toggle
              label="Wake phrase"
              hint="Keyword spotting in the browser. It does not identify you, and anyone nearby can say it."
              checked={voice.wakeEnabled}
              onChange={(next) => void onApplySettings({ voice: { wakeEnabled: next } })}
            />
            <Field label="Wake phrase words">
              <input
                value={voice.wakePhrase}
                onChange={(e) => void onApplySettings({ voice: { wakePhrase: e.target.value } })}
                maxLength={40}
              />
            </Field>
            <Toggle
              label="Privacy mode"
              hint="Keeps the microphone off until you explicitly press the orb."
              checked={voice.privacyMode}
              onChange={(next) => void onApplySettings({ voice: { privacyMode: next } })}
            />
            <Toggle
              label="Read notifications aloud"
              hint="Planned: there is no notification pipeline yet, so this stores your preference only."
              checked={voice.readNotifications}
              onChange={(next) => void onApplySettings({ voice: { readNotifications: next } })}
            />
            <Toggle
              label="Reduced motion"
              hint="Disables the pulse and glow animations for accessibility."
              checked={voice.reducedMotion}
              onChange={(next) => void onApplySettings({ voice: { reducedMotion: next } })}
            />
          </div>
          <Notice tone="amber" title="Speaker verification is not implemented">
            Any voice can trigger a transcript. Nothing sensitive is authorised by voice alone; the
            Approval centre is the gate. Enrolment, thresholds and template storage are a future
            milestone and will never silently enrol a new speaker.
          </Notice>
        </Panel>

        <Panel title="Usage & cost" subtitle="Estimated from provider-reported tokens">
          {usage.data ? (
            <>
              <div className="grid grid--2" style={{ gap: 10 }}>
                <Stat label="Requests" value={usage.data.requests} hint={`${usage.data.failures} failed`} />
                <Stat label="Avg latency" value={`${usage.data.avgLatencyMs} ms`} />
                <Stat label="Prompt tokens" value={usage.data.promptTokens.toLocaleString()} />
                <Stat label="Completion tokens" value={usage.data.completionTokens.toLocaleString()} />
                <Stat label="Estimated cost" value={`$${usage.data.costUsdEstimate.toFixed(4)}`} />
                <Stat label="Priced model" value={usage.data.pricing.model} hint={`$${usage.data.pricing.inputPerMTok}/M in · $${usage.data.pricing.outputPerMTok}/M out`} />
              </div>
              <p className="tiny muted-2" style={{ marginTop: 10 }}>{usage.data.note}</p>
              {usage.data.last7Days.length > 0 && (
                <div className="table-wrap" style={{ marginTop: 10 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Day</th>
                        <th className="num">Requests</th>
                        <th className="num">Est. cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {usage.data.last7Days.map((day) => (
                        <tr key={day.day}>
                          <td className="tiny">{day.day}</td>
                          <td className="num">{day.requests}</td>
                          <td className="num">${day.costUsdEstimate.toFixed(4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <Spinner label="Loading usage…" />
          )}
        </Panel>
      </div>

      <div className="grid grid--2">
        <Panel title="Audit trail" subtitle="Every action, decision and failure">
          {audit.data ? (
            audit.data.entries.length === 0 ? (
              <p className="muted small">No audit entries yet.</p>
            ) : (
              <div className="table-wrap scroll-y">
                <table>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Actor</th>
                      <th>Action</th>
                      <th>Target</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.data.entries.map((entry) => (
                      <tr key={entry.id}>
                        <td className="tiny muted-2">{relativeTime(entry.created_at)}</td>
                        <td className="tiny">{entry.actor}</td>
                        <td className="tiny">{entry.action}</td>
                        <td className="tiny muted-2 wrap-anywhere">{entry.target ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <Spinner label="Loading audit log…" />
          )}
        </Panel>

        <Panel title="Change password" subtitle="Signs out every other session">
          {passwordMessage && (
            <Notice tone={passwordMessage.tone} onDismiss={() => setPasswordMessage(null)}>
              {passwordMessage.text}
            </Notice>
          )}
          <form className="stack" onSubmit={changePassword}>
            <Field label="Current password">
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </Field>
            <Field label="New password" hint="At least 10 characters with a letter and a number.">
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                minLength={10}
                required
              />
            </Field>
            <div className="form-actions">
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? 'Updating…' : 'Update password'}
              </Button>
            </div>
          </form>

          <hr className="hr" />
          <p className="tiny muted-2">
            Last audit entry: {audit.data?.entries[0] ? `${audit.data.entries[0].action} at ${formatDate(audit.data.entries[0].created_at)}` : 'none'}.
            Data export and full account deletion are not built yet — memory records can be deleted
            individually from the Memory view.
          </p>
        </Panel>
      </div>
    </>
  );
}
