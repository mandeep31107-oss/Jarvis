import { Router } from 'express';
import { SseStream } from '../http/sse.js';
import { requireAuth } from '../security/auth.js';
import {
  cancelRun,
  getRun,
  listRunEvents,
  listRuns,
  onRunEvent,
  progressPercent,
  recentRunEvents,
} from '../services/runs.js';
import { countPendingApprovals } from '../services/approvals.js';

export function runsRouter(): Router {
  const router = Router();

  router.get('/', requireAuth, (req, res) => {
    const runs = listRuns(req.user!.id, Number(req.query.limit ?? 30)).map((r) => ({
      ...r,
      progress: progressPercent(r),
    }));
    res.json({ runs, pendingApprovals: countPendingApprovals(req.user!.id) });
  });

  router.get('/events/recent', requireAuth, (req, res) => {
    res.json({ events: recentRunEvents(req.user!.id, 60).reverse() });
  });

  router.get('/:id', requireAuth, (req, res) => {
    const runRecord = getRun(req.user!.id, req.params.id);
    if (!runRecord) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    res.json({
      run: { ...runRecord, progress: progressPercent(runRecord) },
      events: listRunEvents(req.user!.id, runRecord.id),
    });
  });

  /**
   * Live workflow stream. Replays the last events, then forwards real events
   * emitted by the orchestrator while a turn is executing.
   */
  router.get('/stream', requireAuth, (req, res) => {
    const userId = req.user!.id;
    const sse = new SseStream(res);
    const replay = recentRunEvents(userId, 30).reverse();
    sse.send('replay', { events: replay });

    const unsubscribe = onRunEvent((event) => {
      if (event.user_id !== userId) return;
      sse.send('event', event);
    });

    const heartbeat = setInterval(() => sse.comment('keep-alive'), 20_000);
    const cleanup = () => {
      clearInterval(heartbeat);
      unsubscribe();
      sse.close();
    };
    req.on('close', cleanup);
    res.on('close', cleanup);
  });

  router.post('/:id/cancel', requireAuth, (req, res) => {
    const result = cancelRun(req.user!.id, req.params.id);
    res.status(result.cancelled ? 200 : 409).json(result);
  });

  return router;
}
