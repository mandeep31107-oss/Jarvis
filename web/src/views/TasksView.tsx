import { useState } from 'react';
import { Button, EmptyState, Field, Notice, Panel, Pill, Spinner, Stat, formatDate } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { useAsync } from '../lib/hooks';
import type { Task, TaskStats } from '../lib/types';

interface TasksResponse {
  tasks: Task[];
  stats: TaskStats;
}

const STATUSES: Task['status'][] = ['pending', 'in_progress', 'blocked', 'done', 'cancelled'];

export default function TasksView() {
  const tasks = useAsync<TasksResponse>(() => api.get<TasksResponse>('/tasks'), []);
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState<Task['priority']>('medium');
  const [due, setDue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (title.trim().length < 3) {
      setError('A task needs a title of at least 3 characters.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post('/tasks', {
        title: title.trim(),
        priority,
        dueAt: due ? new Date(`${due}T18:00:00`).toISOString() : null,
      });
      setTitle('');
      setDue('');
      setPriority('medium');
      tasks.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the task');
    } finally {
      setBusy(false);
    }
  };

  const update = async (task: Task, patch: Partial<Task>) => {
    setError(null);
    try {
      await api.patch(`/tasks/${task.id}`, patch);
      tasks.reload();
    } catch (err) {
      const payload = err instanceof ApiError ? (err.payload as { message?: string }) : null;
      setError(payload?.message ?? (err instanceof Error ? err.message : 'Could not update the task'));
    }
  };

  const remove = async (task: Task) => {
    if (!window.confirm(`Delete “${task.title}”? This removes it permanently.`)) return;
    try {
      await api.del(`/tasks/${task.id}`);
      tasks.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the task');
    }
  };

  const data = tasks.data;
  const open = data?.tasks.filter((t) => t.status !== 'done' && t.status !== 'cancelled') ?? [];
  const closed = data?.tasks.filter((t) => t.status === 'done' || t.status === 'cancelled') ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Tasks</h2>
          <p>
            Completion is recorded when you mark it done or when evidence exists — never because a plan
            was generated. Dependencies block completion until they are finished.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Button variant="subtle" size="sm" onClick={tasks.reload}>
          Refresh
        </Button>
      </div>

      {error && <Notice tone="red" title="Something went wrong" onDismiss={() => setError(null)}>{error}</Notice>}

      {data && (
        <div className="grid grid--4">
          <Stat label="Open" value={data.stats.open} />
          <Stat label="Due today" value={data.stats.dueToday} />
          <Stat label="Overdue" value={data.stats.overdue} />
          <Stat label="Blocked" value={data.stats.blocked} />
        </div>
      )}

      <div className="grid grid--sidebar">
        <Panel title="Task list" subtitle="Open work first, ordered by priority">
          {tasks.loading && !data ? (
            <Spinner label="Loading tasks…" />
          ) : open.length === 0 ? (
            <EmptyState title="Nothing open" body="Add a task on the right, or ask J.A.R.V.I.S. to create one from a conversation." />
          ) : (
            <div className="list">
              {open.map((task) => (
                <div key={task.id} className="list__row">
                  <input
                    type="checkbox"
                    aria-label={`Mark ${task.title} done`}
                    checked={false}
                    onChange={() => void update(task, { status: 'done' })}
                  />
                  <div className="list__main">
                    <div className="list__title">{task.title}</div>
                    <div className="list__sub">
                      {task.due_at ? `Due ${formatDate(task.due_at)}` : 'No due date'} · {task.source === 'jarvis' ? 'created by J.A.R.V.I.S.' : 'added by you'}
                      {task.depends_on.length > 0 ? ` · ${task.depends_on.length} dependency` : ''}
                    </div>
                  </div>
                  <select
                    value={task.status}
                    onChange={(e) => void update(task, { status: e.target.value as Task['status'] })}
                    aria-label={`Status of ${task.title}`}
                    style={{ width: 132 }}
                  >
                    {STATUSES.map((status) => (
                      <option key={status} value={status}>
                        {status.replace(/_/g, ' ')}
                      </option>
                    ))}
                  </select>
                  <Pill tone={task.priority === 'critical' ? 'red' : task.priority === 'high' ? 'amber' : 'muted'}>
                    {task.priority}
                  </Pill>
                  <button className="btn btn--subtle btn--sm" onClick={() => void remove(task)} aria-label={`Delete ${task.title}`}>
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          {closed.length > 0 && (
            <>
              <hr className="hr" />
              <p className="tiny muted-2" style={{ marginBottom: 6 }}>COMPLETED ({closed.length})</p>
              <div className="list scroll-y">
                {closed.map((task) => (
                  <div key={task.id} className="list__row list__row--done">
                    <div className="list__main">
                      <div className="list__title">{task.title}</div>
                      <div className="list__sub">{task.completed_at ? `Completed ${formatDate(task.completed_at)}` : task.status}</div>
                    </div>
                    <button
                      className="btn btn--subtle btn--sm"
                      onClick={() => void update(task, { status: 'in_progress' })}
                      title="Reopen"
                    >
                      Reopen
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </Panel>

        <Panel title="New task" subtitle="Stored with an audit entry">
          <form className="stack" onSubmit={create}>
            <Field label="Title" htmlFor="task-title">
              <input
                id="task-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Validate the customer problem with 5 interviews"
                maxLength={300}
              />
            </Field>
            <div className="form-row form-row--2">
              <Field label="Priority">
                <select value={priority} onChange={(e) => setPriority(e.target.value as Task['priority'])}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </Field>
              <Field label="Due date">
                <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
              </Field>
            </div>
            <div className="form-actions">
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? 'Saving…' : 'Add task'}
              </Button>
            </div>
          </form>
          <p className="tiny muted-2" style={{ marginTop: 10 }}>
            Tip: in the conversation you can say “Jarvis, create a task to …”. Depending on your
            autonomy level it is created immediately or queued for approval.
          </p>
        </Panel>
      </div>
    </>
  );
}
