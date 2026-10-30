import { Router } from 'express';
import { z } from 'zod';
import { createFinanceEntry, deleteFinanceEntry, financeSummary, listFinanceEntries } from '../repo/finance.js';
import { requireAuth } from '../security/auth.js';
import { writeAudit } from '../services/audit.js';
import { forecast, SUPPORTED_CURRENCIES } from '../tools/finance.js';

const entrySchema = z.object({
  kind: z.enum(['income', 'expense']),
  category: z.string().min(1).max(80),
  amountMinor: z.number().int().positive().max(1_000_000_000_00),
  currency: z.enum(SUPPORTED_CURRENCIES as [string, ...string[]]).optional(),
  occurredOn: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
  recurring: z.boolean().optional(),
  note: z.string().max(500).optional(),
});

const forecastSchema = z.object({
  currency: z.enum(SUPPORTED_CURRENCIES as [string, ...string[]]).default('INR'),
  startingCashMinor: z.number().int().min(0).max(100_000_000_000),
  monthlyRevenueMinor: z.number().int().min(0).max(100_000_000_000),
  monthlyCostsMinor: z.number().int().min(0).max(100_000_000_000),
  monthlyRevenueGrowthPct: z.number().min(-50).max(100).default(0),
  monthlyCostGrowthPct: z.number().min(-50).max(100).default(0),
  months: z.number().int().min(1).max(60).default(12),
});

export function financeRouter(): Router {
  const router = Router();

  router.get('/entries', requireAuth, (req, res) => {
    const currency = typeof req.query.currency === 'string' ? req.query.currency : 'INR';
    res.json({
      entries: listFinanceEntries(req.user!.id),
      summary: financeSummary(req.user!.id, currency),
      currencies: SUPPORTED_CURRENCIES,
    });
  });

  router.post('/entries', requireAuth, (req, res) => {
    const parsed = entrySchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const entry = createFinanceEntry(req.user!.id, parsed.data as never);
    writeAudit({
      userId: req.user!.id,
      actor: 'owner',
      action: 'finance.entry_created',
      target: entry.id,
      detail: { kind: entry.kind, amountMinor: entry.amount_minor },
      ip: req.ip,
    });
    res.status(201).json({ entry });
  });

  router.delete('/entries/:id', requireAuth, (req, res) => {
    if (!deleteFinanceEntry(req.user!.id, req.params.id)) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'finance.entry_deleted', target: req.params.id, ip: req.ip });
    res.json({ ok: true });
  });

  /** Pure calculation — nothing is stored, no money moves. */
  router.post('/forecast', requireAuth, (req, res) => {
    const parsed = forecastSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const result = forecast({
      currency: parsed.data.currency as never,
      startingCashMinor: parsed.data.startingCashMinor,
      monthlyRevenueMinor: parsed.data.monthlyRevenueMinor,
      monthlyCostsMinor: parsed.data.monthlyCostsMinor,
      monthlyRevenueGrowthPct: parsed.data.monthlyRevenueGrowthPct,
      monthlyCostGrowthPct: parsed.data.monthlyCostGrowthPct,
      months: parsed.data.months,
    });
    res.json(result);
  });

  return router;
}
