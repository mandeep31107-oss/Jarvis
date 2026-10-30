import { Router } from 'express';
import { z } from 'zod';
import { requireAuth } from '../security/auth.js';
import {
  createTask,
  deleteTask,
  dependencyConflict,
  getTask,
  listTasks,
  taskStats,
  updateTask,
  type TaskPriority,
  type TaskStatus,
} from '../repo/tasks.js';
import { writeAudit } from '../services/audit.js';

const statusEnum = z.enum(['pending', 'in_progress', 'blocked', 'done', 'cancelled']);
const priorityEnum = z.enum(['low', 'medium', 'high', 'critical']);

const createSchema = z.object({
  title: z.string().min(3).max(300),
  description: z.string().max(4000).optional(),
  status: statusEnum.optional(),
  priority: priorityEnum.optional(),
  dueAt: z.string().datetime().nullable().optional(),
  estimateMinutes: z.number().int().min(5).max(10_000).nullable().optional(),
  project: z.string().max(120).nullable().optional(),
  dependsOn: z.array(z.string().uuid()).max(10).optional(),
});

const patchSchema = z.object({
  title: z.string().min(3).max(300).optional(),
  description: z.string().max(4000).optional(),
  status: statusEnum.optional(),
  priority: priorityEnum.optional(),
  dueAt: z.string().datetime().nullable().optional(),
  estimateMinutes: z.number().int().min(5).max(10_000).nullable().optional(),
  project: z.string().max(120).nullable().optional(),
});

export function tasksRouter(): Router {
  const router = Router();

  router.get('/', requireAuth, (req, res) => {
    const status = typeof req.query.status === 'string' ? (req.query.status as TaskStatus) : undefined;
    res.json({
      tasks: listTasks(req.user!.id, { status, project: typeof req.query.project === 'string' ? req.query.project : undefined }),
      stats: taskStats(req.user!.id),
    });
  });

  router.get('/stats', requireAuth, (req, res) => {
    res.json({ stats: taskStats(req.user!.id) });
  });

  router.post('/', requireAuth, (req, res) => {
    const parsed = createSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const task = createTask(req.user!.id, {
      title: parsed.data.title,
      description: parsed.data.description,
      status: parsed.data.status,
      priority: parsed.data.priority as TaskPriority | undefined,
      dueAt: parsed.data.dueAt ?? null,
      estimateMinutes: parsed.data.estimateMinutes ?? null,
      project: parsed.data.project ?? null,
      dependsOn: parsed.data.dependsOn,
      source: 'manual',
    });
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'task.created', target: task.id, detail: { title: task.title }, ip: req.ip });
    res.status(201).json({ task });
  });

  router.get('/:id', requireAuth, (req, res) => {
    const task = getTask(req.user!.id, req.params.id);
    if (!task) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    res.json({ task });
  });

  router.patch('/:id', requireAuth, (req, res) => {
    const parsed = patchSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const existing = getTask(req.user!.id, req.params.id);
    if (!existing) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    if (parsed.data.status === 'done') {
      const conflict = dependencyConflict(req.user!.id, existing.id);
      if (conflict) {
        res.status(409).json({ error: 'dependency_blocked', message: conflict });
        return;
      }
    }
    const task = updateTask(req.user!.id, existing.id, parsed.data as never);
    writeAudit({
      userId: req.user!.id,
      actor: 'owner',
      action: 'task.updated',
      target: existing.id,
      detail: { changes: parsed.data },
      ip: req.ip,
    });
    res.json({ task });
  });

  router.delete('/:id', requireAuth, (req, res) => {
    const deleted = deleteTask(req.user!.id, req.params.id);
    if (!deleted) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'task.deleted', target: req.params.id, ip: req.ip });
    res.json({ ok: true });
  });

  return router;
}
