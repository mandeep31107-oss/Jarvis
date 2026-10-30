import { useState } from 'react';
import { Button, EmptyState, Field, Notice, Panel, Pill, Spinner, formatDate } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { useAsync } from '../lib/hooks';
import type { MemoryEntry } from '../lib/types';

const CATEGORY_LABELS: Record<string, string> = {
  preference: 'Preference',
  project: 'Project',
  decision: 'Decision',
  goal: 'Goal',
  constraint: 'Constraint',
  deadline: 'Deadline',
  research: 'Research',
};

export default function MemoryView() {
  const memory = useAsync<{ entries: MemoryEntry[]; categories: string[] }>(() => api.get('/memory'), []);
  const [category, setCategory] = useState('preference');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [filter, setFilter] = useState<string>('all');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post('/memory', { category, title: title.trim(), content: content.trim(), confidence: 'high' });
      setTitle('');
      setContent('');
      memory.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this memory');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (entry: MemoryEntry) => {
    if (!window.confirm(`Forget “${entry.title}”? The record is deleted permanently.`)) return;
    try {
      await api.del(`/memory/${entry.id}`);
      memory.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this memory');
    }
  };

  const entries = (memory.data?.entries ?? []).filter((e) => filter === 'all' || e.category === filter);

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Memory</h2>
          <p>
            Durable context, separated by category and labelled by who said it. Anything the model
            inferred is marked <em>inferred</em>; anything you typed is <em>owner-stated</em>.
          </p>
        </div>
        <div className="page-head__spacer" />
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by category" style={{ width: 170 }}>
          <option value="all">All categories</option>
          {(memory.data?.categories ?? []).map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABELS[c] ?? c}
            </option>
          ))}
        </select>
      </div>

      {memory.error && <Notice tone="red" title="Could not load memory">{memory.error}</Notice>}
      {error && <Notice tone="red" title="Action failed" onDismiss={() => setError(null)}>{error}</Notice>}

      <div className="grid grid--sidebar">
        <Panel title="Stored memories" subtitle={`${entries.length} shown`}>
          {memory.loading && !memory.data ? (
            <Spinner label="Loading memory…" />
          ) : entries.length === 0 ? (
            <EmptyState
              title="Nothing remembered yet"
              body="Add a durable fact on the right, or say “Jarvis, remember that my budget is ₹50,000 per month”."
            />
          ) : (
            <div className="list">
              {entries.map((entry) => (
                <div key={entry.id} className="list__row" style={{ alignItems: 'flex-start' }}>
                  <div className="list__main">
                    <div className="list__title">{entry.title}</div>
                    <div className="list__sub" style={{ whiteSpace: 'pre-wrap', margin: '4px 0 6px' }}>
                      {entry.content}
                    </div>
                    <div className="row tiny">
                      <Pill tone="cyan">{CATEGORY_LABELS[entry.category] ?? entry.category}</Pill>
                      <Pill tone={entry.source_type === 'user' ? 'green' : 'violet'}>
                        {entry.source_type === 'user' ? 'owner-stated' : entry.source_type === 'ai' ? 'inferred' : 'tool'}
                      </Pill>
                      <Pill tone={entry.confidence === 'high' ? 'green' : entry.confidence === 'low' ? 'red' : 'amber'}>
                        {entry.confidence} confidence
                      </Pill>
                      <span className="muted-2">updated {formatDate(entry.updated_at)}</span>
                    </div>
                  </div>
                  <button className="btn btn--subtle btn--sm" onClick={() => void remove(entry)} aria-label={`Forget ${entry.title}`}>
                    Forget
                  </button>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Add memory" subtitle="Only useful, durable facts">
          <form className="stack" onSubmit={create}>
            <Field label="Category">
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {(memory.data?.categories ?? ['preference', 'project', 'decision', 'goal', 'constraint', 'deadline', 'research']).map(
                  (c) => (
                    <option key={c} value={c}>
                      {CATEGORY_LABELS[c] ?? c}
                    </option>
                  ),
                )}
              </select>
            </Field>
            <Field label="Title">
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Default currency" maxLength={200} />
            </Field>
            <Field label="Content" hint="Facts you want J.A.R.V.I.S. to keep using in later conversations.">
              <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="All budgeting happens in INR." maxLength={8000} />
            </Field>
            <div className="form-actions">
              <Button type="submit" variant="primary" disabled={busy || title.trim().length < 3 || content.trim().length < 3}>
                {busy ? 'Saving…' : 'Save memory'}
              </Button>
            </div>
          </form>
          <div className="notice notice--amber" style={{ marginTop: 12 }}>
            <div>
              <strong>Never store secrets here</strong>
              <div className="notice__body">
                API keys, passwords and tokens belong in <code>.env</code> on the server — not in
                conversational memory. Anything typed here is sent to the model as context.
              </div>
            </div>
          </div>
        </Panel>
      </div>
    </>
  );
}
