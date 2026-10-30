import { EmptyState, Notice, Panel, Pill, Spinner, Stat, StatusBadge, formatDate, formatDay } from '../components/ui';
import type { ViewId } from '../components/Shell';
import { api } from '../lib/api';
import { relativeTime, useAsync } from '../lib/hooks';
import type { Briefing } from '../lib/types';

function greeting(name: string): string {
  const hour = new Date().getHours();
  const part = hour < 5 ? 'Still awake' : hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  return `${part}, ${name.split(' ')[0]}`;
}

export default function BriefingView({ onNavigate }: { onNavigate: (view: ViewId) => void }) {
  const briefing = useAsync<Briefing>(() => api.get<Briefing>('/system/briefing'), []);

  if (briefing.loading && !briefing.data) return <Spinner label="Assembling your briefing…" />;
  if (briefing.error) {
    return (
      <Notice tone="red" title="Could not load the briefing">
        {briefing.error}
      </Notice>
    );
  }
  const data = briefing.data;
  if (!data) return null;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>{greeting(data.owner.name)}</h2>
          <p>
            Everything below is read from your own database — tasks, approvals, finance records and
            run history. Nothing here is a placeholder.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone="muted">updated {relativeTime(data.generatedAt)}</Pill>
        <button className="btn btn--ghost btn--sm" onClick={briefing.reload}>
          Refresh
        </button>
      </div>

      {data.warnings.length > 0 && (
        <Notice tone="amber" title="Before you rely on anything">
          <ul className="stack" style={{ gap: 4 }}>
            {data.warnings.map((warning) => (
              <li key={warning}>• {warning}</li>
            ))}
          </ul>
        </Notice>
      )}

      <div className="grid grid--4">
        <Stat label="Open tasks" value={data.stats.open} hint={`${data.stats.done} completed all-time`} />
        <Stat label="Due today" value={data.stats.dueToday} hint="From your task list" />
        <Stat label="Overdue" value={data.stats.overdue} hint="Due date has passed" />
        <Stat label="Awaiting approval" value={data.pendingApprovals} hint="External or protected actions" />
      </div>

      <div className="grid grid--sidebar">
        <Panel
          title="Today's priorities"
          subtitle="Ordered by priority and due date"
          actions={
            <button className="btn btn--ghost btn--sm" onClick={() => onNavigate('tasks')}>
              Open tasks
            </button>
          }
        >
          {data.priorities.length === 0 ? (
            <EmptyState
              title="No open tasks"
              body="Create a task, or say: “Jarvis, create a task to validate my startup idea this week.”"
              action={
                <button className="btn btn--ghost btn--sm" onClick={() => onNavigate('chat')}>
                  Go to conversation
                </button>
              }
            />
          ) : (
            <div className="list">
              {data.priorities.map((task) => (
                <div key={task.id} className="list__row">
                  <div className="list__main">
                    <div className="list__title">{task.title}</div>
                    <div className="list__sub">
                      {task.dueAt ? `Due ${formatDate(task.dueAt)}` : 'No due date'} · {task.status.replace(/_/g, ' ')}
                    </div>
                  </div>
                  <Pill tone={task.priority === 'critical' ? 'red' : task.priority === 'high' ? 'amber' : 'muted'}>
                    {task.priority}
                  </Pill>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <div className="stack stack--lg">
          <Panel title="Finance snapshot" subtitle="From recorded entries only">
            <div className="grid grid--2" style={{ gap: 10 }}>
              <Stat label="Income" value={data.finance.income} />
              <Stat label="Expenses" value={data.finance.expense} />
              <Stat label="Net recorded" value={data.finance.net} />
              <Stat label="Recurring / month" value={data.finance.recurringMonthly} />
            </div>
            <p className="small muted-2" style={{ marginTop: 10 }}>
              {data.finance.entryCount} entries. Revenue is not profit; these are records, not an
              accounting statement.
            </p>
            <button className="btn btn--subtle btn--sm" style={{ marginTop: 10 }} onClick={() => onNavigate('finance')}>
              Open finance
            </button>
          </Panel>

          <Panel title="System status">
            <dl className="kv">
              <dt>Model</dt>
              <dd>{data.provider}</dd>
              <dt>Autonomy</dt>
              <dd>Level {data.autonomyLevel}</dd>
              <dt>Memories</dt>
              <dd>{data.memoryCount} stored</dd>
              <dt>Approvals</dt>
              <dd>{data.pendingApprovals} pending</dd>
            </dl>
            <button
              className="btn btn--subtle btn--sm"
              style={{ marginTop: 12 }}
              onClick={() => onNavigate('security')}
            >
              Permissions & security
            </button>
          </Panel>
        </div>
      </div>

      <div className="grid grid--2">
        <Panel title="Deadlines" subtitle="Due today and overdue">
          {data.dueToday.length === 0 && data.overdue.length === 0 ? (
            <p className="muted small">Nothing is due today or overdue. Deadlines appear here as soon as they exist.</p>
          ) : (
            <div className="list">
              {data.overdue.map((task) => (
                <div key={task.id} className="list__row">
                  <div className="list__main">
                    <div className="list__title">{task.title}</div>
                    <div className="list__sub">Overdue since {formatDay(task.dueAt)}</div>
                  </div>
                  <Pill tone="red">overdue</Pill>
                </div>
              ))}
              {data.dueToday.map((task) => (
                <div key={task.id} className="list__row">
                  <div className="list__main">
                    <div className="list__title">{task.title}</div>
                    <div className="list__sub">Due {formatDate(task.dueAt)}</div>
                  </div>
                  <Pill tone="amber">today</Pill>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          title="Recent workflow runs"
          subtitle="Real execution history"
          actions={
            <button className="btn btn--subtle btn--sm" onClick={() => onNavigate('workflows')}>
              Monitor
            </button>
          }
        >
          {data.recentRuns.length === 0 ? (
            <p className="muted small">No runs yet. Every conversation turn creates one.</p>
          ) : (
            <div className="list">
              {data.recentRuns.map((run) => (
                <div key={run.id} className="list__row">
                  <div className="list__main">
                    <div className="list__title">{run.prompt_preview}</div>
                    <div className="list__sub">
                      {run.agent_id} · {relativeTime(run.started_at)}
                    </div>
                  </div>
                  <StatusBadge status={run.status} />
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
