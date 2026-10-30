import { randomUUID } from 'node:crypto';
import { all, nowIso, run } from '../db/index.js';
import { summariseEntries, type FinanceEntryLike, type BudgetSummary } from '../tools/finance.js';

/** Snake-case row exactly as stored; converted to FinanceEntryLike for maths. */
export interface FinanceEntryRow {
  id: string;
  user_id: string;
  kind: 'income' | 'expense';
  category: string;
  amount_minor: number;
  currency: string;
  occurred_on: string;
  recurring: number;
  note: string;
  created_at: string;
}

function toLike(row: FinanceEntryRow): FinanceEntryLike {
  return {
    kind: row.kind,
    category: row.category,
    amountMinor: row.amount_minor,
    currency: row.currency,
    occurredOn: row.occurred_on,
    recurring: row.recurring === 1 ? 1 : 0,
  };
}

export interface CreateFinanceEntryInput {
  kind: 'income' | 'expense';
  category: string;
  amountMinor: number;
  currency?: string;
  occurredOn?: string;
  recurring?: boolean;
  note?: string;
}

export function createFinanceEntry(userId: string, input: CreateFinanceEntryInput): FinanceEntryRow {
  if (!Number.isInteger(input.amountMinor) || input.amountMinor <= 0) {
    throw new Error('amount_invalid');
  }
  if (input.kind !== 'income' && input.kind !== 'expense') throw new Error('kind_invalid');
  const id = randomUUID();
  run(
    `INSERT INTO finance_entries (id, user_id, kind, category, amount_minor, currency, occurred_on, recurring, note, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      userId,
      input.kind,
      input.category.slice(0, 80) || 'uncategorised',
      input.amountMinor,
      (input.currency ?? 'INR').toUpperCase(),
      input.occurredOn ?? new Date().toISOString().slice(0, 10),
      input.recurring ? 1 : 0,
      (input.note ?? '').slice(0, 500),
      nowIso(),
    ],
  );
  return all<FinanceEntryRow>('SELECT * FROM finance_entries WHERE id = ?', [id])[0]!;
}

export function listFinanceEntries(userId: string, limit = 200): FinanceEntryRow[] {
  return all<FinanceEntryRow>(
    'SELECT * FROM finance_entries WHERE user_id = ? ORDER BY occurred_on DESC, created_at DESC LIMIT ?',
    [userId, Math.min(Math.max(limit, 1), 1000)],
  );
}

export function deleteFinanceEntry(userId: string, id: string): boolean {
  return run('DELETE FROM finance_entries WHERE id = ? AND user_id = ?', [id, userId]).changes > 0;
}

export function financeSummary(userId: string, currency = 'INR'): BudgetSummary {
  return summariseEntries(listFinanceEntries(userId, 1000).map(toLike), currency);
}
