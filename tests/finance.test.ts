import { describe, expect, it } from 'vitest';
import { forecast, runwayMonths, summariseEntries } from '../server/tools/finance.js';

describe('runway', () => {
  it('returns months of cash at the current burn', () => {
    expect(runwayMonths(50_000_00, 10_000_00)).toBe(5);
  });

  it('returns null when the business is cash-flow positive', () => {
    expect(runwayMonths(10_000_00, -5_000_00)).toBeNull();
  });

  it('returns zero when there is no cash', () => {
    expect(runwayMonths(0, 10_000_00)).toBe(0);
  });
});

describe('forecast', () => {
  const base = {
    currency: 'INR' as const,
    startingCashMinor: 100_000_00,
    monthlyRevenueMinor: 0,
    monthlyCostsMinor: 10_000_00,
    monthlyRevenueGrowthPct: 0,
    monthlyCostGrowthPct: 0,
    months: 12,
  };

  it('projects cash and finds the month it goes negative', () => {
    const result = forecast(base);
    expect(result.rows).toHaveLength(12);
    expect(result.rows[0]?.closingCashMinor).toBe(90_000_00);
    expect(result.runwayMonths).toBe(10);
    expect(result.warnings.some((w) => w.includes('negative'))).toBe(true);
  });

  it('finds break-even when revenue covers costs', () => {
    const result = forecast({
      ...base,
      monthlyRevenueMinor: 5_000_00,
      monthlyCostsMinor: 10_000_00,
      monthlyRevenueGrowthPct: 30,
    });
    expect(result.breakEvenMonth).not.toBeNull();
    expect(result.rows[result.rows.length - 1]?.netMinor).toBeGreaterThan(0);
  });

  it('states assumptions, currency and uncertainty', () => {
    const result = forecast(base);
    expect(result.assumptions.length).toBeGreaterThanOrEqual(4);
    expect(result.assumptions.join(' ')).toContain('INR');
    expect(result.assumptions.join(' ')).toContain('estimate');
    expect(result.note).toContain('not profit');
  });

  it('clamps the projection window', () => {
    expect(forecast({ ...base, months: 500 }).rows).toHaveLength(60);
    expect(forecast({ ...base, months: 0 }).rows).toHaveLength(1);
  });
});

describe('budget summary', () => {
  const entries = [
    { kind: 'income' as const, category: 'client', amountMinor: 50_000_00, currency: 'INR', occurredOn: '2026-01-05', recurring: 0 as const },
    { kind: 'expense' as const, category: 'hosting', amountMinor: 1_200_00, currency: 'INR', occurredOn: '2026-01-06', recurring: 1 as const },
    { kind: 'expense' as const, category: 'hosting', amountMinor: 800_00, currency: 'INR', occurredOn: '2026-02-06', recurring: 0 as const },
    { kind: 'expense' as const, category: 'usd', amountMinor: 100, currency: 'USD', occurredOn: '2026-02-06', recurring: 0 as const },
  ];

  it('summarises only the requested currency', () => {
    const summary = summariseEntries(entries, 'INR');
    expect(summary.entryCount).toBe(3);
    expect(summary.incomeMinor).toBe(50_000_00);
    expect(summary.expenseMinor).toBe(2_000_00);
    expect(summary.netMinor).toBe(48_000_00);
    expect(summary.recurringMonthlyMinor).toBe(1_200_00);
    expect(summary.periodFrom).toBe('2026-01-05');
    expect(summary.periodTo).toBe('2026-02-06');
  });

  it('groups by category with counts', () => {
    const summary = summariseEntries(entries, 'INR');
    const hosting = summary.byCategory.find((row) => row.category === 'hosting');
    expect(hosting?.totalMinor).toBe(2_000_00);
    expect(hosting?.count).toBe(2);
  });
});
