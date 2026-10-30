import { useState } from 'react';
import { Button, EmptyState, Notice, Panel, Pill, Spinner, StatusBadge, formatDate } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { useAsync } from '../lib/hooks';
import type { Approval } from '../lib/types';

interface Catalogue {
  available: Array<{ type: string; label: string; description: string; risk: string; external: boolean }>;
  unavailable: Array<{ type: string; label: string; reason: string }>;
}

export default function ApprovalsView({ onChanged }: { onChanged: () => void }) {
  const approvals = useAsync<{ approvals: Approval[]; pending: number }>(() => api.get('/approvals'), []);
  const catalogue = useAsync<Catalogue>(() => api.get<Catalogue>('/approvals/catalogue'), []);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const decide = async (approval: Approval, decision: 'approve' | 'reject') => {
    setBusyId(approval.id);
    setError(null);
    setMessage(null);
    try {
      const result = await api.post<{ status: string; result: unknown }>(`/approvals/${approval.id}/decide`, { decision });
      setMessage(
        decision === 'reject'
          ? 'Rejected. Nothing was executed.'
          : `Approved. Execution status: ${result.status.replace(/_/g, ' ')}.`,
      );
      approvals.reload();
      onChanged();
    } catch (err) {
      const payload = err instanceof ApiError ? (err.payload as { error?: string; result?: unknown }) : null;
      setError(
        payload?.error === 'already_decided'
          ? 'This request was already decided elsewhere. Reloading.'
          : err instanceof Error
            ? err.message
            : 'Could not record the decision',
      );
      approvals.reload();
    } finally {
      setBusyId(null);
    }
  };

  const pending = approvals.data?.approvals.filter((a) => a.status === 'pending') ?? [];
  const history = approvals.data?.approvals.filter((a) => a.status !== 'pending') ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Approval centre</h2>
          <p>
            External actions always require your approval. Low-risk internal records can execute
            immediately at autonomy level 2+, and every one is still listed below with its audit trail.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone={pending.length > 0 ? 'amber' : 'green'}>{pending.length} pending</Pill>
        <Button variant="subtle" size="sm" onClick={approvals.reload}>
          Refresh
        </Button>
      </div>

      {error && <Notice tone="red" title="Decision not applied" onDismiss={() => setError(null)}>{error}</Notice>}
      {message && <Notice tone="green" onDismiss={() => setMessage(null)}>{message}</Notice>}

      <Panel title="Waiting for you" subtitle="Review the exact payload before approving">
        {approvals.loading && !approvals.data ? (
          <Spinner label="Loading approvals…" />
        ) : pending.length === 0 ? (
          <EmptyState
            title="Nothing waiting"
            body="When J.A.R.V.I.S. proposes an external or protected action, it appears here with its full payload."
          />
        ) : (
          <div className="stack stack--lg">
            {pending.map((approval) => (
              <div key={approval.id} className="agent-card">
                <div className="row row--between">
                  <div className="row">
                    <Pill tone={approval.risk === 'high' ? 'red' : approval.risk === 'medium' ? 'amber' : 'cyan'}>
                      {approval.risk} risk
                    </Pill>
                    <strong style={{ fontSize: 14 }}>{approval.action_label}</strong>
                  </div>
                  <span className="tiny muted-2">{formatDate(approval.created_at)}</span>
                </div>
                <p className="small muted">{approval.summary}</p>
                <pre className="code">{JSON.stringify(approval.payload, null, 2)}</pre>
                {!approval.available && (
                  <Notice tone="red">
                    This action type is no longer available in this build, so it cannot be executed.
                  </Notice>
                )}
                <div className="row" style={{ justifyContent: 'flex-end' }}>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={busyId === approval.id}
                    onClick={() => void decide(approval, 'reject')}
                  >
                    Reject
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={busyId === approval.id || !approval.available}
                    onClick={() => void decide(approval, 'approve')}
                  >
                    {busyId === approval.id ? 'Working…' : 'Approve and run'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <div className="grid grid--2">
        <Panel title="Action catalogue" subtitle="Everything J.A.R.V.I.S. is able to do">
          <div className="list">
            {(catalogue.data?.available ?? []).map((action) => (
              <div key={action.type} className="list__row">
                <div className="list__main">
                  <div className="list__title">{action.label}</div>
                  <div className="list__sub">{action.description}</div>
                </div>
                <Pill tone={action.external ? 'amber' : 'cyan'}>{action.external ? 'external' : 'internal'}</Pill>
              </div>
            ))}
            {!catalogue.data && <Spinner label="Loading catalogue…" />}
          </div>
        </Panel>

        <Panel title="Deliberately unavailable" subtitle="Marked incomplete instead of faked">
          <div className="list">
            {(catalogue.data?.unavailable ?? []).map((action) => (
              <div key={action.type} className="list__row">
                <div className="list__main">
                  <div className="list__title">{action.label}</div>
                  <div className="list__sub">{action.reason}</div>
                </div>
                <Pill tone="muted">not built</Pill>
              </div>
            ))}
          </div>
          <p className="tiny muted-2" style={{ marginTop: 12 }}>
            Money movement is not implemented at all, by design. Adding it would require a separate
            review, explicit limits and a second authentication factor.
          </p>
        </Panel>
      </div>

      <Panel title="History" subtitle="Every decision and automatic execution">
        {history.length === 0 ? (
          <p className="muted small">No decisions yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Action</th>
                  <th>Summary</th>
                  <th>Status</th>
                  <th>Result / error</th>
                </tr>
              </thead>
              <tbody>
                {history.map((approval) => (
                  <tr key={approval.id}>
                    <td className="tiny muted-2">{formatDate(approval.executed_at ?? approval.decided_at ?? approval.created_at)}</td>
                    <td>{approval.action_label}</td>
                    <td className="muted">{approval.summary}</td>
                    <td>
                      <StatusBadge status={approval.status} />
                    </td>
                    <td className="tiny muted-2 wrap-anywhere">
                      {approval.error ? `⚠ ${approval.error}` : approval.result ? JSON.stringify(approval.result) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
