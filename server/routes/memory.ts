import { Router } from 'express';
import { z } from 'zod';
import {
  createMemory,
  deleteMemory,
  listMemory,
  MEMORY_CATEGORIES,
  updateMemory,
  type MemoryCategory,
} from '../repo/memory.js';
import { requireAuth } from '../security/auth.js';
import { writeAudit } from '../services/audit.js';

const categoryEnum = z.enum(MEMORY_CATEGORIES as [string, ...string[]]);

const createSchema = z.object({
  category: categoryEnum,
  title: z.string().min(3).max(200),
  content: z.string().min(3).max(8000),
  confidence: z.enum(['high', 'medium', 'low']).optional(),
  project: z.string().max(120).nullable().optional(),
});

const patchSchema = z.object({
  title: z.string().min(3).max(200).optional(),
  content: z.string().min(3).max(8000).optional(),
  confidence: z.enum(['high', 'medium', 'low']).optional(),
  category: categoryEnum.optional(),
});

export function memoryRouter(): Router {
  const router = Router();

  router.get('/', requireAuth, (req, res) => {
    const category = typeof req.query.category === 'string' ? (req.query.category as MemoryCategory) : undefined;
    res.json({ entries: listMemory(req.user!.id, category), categories: MEMORY_CATEGORIES });
  });

  router.post('/', requireAuth, (req, res) => {
    const parsed = createSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const entry = createMemory(req.user!.id, {
      category: parsed.data.category as MemoryCategory,
      title: parsed.data.title,
      content: parsed.data.content,
      confidence: parsed.data.confidence,
      project: parsed.data.project ?? null,
      sourceType: 'user',
    });
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'memory.created', target: entry.id, ip: req.ip });
    res.status(201).json({ entry });
  });

  router.patch('/:id', requireAuth, (req, res) => {
    const parsed = patchSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const entry = updateMemory(req.user!.id, req.params.id, {
      ...parsed.data,
      category: parsed.data.category as MemoryCategory | undefined,
    });
    if (!entry) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'memory.updated', target: entry.id, ip: req.ip });
    res.json({ entry });
  });

  router.delete('/:id', requireAuth, (req, res) => {
    if (!deleteMemory(req.user!.id, req.params.id)) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'memory.deleted', target: req.params.id, ip: req.ip });
    res.json({ ok: true });
  });

  return router;
}
