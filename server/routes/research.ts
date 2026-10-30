import { Router } from 'express';
import { z } from 'zod';
import { createResearchNote, listResearchNotes } from '../repo/research.js';
import { requireAuth } from '../security/auth.js';
import { writeAudit } from '../services/audit.js';

const noteSchema = z.object({
  question: z.string().min(3).max(500),
  jurisdiction: z.string().max(40).nullable().optional(),
  summary: z.string().min(3).max(20_000),
  sources: z
    .array(
      z.object({
        url: z.string().url(),
        title: z.string().max(300),
        fetchedAt: z.string(),
        excerpt: z.string().max(2000).optional(),
      }),
    )
    .max(20)
    .default([]),
});

export function researchRouter(): Router {
  const router = Router();

  router.get('/notes', requireAuth, (req, res) => {
    res.json({ notes: listResearchNotes(req.user!.id) });
  });

  /** Owner-entered notes. Any source you paste in is stored with its URL and date. */
  router.post('/notes', requireAuth, (req, res) => {
    const parsed = noteSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const note = createResearchNote(req.user!.id, {
      question: parsed.data.question,
      jurisdiction: parsed.data.jurisdiction ?? null,
      summary: parsed.data.summary,
      sources: parsed.data.sources,
      status: 'owner_entered',
      confidence: 'unverified',
    });
    writeAudit({ userId: req.user!.id, actor: 'owner', action: 'research.note_created', target: note.id, ip: req.ip });
    res.status(201).json({ note });
  });

  return router;
}
