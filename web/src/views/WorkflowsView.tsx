import { useEffect, useRef, useState } from 'react';
import { Button, EmptyState, Notice, Panel, Pill, Spinner, StatusBadge, formatDate } from '../components/ui';
import { api, ApiError, subscribeRunEvents } from '../lib/api';
import { relativeTime, useAsync, useInterval } from '../lib/hooks';
import type { Run, RunEvent } from '../lib/types';

export default function WorkflowsView() {
  const runs = useAsync<{ runs: Run[]; pendingApprovals: number }>(() => api.get('/runs'), []);
  const [live, setLive] = useState<RunEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ run: Run; events: RunEvent[] } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const seen = useRef(new Set<number>());

  useEffect(() => {
    const unsubscribe = subscribeRunEvents((event) => {
      setConnected(true);
      if (event.type === 'replay') {
        const payload = event.data as { events: RunEvent[] };
        for (const e of payload.events) seen.current.add(e.id);
        setLive(payload.events.slice(-40));
        return;
      }
      if (event.type !== 'event') return;
      const record = event.data as RunEvent;
      if (seen.current.has(record.id)) return;
      seen.current.add(record.id);
      setLive((prev) => [...prev.slice(-60), record]);
    });
    return unsubscribe;
  }, []);

  useInterval(() => runs.reload(), 6_000);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api
      .get<{ run: Run; events: RunEvent[] }>(`/runs/${selectedId}`)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, runs.data]);

  const cancel = async (run: Run) => {
    try {
      const result = await api.post<{ cancelled: boolean; reason: string }>(`/runs/${run.id}/cancel`);
      setNotice(
        result.cancelled
          ? 'Cancellation signal sent to the provider request.'
          : `Not cancelled: ${result.reason.replace(/_/g, ' ')}.`,
      );
      runs.reload();
    } catch (err) {
      const payload = err instanceof ApiError ? (err.payload as { reason?: string }) : null;
      setNotice(payload?.reason ? `Not cancelled: ${payload.reason.replace(/_/g, ' ')}` : 'Could not cancel.');
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Workflow monitor</h2>
          <p>
            Every turn is a run with real stage events written as they happen. Progress is completed
            stages ÷ total stages — never a simulated animation.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone={connected ? 'green' : 'muted'}>{connected ? 'live stream connected' : 'polling only'}</Pill>
        <Button variant="subtle" size="sm" onClick={runs.reload}>
          Refresh
        </Button>
      </div>

      {notice && <Notice tone="cyan" onDismiss={() => setNotice(null)}>{notice}</Notice>}

      <div className="grid grid--sidebar">
        <Panel title="Runs" subtitle="Newest first">
          {runs.loading && !runs.data ? (
            <Spinner label="Loading runs…" />
          ) : (runs.data?.runs.length ?? 0) === 0 ? (
            <EmptyState title="No runs yet" body="Send a message in the conversation view; its execution appears here instantly." />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Request</th>
                    <th>Agent</th>
                    <th>Status</th>
                    <th>Stage progress</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {runs.data?.runs.map((run) => (
                    <tr key={run.id} style={{ cursor: 'pointer' }} onClick={() => setSelectedId(run.id)}>
                      <td className="tiny muted-2">{formatDate(run.started_at)}</td>
                      <td className="wrap-anywhere" style={{ maxWidth: 280 }}>{run.prompt_preview}</td>
                      <td className="tiny muted">{run.agent_id}</td>
                      <td>
                        <StatusBadge status={run.status} />
                      </td>
                      <td style={{ minWidth: 130 }}>
                        <div className="progress" aria-label={`${run.progress}% of stages complete`}>
                          <div className="progress__fill" style={{ width: `${run.progress}%` }} />
                        </div>
                        <span className="tiny muted-2">
                          {run.stage_done}/{run.stage_total} stages
                        </span>
                      </td>
                      <td>
                        {['running', 'waiting', 'planned'].includes(run.status) && (
                          <Button
                            variant="subtle"
                            size="sm"
                            onClick={(event) => {
                              event.stopPropagation();
                              void cancel(run);
                            }}
                          >
                            Cancel
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Selected run" subtitle={selectedId ? 'Stage timeline from the database' : 'Click a row'}>
          {!detail ? (
            <p className="muted small">Select a run to see exactly what executed and when.</p>
          ) : (
            <div className="stack">
              <dl className="kv">
                <dt>Intent</dt>
                <dd>{detail.run.intent}</dd>
                <dt>Language</dt>
                <dd>{detail.run.language}</dd>
                <dt>Agent</dt>
                <dd>{detail.run.agent_id}</dd>
                <dt>Status</dt>
                <dd>
                  <StatusBadge status={detail.run.status} />
                </dd>
                {detail.run.error && (
                  <>
                    <dt>Error</dt>
                    <dd style={{ color: 'var(--red)' }}>{detail.run.error}</dd>
                  </>
                )}
              </dl>
              <div className="timeline">
                {detail.events.map((event) => (
                  <div key={event.id} className="timeline__item">
                    <span className={`timeline__dot timeline__dot--${event.status}`} />
                    <div>
                      <div className="timeline__stage">
                        {event.stage} — {event.status}
                      </div>
                      {event.detail && <div className="timeline__detail">{event.detail}</div>}
                      <div className="timeline__time">{relativeTime(event.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Live event stream" subtitle="Server-Sent Events from the orchestrator">
        {live.length === 0 ? (
          <p className="muted small">
            No events yet. The stream shows stage transitions, approvals and failures as they occur.
          </p>
        ) : (
          <div className="timeline scroll-y">
            {live.map((event) => (
              <div key={event.id} className="timeline__item">
                <span className={`timeline__dot timeline__dot--${event.status}`} />
                <div>
                  <div className="timeline__stage">
                    <span className="mono tiny muted-2">{event.run_id.slice(0, 8)}</span> {event.stage} — {event.status}
                  </div>
                  {event.detail && <div className="timeline__detail">{event.detail}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  );
}
