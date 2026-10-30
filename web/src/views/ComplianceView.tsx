import { useState } from 'react';
import { Button, EmptyState, Field, Notice, Panel, Pill, Spinner, StatusBadge, formatDay } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { useAsync } from '../lib/hooks';
import type { ComplianceItem } from '../lib/types';

const AREA_LABELS: Record<string, string> = {
  company_formation: 'Company formation',
  taxation: 'Taxation',
  privacy_data: 'Privacy & data',
  intellectual_property: 'Intellectual property',
  employment: 'Employment & contractors',
  consumer_protection: 'Consumer protection',
  cybersecurity: 'Cybersecurity',
  ai_regulation: 'AI regulation',
  marketing_advertising: 'Marketing & advertising',
  cross_border: 'Cross-border services',
  financial_services: 'Financial services',
  software_licensing: 'Software licensing',
  platform_terms: 'Platform terms',
  api_usage: 'API usage',
  data_retention: 'Data retention',
  communications_consent: 'Communications consent',
};

const STATUSES = ['to_review', 'in_progress', 'done', 'not_applicable', 'needs_legal_advice'];

export default function ComplianceView() {
  const items = useAsync<{ items: ComplianceItem[]; areas: string[] }>(() => api.get('/compliance'), []);
  const [jurisdiction, setJurisdiction] = useState('IN');
  const [area, setArea] = useState('privacy_data');
  const [title, setTitle] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [publisher, setPublisher] = useState('');
  const [publishedOn, setPublishedOn] = useState('');
  const [effectiveOn, setEffectiveOn] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post('/compliance', {
        jurisdiction: jurisdiction.trim().toUpperCase(),
        area,
        title: title.trim(),
        sourceUrl: sourceUrl.trim() || null,
        publisher: publisher.trim() || null,
        publishedOn: publishedOn || null,
        effectiveOn: effectiveOn || null,
        notes: notes.trim(),
      });
      setTitle('');
      setSourceUrl('');
      setPublisher('');
      setPublishedOn('');
      setEffectiveOn('');
      setNotes('');
      items.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save this item');
    } finally {
      setBusy(false);
    }
  };

  const setStatus = async (item: ComplianceItem, status: string) => {
    try {
      await api.patch(`/compliance/${item.id}`, { status });
      items.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update status');
    }
  };

  const grouped = (items.data?.items ?? []).reduce<Record<string, ComplianceItem[]>>((acc, item) => {
    const key = item.jurisdiction;
    acc[key] = acc[key] ?? [];
    acc[key].push(item);
    return acc;
  }, {});

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Rules & compliance</h2>
          <p>
            A jurisdiction-first register. J.A.R.V.I.S. will not tell you that you are compliant, and
            this is not legal advice — consequential questions should go to a qualified professional.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone="amber">automated monitoring not built</Pill>
      </div>

      <Notice tone="amber" title="Read this before trusting any row">
        Each row records what <em>you</em> verified, with the publication date and the effective date
        kept separate. Automated regulation tracking, version comparison and change alerts are a later
        milestone — they are not running today.
      </Notice>

      {error && <Notice tone="red" title="Action failed" onDismiss={() => setError(null)}>{error}</Notice>}

      <div className="grid grid--sidebar">
        <Panel title="Register" subtitle={`${items.data?.items.length ?? 0} tracked items`}>
          {items.loading && !items.data ? (
            <Spinner label="Loading register…" />
          ) : (items.data?.items.length ?? 0) === 0 ? (
            <EmptyState
              title="Nothing tracked yet"
              body="Start with one obligation you must meet: data protection, GST registration, or platform terms."
            />
          ) : (
            <div className="stack stack--lg">
              {Object.entries(grouped).map(([code, list]) => (
                <div key={code} className="stack">
                  <div className="row">
                    <Pill tone="cyan">{code}</Pill>
                    <span className="small muted-2">{list.length} item(s)</span>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Area</th>
                          <th>Obligation</th>
                          <th>Published</th>
                          <th>Effective</th>
                          <th>Source</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {list.map((item) => (
                          <tr key={item.id}>
                            <td className="tiny">{AREA_LABELS[item.area] ?? item.area}</td>
                            <td>
                              <div>{item.title}</div>
                              {item.notes && <div className="tiny muted-2">{item.notes.slice(0, 160)}</div>}
                            </td>
                            <td className="tiny muted-2">{item.published_on ? formatDay(item.published_on) : '—'}</td>
                            <td className="tiny muted-2">{item.effective_on ? formatDay(item.effective_on) : '—'}</td>
                            <td className="tiny">
                              {item.source_url ? (
                                <a href={item.source_url} target="_blank" rel="noreferrer noopener">
                                  {item.publisher ?? 'link'}
                                </a>
                              ) : (
                                <span style={{ color: 'var(--amber)' }}>no source</span>
                              )}
                            </td>
                            <td>
                              <select
                                value={item.status}
                                onChange={(e) => void setStatus(item, e.target.value)}
                                aria-label={`Status for ${item.title}`}
                                style={{ width: 150 }}
                              >
                                {STATUSES.map((status) => (
                                  <option key={status} value={status}>
                                    {status.replace(/_/g, ' ')}
                                  </option>
                                ))}
                              </select>
                              <div style={{ marginTop: 4 }}>
                                <StatusBadge status={item.status} />
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Track an obligation" subtitle="Jurisdiction first">
          <form className="stack" onSubmit={create}>
            <div className="form-row form-row--2">
              <Field label="Jurisdiction" hint="Country code, e.g. IN or US">
                <input value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)} maxLength={8} />
              </Field>
              <Field label="Area">
                <select value={area} onChange={(e) => setArea(e.target.value)}>
                  {(items.data?.areas ?? Object.keys(AREA_LABELS)).map((key) => (
                    <option key={key} value={key}>
                      {AREA_LABELS[key] ?? key}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="Obligation">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Publish a consent notice before collecting personal data"
                maxLength={300}
              />
            </Field>
            <div className="form-row form-row--2">
              <Field label="Published on">
                <input type="date" value={publishedOn} onChange={(e) => setPublishedOn(e.target.value)} />
              </Field>
              <Field label="Effective on">
                <input type="date" value={effectiveOn} onChange={(e) => setEffectiveOn(e.target.value)} />
              </Field>
            </div>
            <div className="form-row form-row--2">
              <Field label="Source URL">
                <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://official…" />
              </Field>
              <Field label="Publisher">
                <input value={publisher} onChange={(e) => setPublisher(e.target.value)} placeholder="Regulator / ministry" />
              </Field>
            </div>
            <Field label="Notes" hint="Exact requirement, exceptions, and what you still need to confirm.">
              <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} maxLength={5000} />
            </Field>
            <div className="form-actions">
              <Button type="submit" variant="primary" disabled={busy || title.trim().length < 3}>
                {busy ? 'Saving…' : 'Add to register'}
              </Button>
            </div>
          </form>
        </Panel>
      </div>
    </>
  );
}
