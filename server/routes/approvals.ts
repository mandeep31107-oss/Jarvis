import { Router } from 'express';
import { z } from 'zod';
import { actionCatalogue, UNAVAILABLE_ACTIONS } from '../actions/index.js';
import type { AppConfig } from '../config.js';
import { effectiveConfig } from '../repo/settings.js';
import { requireAuth } from '../security/auth.js';
import { countPendingApprovals, decideApproval, listApprovals, type ApprovalStatus } from '../services/approvals.js';

const decideSchema = z.object({ decision: z.enum(['approve', 'reject']) });

export function approvalsRouter(config: AppConfig): Router {
  const router = Router();

  router.get('/', requireAuth, (req, res) => {
    const status = typeof req.query.status === 'string' ? (req.query.status as ApprovalStatus) : undefined;
    res.json({
      approvals: listApprovals(req.user!.id, status),
      pending: countPendingApprovals(req.user!.id),
    });
  });

  router.get('/catalogue', requireAuth, (_req, res) => {
    res.json({ available: actionCatalogue(), unavailable: UNAVAILABLE_ACTIONS });
  });

  router.post('/:id/decide', requireAuth, async (req, res) => {
    const parsed = decideSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request' });
      return;
    }
    const result = await decideApproval({
      userId: req.user!.id,
      approvalId: req.params.id,
      decision: parsed.data.decision,
      config: effectiveConfig(req.user!.id, config),
      ip: req.ip,
    });
    if (!result.ok) {
      const status = result.error === 'not_found' ? 404 : result.error === 'already_decided' ? 409 : 400;
      res.status(status).json({ error: result.error ?? 'failed', status: result.status, result: result.result ?? null });
      return;
    }
    res.json({ ok: true, status: result.status, result: result.result ?? null });
  });

  return router;
}
