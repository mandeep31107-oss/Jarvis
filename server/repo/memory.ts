import { randomUUID } from 'node:crypto';
import { all, get, nowIso, run } from '../db/index.js';

export type MemoryCategory =
  | 'preference'
  | 'project'
  | 'decision'
  | 'goal'
  | 'constraint'
  | 'deadline'
  | 'research';

export type MemorySourceType = 'user' | 'ai' | 'tool';
export type Confidence = 'high' | 'medium' | 'low';

export interface MemoryEntry {
  id: string;
  user_id: string;
  category: MemoryCategory;
  title: string;
  content: string;
  source_type: MemorySourceType;
  source_ref: string | null;
  confidence: Confidence;
  project: string | null;
  created_at: string;
  updated_at: string;
}

export const MEMORY_CATEGORIES: MemoryCategory[] = [
  'preference',
  'project',
  'decision',
  'goal',
  'constraint',
  'deadline',
  'research',
];

export function createMemory(
  userId: string,
  input: {
    category: MemoryCategory;
    title: string;
    content: string;
    sourceType?: MemorySourceType;
    sourceRef?: string | null;
    confidence?: Confidence;
    project?: string | null;
  },
): MemoryEntry {
  if (!MEMORY_CATEGORIES.includes(input.category)) throw new Error('invalid_category');
  const id = randomUUID();
  const ts = nowIso();
  run(
    `INSERT INTO memory_entries (id, user_id, category, title, content, source_type, source_ref,
       confidence, project, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      userId,
      input.category,
      input.title.slice(0, 200),
      input.content.slice(0, 8000),
      input.sourceType ?? 'user',
      input.sourceRef ?? null,
      input.confidence ?? 'medium',
      input.project ?? null,
      ts,
      ts,
    ],
  );
  return get<MemoryEntry>('SELECT * FROM memory_entries WHERE id = ?', [id])!;
}

export function listMemory(userId: string, category?: MemoryCategory): MemoryEntry[] {
  if (category) {
    return all<MemoryEntry>(
      'SELECT * FROM memory_entries WHERE user_id = ? AND category = ? ORDER BY updated_at DESC',
      [userId, category],
    );
  }
  return all<MemoryEntry>(
    'SELECT * FROM memory_entries WHERE user_id = ? ORDER BY updated_at DESC LIMIT 300',
    [userId],
  );
}

export function updateMemory(
  userId: string,
  id: string,
  patch: { title?: string; content?: string; confidence?: Confidence; category?: MemoryCategory },
): MemoryEntry | undefined {
  const existing = get<MemoryEntry>('SELECT * FROM memory_entries WHERE id = ? AND user_id = ?', [id, userId]);
  if (!existing) return undefined;
  run(
    `UPDATE memory_entries SET title = ?, content = ?, confidence = ?, category = ?, updated_at = ?
     WHERE id = ? AND user_id = ?`,
    [
      patch.title ?? existing.title,
      patch.content ?? existing.content,
      patch.confidence ?? existing.confidence,
      patch.category ?? existing.category,
      nowIso(),
      id,
      userId,
    ],
  );
  return get<MemoryEntry>('SELECT * FROM memory_entries WHERE id = ?', [id]);
}

export function deleteMemory(userId: string, id: string): boolean {
  return run('DELETE FROM memory_entries WHERE id = ? AND user_id = ?', [id, userId]).changes > 0;
}

/**
 * Cheap relevance retrieval: term overlap against title/content. This is
 * deliberately simple and local; embeddings/vector search can replace it behind
 * the same signature when the corpus justifies the cost.
 */
export function relevantMemory(userId: string, query: string, limit = 8): MemoryEntry[] {
  const terms = query
    .toLowerCase()
    .split(/[^a-z0-9\u0900-\u097F]+/)
    .filter((t) => t.length >= 3);
  const entries = listMemory(userId);
  if (terms.length === 0) return entries.slice(0, limit);
  const scored = entries.map((entry) => {
    const haystack = `${entry.title} ${entry.content} ${entry.category}`.toLowerCase();
    let score = 0;
    for (const term of terms) if (haystack.includes(term)) score += 1;
    if (entry.source_type === 'user') score += 0.5; // owner-stated facts rank slightly higher
    return { entry, score };
  });
  return scored
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((s) => s.entry);
}
