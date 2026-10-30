import { Router } from 'express';
import { z } from 'zod';
import {
  COMPLIANCE_AREAS,
  createComplianceItem,
  deleteComplianceItem,
  listComplianceItems,
  updateComplianceItem,
} from '../repo/compliance.js';
import { requireAuth } from '../security/auth.js';
import { writeAudit } from '../services/audit.js';

const areaEnum = z.enum(COMPLIANCE_AREAS as unknown as [string, ...string[]]);

const createSchema = z.object({
  jurisdiction: z.string().min(2).max(8),
  area: areaEnum,
  title: z.string().min(3).max(300),
  status: z.enum(['to_review', 'in_progress', 'done', 'not_applicable', 'needs_legal_advice']).optional(),
  sourceUrl: z.string().url().nullable().optional(),
  publisher: z.string().max(200).nullable().optional(),
  publishedOn: z.string().max(40).nullable().optional(),
  effectiveOn: z.string().max(40).nullable().optional(),
  notes: z.string().max(5000).optional(),
});

const patchSchema = z.object({
  title: z.string().min(3).max(300).optional(),
  status: z.enum(['to_review', 'in_progress', 'done', 'not_applicable', 'needs_legal_advice']).optional(),
  notes: z.string().max(5000).optional(),
  sourceUrl: z.string().url().nullable().optional(),
  publisher: z.string().max(200).nullable().optional(),
  publishedOn: z.string().max(40).nullable().optional(),
  effectiveOn: z.string().max(40).nullable().optional(),
});

export function complianceRouter(): Router {
  const router = Router();

  router.get('/', requireAuth, (req, res) => {
    res.json({ items: listComplianceItems(req.user!.id), areas: COMPLIANCE_AREAS });
  });

  router.post('/', requireAuth, (req, res) => {
    const parsed = createSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const item = createComplianceItem(req.user!.id, {
      ...parsed.data,
      area: parsed.data.area as never,
      sourceUrl: parsed.data.sourceUrl ?? null,
      publisher: parsed.data.publisher ?? null,
      publishedOn: parsed.data.publishedOn ?? null,
      effectiveOn: parsed.data.effectiveOn ?? null,
    });
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'compliance.item_created', target: item.id, ip: req.ip });
    res.status(201).json({ item });
  });

  router.patch('/:id', requireAuth, (req, res) => {
    const parsed = patchSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const item = updateComplianceItem(req.user!.id, req.params.id, {
      title: parsed.data.title,
      status: parsed.data.status as never,
      notes: parsed.data.notes,
      source_url: parsed.data.sourceUrl ?? undefined,
      publisher: parsed.data.publisher ?? undefined,
      published_on: parsed.data.publishedOn ?? undefined,
      effective_on: parsed.data.effectiveOn ?? undefined,
    });
    if (!item) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'compliance.item_updated', target: item.id, ip: req.ip });
    res.json({ item });
  });

  router.delete('/:id', requireAuth, (req, res) => {
    if (!deleteComplianceItem(req.user!.id, req.params.id)) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'compliance.item_deleted', target: req.params.id, ip: req.ip });
    res.json({ ok: true });
  });

  return router;
}
