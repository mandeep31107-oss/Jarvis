import { useState } from 'react';
import { Button, EmptyState, Field, Notice, Panel, Pill, Spinner, formatDate } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { useAsync } from '../lib/hooks';
import type { ResearchNote } from '../lib/types';

export default function ResearchView() {
  const notes = useAsync<{ notes: ResearchNote[] }>(() => api.get('/research/notes'), []);
  const [question, setQuestion] = useState('');
  const [jurisdiction, setJurisdiction] = useState('IN');
  const [summary, setSummary] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post('/research/notes', {
        question: question.trim(),
        jurisdiction: jurisdiction.trim() || null,
        summary: summary.trim(),
        sources: sourceUrl.trim()
          ? [{ url: sourceUrl.trim(), title: new URL(sourceUrl.trim()).hostname, fetchedAt: new Date().toISOString() }]
          : [],
      });
      setQuestion('');
      setSummary('');
      setSourceUrl('');
      notes.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Source must be a valid absolute URL');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Research library</h2>
          <p>
            Sources are stored with their URL, publisher and retrieval date. Nothing here is
            model-generated unless clearly labelled, and no search engine is connected.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone="amber">search API not connected</Pill>
      </div>

      <Notice tone="amber" title="What research can and cannot do yet">
        J.A.R.V.I.S. cannot discover URLs on its own. If you paste links into the conversation it can
        request permission to fetch them (outbound fetch must be enabled in <code>.env</code>). You can
        also record a source manually below. Findings are never invented to fill a gap.
      </Notice>

      {error && <Notice tone="red" title="Could not save the note" onDismiss={() => setError(null)}>{error}</Notice>}

      <div className="grid grid--sidebar">
        <Panel title="Notes" subtitle="Newest first">
          {notes.loading && !notes.data ? (
            <Spinner label="Loading notes…" />
          ) : (notes.data?.notes.length ?? 0) === 0 ? (
            <EmptyState
              title="No research stored"
              body="Add a note manually, or in the conversation paste a URL and ask: “Jarvis, research this page.”"
            />
          ) : (
            <div className="stack stack--lg">
              {notes.data?.notes.map((note) => (
                <div key={note.id} className="agent-card">
                  <div className="row row--between">
                    <strong style={{ fontSize: 14 }}>{note.question}</strong>
                    <div className="row">
                      {note.jurisdiction && <Pill tone="cyan">{note.jurisdiction}</Pill>}
                      <Pill tone={note.status === 'failed' ? 'red' : note.status === 'sources_retrieved' ? 'green' : 'amber'}>
                        {note.status.replace(/_/g, ' ')}
                      </Pill>
                      <Pill tone="muted">{note.confidence} confidence</Pill>
                    </div>
                  </div>
                  <p className="small muted" style={{ whiteSpace: 'pre-wrap' }}>{note.summary}</p>
                  {note.sources.length > 0 && (
                    <div className="stack" style={{ gap: 6 }}>
                      {note.sources.map((source) => (
                        <div key={source.url} className="row small" style={{ alignItems: 'baseline' }}>
                          <Pill tone="cyan">source</Pill>
                          <a href={source.url} target="_blank" rel="noreferrer noopener">
                            {source.title || source.url}
                          </a>
                          <span className="tiny muted-2">
                            {source.fetchedAt ? `retrieved ${formatDate(source.fetchedAt)}` : ''}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  <span className="tiny muted-2">recorded {formatDate(note.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Record a source" subtitle="You supply the evidence">
          <form className="stack" onSubmit={create}>
            <Field label="Question">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="What does the DPDP Act require for consent notices?"
                maxLength={500}
              />
            </Field>
            <div className="form-row form-row--2">
              <Field label="Jurisdiction">
                <input value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)} placeholder="IN" maxLength={8} />
              </Field>
              <Field label="Source URL">
                <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://…" />
              </Field>
            </div>
            <Field label="What it says" hint="Summarise only what the source actually states. Label your own interpretation separately.">
              <textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={5} maxLength={20000} />
            </Field>
            <div className="form-actions">
              <Button
                type="submit"
                variant="primary"
                disabled={busy || question.trim().length < 3 || summary.trim().length < 3}
              >
                {busy ? 'Saving…' : 'Save note'}
              </Button>
            </div>
          </form>
        </Panel>
      </div>
    </>
  );
}
