import { randomUUID } from 'node:crypto';
import { all, get, nowIso, run } from '../db/index.js';

export interface ResearchSource {
  url: string;
  finalUrl?: string;
  title: string;
  fetchedAt: string;
  excerpt?: string;
  publisher?: string;
  publishedOn?: string | null;
}

export interface ResearchNote {
  id: string;
  user_id: string;
  question: string;
  jurisdiction: string | null;
  summary: string;
  sources_json: string;
  status: string;
  confidence: string;
  created_at: string;
}

export interface ResearchNoteView extends Omit<ResearchNote, 'sources_json'> {
  sources: ResearchSource[];
}

export function createResearchNote(
  userId: string,
  input: {
    question: string;
    jurisdiction?: string | null;
    summary: string;
    sources: ResearchSource[];
    status: string;
    confidence: string;
  },
): ResearchNoteView {
  const id = randomUUID();
  run(
    `INSERT INTO research_notes (id, user_id, question, jurisdiction, summary, sources_json, status, confidence, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      userId,
      input.question.slice(0, 500),
      input.jurisdiction ?? null,
      input.summary,
      JSON.stringify(input.sources),
      input.status,
      input.confidence,
      nowIso(),
    ],
  );
  return getResearchNote(userId, id)!;
}

function toView(row: ResearchNote): ResearchNoteView {
  let sources: ResearchSource[] = [];
  try {
    sources = JSON.parse(row.sources_json) as ResearchSource[];
  } catch {
    sources = [];
  }
  const { sources_json: _ignored, ...rest } = row;
  return { ...rest, sources };
}

export function listResearchNotes(userId: string, limit = 100): ResearchNoteView[] {
  return all<ResearchNote>(
    'SELECT * FROM research_notes WHERE user_id = ? ORDER BY created_at DESC LIMIT ?',
    [userId, Math.min(Math.max(limit, 1), 300)],
  ).map(toView);
}

export function getResearchNote(userId: string, id: string): ResearchNoteView | undefined {
  const row = get<ResearchNote>('SELECT * FROM research_notes WHERE id = ? AND user_id = ?', [id, userId]);
  return row ? toView(row) : undefined;
}
