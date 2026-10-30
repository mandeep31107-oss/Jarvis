import { useState } from 'react';
import { Button, EmptyState, Field, Notice, Panel, Pill, Spinner, Stat, formatDay } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { money, useAsync } from '../lib/hooks';
import type { FinanceEntry, FinanceSummary, ForecastResult } from '../lib/types';

export default function FinanceView() {
  const entries = useAsync<{ entries: FinanceEntry[]; summary: FinanceSummary; currencies: string[] }>(
    () => api.get('/finance/entries'),
    [],
  );

  const [kind, setKind] = useState<'income' | 'expense'>('expense');
  const [category, setCategory] = useState('');
  const [amount, setAmount] = useState('');
  const [occurredOn, setOccurredOn] = useState(new Date().toISOString().slice(0, 10));
  const [recurring, setRecurring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [startingCash, setStartingCash] = useState('50000');
  const [monthlyRevenue, setMonthlyRevenue] = useState('0');
  const [monthlyCosts, setMonthlyCosts] = useState('15000');
  const [revenueGrowth, setRevenueGrowth] = useState('10');
  const [costGrowth, setCostGrowth] = useState('0');
  const [months, setMonths] = useState('12');
  const [forecast, setForecast] = useState<ForecastResult | null>(null);
  const [forecastError, setForecastError] = useState<string | null>(null);

  const addEntry = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const amountMinor = Math.round(Number(amount) * 100);
    if (!Number.isFinite(amountMinor) || amountMinor <= 0) {
      setError('Enter an amount greater than zero.');
      setBusy(false);
      return;
    }
    try {
      await api.post('/finance/entries', {
        kind,
        category: category.trim() || 'uncategorised',
        amountMinor,
        currency: 'INR',
        occurredOn,
        recurring,
      });
      setAmount('');
      setCategory('');
      entries.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the entry');
    } finally {
      setBusy(false);
    }
  };

  const removeEntry = async (entry: FinanceEntry) => {
    if (!window.confirm('Delete this finance entry?')) return;
    try {
      await api.del(`/finance/entries/${entry.id}`);
      entries.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the entry');
    }
  };

  const runForecast = async (event: React.FormEvent) => {
    event.preventDefault();
    setForecastError(null);
    try {
      const result = await api.post<ForecastResult>('/finance/forecast', {
        currency: 'INR',
        startingCashMinor: Math.round(Number(startingCash) * 100),
        monthlyRevenueMinor: Math.round(Number(monthlyRevenue) * 100),
        monthlyCostsMinor: Math.round(Number(monthlyCosts) * 100),
        monthlyRevenueGrowthPct: Number(revenueGrowth),
        monthlyCostGrowthPct: Number(costGrowth),
        months: Number(months),
      });
      setForecast(result);
    } catch (err) {
      setForecastError(err instanceof ApiError ? err.message : 'The forecast could not be calculated');
    }
  };

  const summary = entries.data?.summary;

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Finance</h2>
          <p>
            Records and deterministic forecasts. Nothing here can move money, and every projection
            states its assumptions, period and currency.
          </p>
        </div>
        <div className="page-head__spacer" />
        <Pill tone="cyan">INR default</Pill>
      </div>

      {error && <Notice tone="red" title="Entry not saved" onDismiss={() => setError(null)}>{error}</Notice>}

      {summary && (
        <div className="grid grid--4">
          <Stat label="Income (recorded)" value={money(summary.incomeMinor)} />
          <Stat label="Expenses (recorded)" value={money(summary.expenseMinor)} />
          <Stat label="Net" value={money(summary.netMinor)} hint="Income minus expenses — not accounting profit" />
          <Stat label="Recurring / month" value={money(summary.recurringMonthlyMinor)} />
        </div>
      )}

      <div className="grid grid--2">
        <Panel title="Recorded entries" subtitle={summary ? `${summary.entryCount} entries` : undefined}>
          {entries.loading && !entries.data ? (
            <Spinner label="Loading entries…" />
          ) : (entries.data?.entries.length ?? 0) === 0 ? (
            <EmptyState title="No entries" body="Record your first income or expense to unlock the summary and cash-flow view." />
          ) : (
            <div className="table-wrap scroll-y">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Category</th>
                    <th>Kind</th>
                    <th className="num">Amount</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {entries.data?.entries.map((entry) => (
                    <tr key={entry.id}>
                      <td className="tiny muted-2">{formatDay(entry.occurred_on)}</td>
                      <td>
                        {entry.category}
                        {entry.recurring === 1 && <Pill tone="violet">recurring</Pill>}
                      </td>
                      <td>
                        <Pill tone={entry.kind === 'income' ? 'green' : 'amber'}>{entry.kind}</Pill>
                      </td>
                      <td className="num">{money(entry.amount_minor, entry.currency)}</td>
                      <td>
                        <button className="btn btn--subtle btn--sm" onClick={() => void removeEntry(entry)} aria-label="Delete entry">
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {summary && summary.byCategory.length > 0 && (
            <>
              <hr className="hr" />
              <p className="tiny muted-2" style={{ marginBottom: 6 }}>BY CATEGORY</p>
              <div className="list">
                {summary.byCategory.slice(0, 8).map((row) => (
                  <div key={`${row.kind}-${row.category}`} className="list__row">
                    <div className="list__main">
                      <div className="list__title">{row.category}</div>
                      <div className="list__sub">
                        {row.kind} · {row.count} entries
                      </div>
                    </div>
                    <span className="mono small">{money(row.totalMinor)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </Panel>

        <Panel title="Add entry" subtitle="Records only — no bank connection">
          <form className="stack" onSubmit={addEntry}>
            <div className="form-row form-row--2">
              <Field label="Kind">
                <select value={kind} onChange={(e) => setKind(e.target.value as 'income' | 'expense')}>
                  <option value="expense">Expense</option>
                  <option value="income">Income</option>
                </select>
              </Field>
              <Field label="Amount (₹)">
                <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" placeholder="1500" />
              </Field>
            </div>
            <Field label="Category">
              <input value={category} onChange={(e) => setCategory(e.target.value)} placeholder="api credits / hosting / salary" maxLength={80} />
            </Field>
            <div className="form-row form-row--2">
              <Field label="Date">
                <input type="date" value={occurredOn} onChange={(e) => setOccurredOn(e.target.value)} />
              </Field>
              <Field label="Recurring monthly">
                <select value={recurring ? 'yes' : 'no'} onChange={(e) => setRecurring(e.target.value === 'yes')}>
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
              </Field>
            </div>
            <div className="form-actions">
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? 'Saving…' : 'Record entry'}
              </Button>
            </div>
          </form>
        </Panel>
      </div>

      <Panel
        title="Cash-flow & runway forecast"
        subtitle="Deterministic maths — assumptions are shown with the result"
      >
        <form className="stack" onSubmit={runForecast}>
          <div className="form-row form-row--3">
            <Field label="Starting cash (₹)">
              <input value={startingCash} onChange={(e) => setStartingCash(e.target.value)} inputMode="decimal" />
            </Field>
            <Field label="Month 1 revenue (₹)">
              <input value={monthlyRevenue} onChange={(e) => setMonthlyRevenue(e.target.value)} inputMode="decimal" />
            </Field>
            <Field label="Month 1 costs (₹)">
              <input value={monthlyCosts} onChange={(e) => setMonthlyCosts(e.target.value)} inputMode="decimal" />
            </Field>
          </div>
          <div className="form-row form-row--3">
            <Field label="Revenue growth % / month">
              <input value={revenueGrowth} onChange={(e) => setRevenueGrowth(e.target.value)} inputMode="decimal" />
            </Field>
            <Field label="Cost growth % / month">
              <input value={costGrowth} onChange={(e) => setCostGrowth(e.target.value)} inputMode="decimal" />
            </Field>
            <Field label="Months (1–60)">
              <input value={months} onChange={(e) => setMonths(e.target.value)} inputMode="numeric" />
            </Field>
          </div>
          <div className="form-actions">
            <span className="tiny muted-2" style={{ marginRight: 'auto' }}>
              Nothing is stored by this calculation.
            </span>
            <Button type="submit" variant="primary">
              Calculate
            </Button>
          </div>
        </form>

        {forecastError && <Notice tone="red" title="Calculation failed">{forecastError}</Notice>}

        {forecast && (
          <>
            <hr className="hr" />
            <div className="grid grid--3" style={{ marginBottom: 12 }}>
              <Stat
                label="Runway"
                value={forecast.runwayMonths === null ? 'Sustainable' : `${forecast.runwayMonths} months`}
                hint="Cash ÷ monthly burn at month 1"
              />
              <Stat
                label="Break-even"
                value={forecast.breakEvenMonth ? `Month ${forecast.breakEvenMonth}` : 'Not reached'}
                hint="First month where net ≥ 0"
              />
              <Stat label="Currency" value={forecast.currency} hint={`${forecast.months} month projection`} />
            </div>

            <div className="table-wrap scroll-y">
              <table>
                <thead>
                  <tr>
                    <th>Month</th>
                    <th className="num">Revenue</th>
                    <th className="num">Costs</th>
                    <th className="num">Net</th>
                    <th className="num">Closing cash</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.rows.map((row) => (
                    <tr key={row.month}>
                      <td>{row.label}</td>
                      <td className="num">{money(row.revenueMinor, forecast.currency)}</td>
                      <td className="num">{money(row.costsMinor, forecast.currency)}</td>
                      <td className="num" style={{ color: row.netMinor < 0 ? 'var(--red)' : 'var(--green)' }}>
                        {money(row.netMinor, forecast.currency)}
                      </td>
                      <td className="num" style={{ color: row.closingCashMinor < 0 ? 'var(--red)' : undefined }}>
                        {money(row.closingCashMinor, forecast.currency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="grid grid--2" style={{ marginTop: 14 }}>
              <div>
                <p className="tiny muted-2" style={{ marginBottom: 6 }}>ASSUMPTIONS</p>
                <ul className="stack small muted">
                  {forecast.assumptions.map((assumption) => (
                    <li key={assumption}>• {assumption}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="tiny muted-2" style={{ marginBottom: 6 }}>WARNINGS</p>
                <ul className="stack small" style={{ color: 'var(--amber)' }}>
                  {forecast.warnings.map((warning) => (
                    <li key={warning}>• {warning}</li>
                  ))}
                </ul>
              </div>
            </div>
            <p className="tiny muted-2" style={{ marginTop: 10 }}>{forecast.note}</p>
          </>
        )}
      </Panel>
    </>
  );
}
