/**
 * Deterministic finance maths. No model is involved here — these numbers can be
 * reproduced by hand, which is the point: forecasts must be auditable.
 *
 * All amounts are integers in minor units (paise for INR) to avoid float drift.
 */

export type Currency = 'INR' | 'USD' | 'EUR' | 'GBP' | 'AED';

export const SUPPORTED_CURRENCIES: Currency[] = ['INR', 'USD', 'EUR', 'GBP', 'AED'];

export interface FinanceEntryLike {
  kind: 'income' | 'expense';
  category: string;
  amountMinor: number;
  currency: string;
  occurredOn: string; // YYYY-MM-DD
  recurring: 0 | 1;
}

export interface ForecastInput {
  currency: Currency;
  startingCashMinor: number;
  monthlyRevenueMinor: number;
  monthlyCostsMinor: number;
  monthlyRevenueGrowthPct: number;
  monthlyCostGrowthPct: number;
  months: number;
}

export interface ForecastRow {
  month: number;
  label: string;
  revenueMinor: number;
  costsMinor: number;
  netMinor: number;
  closingCashMinor: number;
}

export interface ForecastResult {
  currency: Currency;
  months: number;
  rows: ForecastRow[];
  runwayMonths: number | null;
  breakEvenMonth: number | null;
  assumptions: string[];
  warnings: string[];
  note: string;
}

export function runwayMonths(startingCashMinor: number, monthlyBurnMinor: number): number | null {
  if (monthlyBurnMinor <= 0) return null; // never runs out at current burn
  if (startingCashMinor <= 0) return 0;
  return Number((startingCashMinor / monthlyBurnMinor).toFixed(2));
}

export function forecast(input: ForecastInput): ForecastResult {
  const months = Math.max(1, Math.min(60, Math.trunc(input.months)));
  const rows: ForecastRow[] = [];
  let cash = input.startingCashMinor;
  let revenue = input.monthlyRevenueMinor;
  let costs = input.monthlyCostsMinor;
  let breakEvenMonth: number | null = null;
  let monthsUntilZero: number | null = null;

  for (let month = 1; month <= months; month += 1) {
    const net = Math.round(revenue - costs);
    const openingCash = cash;
    cash = Math.round(cash + net);
    rows.push({
      month,
      label: `M${month}`,
      revenueMinor: Math.round(revenue),
      costsMinor: Math.round(costs),
      netMinor: net,
      closingCashMinor: cash,
    });
    if (breakEvenMonth === null && net >= 0) breakEvenMonth = month;
    if (monthsUntilZero === null && openingCash >= 0 && cash < 0) {
      // Linear interpolation inside the month that crosses zero.
      const crossing = net === 0 ? month : month - 1 + openingCash / Math.abs(net);
      monthsUntilZero = Number(crossing.toFixed(2));
    }
    revenue = revenue * (1 + input.monthlyRevenueGrowthPct / 100);
    costs = costs * (1 + input.monthlyCostGrowthPct / 100);
  }

  const initialBurn = input.monthlyCostsMinor - input.monthlyRevenueMinor;
  const runway = monthsUntilZero ?? runwayMonths(input.startingCashMinor, initialBurn);

  const warnings: string[] = [
    'Forecast, not actual results. Revenue and cost assumptions are owner-provided.',
    'Cash balance is not accounting profit; taxes, depreciation and working-capital timing are not modelled.',
  ];
  if (monthsUntilZero !== null) {
    warnings.push(`Projected cash goes negative in month ${monthsUntilZero}.`);
  }
  if (input.monthlyRevenueGrowthPct > 20) {
    warnings.push('Revenue growth above 20% per month compounded is aggressive for a pre-launch product.');
  }
  if (input.monthlyCostGrowthPct > input.monthlyRevenueGrowthPct && input.months > 3) {
    warnings.push('Costs are growing faster than revenue; margin will compress over time.');
  }

  return {
    currency: input.currency,
    months,
    rows,
    runwayMonths: runway,
    breakEvenMonth,
    assumptions: [
      `Starting cash: ${formatMoney(input.startingCashMinor, input.currency)}`,
      `Month 1 revenue: ${formatMoney(input.monthlyRevenueMinor, input.currency)} (growth ${input.monthlyRevenueGrowthPct}%/month)`,
      `Month 1 costs: ${formatMoney(input.monthlyCostsMinor, input.currency)} (growth ${input.monthlyCostGrowthPct}%/month)`,
      `Period: ${months} month${months === 1 ? '' : 's'}, currency ${input.currency}`,
      'All figures are estimates in a single currency; FX, tax and payment fees are excluded.',
    ],
    warnings,
    note: 'Revenue is not profit. Cash balance is not accounting profit. These are forecasts, not actuals.',
  };
}

export interface BudgetSummaryRow {
  category: string;
  kind: 'income' | 'expense';
  totalMinor: number;
  count: number;
}

export interface BudgetSummary {
  currency: string;
  incomeMinor: number;
  expenseMinor: number;
  netMinor: number;
  byCategory: BudgetSummaryRow[];
  recurringMonthlyMinor: number;
  periodFrom: string | null;
  periodTo: string | null;
  entryCount: number;
}

export function summariseEntries(entries: FinanceEntryLike[], currency = 'INR'): BudgetSummary {
  const relevant = entries.filter((e) => e.currency === currency);
  let incomeMinor = 0;
  let expenseMinor = 0;
  let recurringMonthlyMinor = 0;
  const map = new Map<string, BudgetSummaryRow>();

  for (const entry of relevant) {
    const key = `${entry.kind}::${entry.category}`;
    const row = map.get(key) ?? { category: entry.category, kind: entry.kind, totalMinor: 0, count: 0 };
    row.totalMinor += entry.amountMinor;
    row.count += 1;
    map.set(key, row);
    if (entry.kind === 'income') incomeMinor += entry.amountMinor;
    else expenseMinor += entry.amountMinor;
    if (entry.recurring === 1) recurringMonthlyMinor += entry.amountMinor;
  }

  const dates = relevant.map((e) => e.occurredOn).sort();
  return {
    currency,
    incomeMinor,
    expenseMinor,
    netMinor: incomeMinor - expenseMinor,
    byCategory: [...map.values()].sort((a, b) => b.totalMinor - a.totalMinor),
    recurringMonthlyMinor,
    periodFrom: dates[0] ?? null,
    periodTo: dates[dates.length - 1] ?? null,
    entryCount: relevant.length,
  };
}

export function formatMoney(minor: number, currency: string): string {
  const amount = minor / 100;
  try {
    return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(0)}`;
  }
}
