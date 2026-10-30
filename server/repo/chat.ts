import { randomUUID } from 'node:crypto';
import { all, get, nowIso, run } from '../db/index.js';

export interface Conversation {
  id: string;
  user_id: string;
  title: string;
  agent_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageRow {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  agent_id: string | null;
  language: string | null;
  meta_json: string | null;
  created_at: string;
}

export interface ConversationSummary extends Conversation {
  message_count: number;
  last_message: string | null;
}

export function createConversation(userId: string, title: string, agentId?: string | null): Conversation {
  const id = randomUUID();
  const ts = nowIso();
  run(
    'INSERT INTO conversations (id, user_id, title, agent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
    [id, userId, title.slice(0, 120) || 'New conversation', agentId ?? null, ts, ts],
  );
  return get<Conversation>('SELECT * FROM conversations WHERE id = ?', [id])!;
}

export function getConversation(userId: string, id: string): Conversation | undefined {
  return get<Conversation>('SELECT * FROM conversations WHERE id = ? AND user_id = ?', [id, userId]);
}

export function listConversations(userId: string, limit = 50): ConversationSummary[] {
  return all<ConversationSummary>(
    `SELECT c.*,
            (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count,
            (SELECT m.content FROM messages m WHERE m.conversation_id = c.id ORDER BY m.created_at DESC LIMIT 1) AS last_message
     FROM conversations c WHERE c.user_id = ? ORDER BY c.updated_at DESC LIMIT ?`,
    [userId, Math.min(Math.max(limit, 1), 200)],
  );
}

export function addMessage(
  conversationId: string,
  role: 'user' | 'assistant' | 'system',
  content: string,
  extra: { agentId?: string | null; language?: string | null; meta?: Record<string, unknown> | null } = {},
): MessageRow {
  const id = randomUUID();
  const ts = nowIso();
  run(
    `INSERT INTO messages (id, conversation_id, role, content, agent_id, language, meta_json, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      conversationId,
      role,
      content,
      extra.agentId ?? null,
      extra.language ?? null,
      extra.meta ? JSON.stringify(extra.meta) : null,
      ts,
    ],
  );
  run('UPDATE conversations SET updated_at = ? WHERE id = ?', [ts, conversationId]);
  return get<MessageRow>('SELECT * FROM messages WHERE id = ?', [id])!;
}

export function listMessages(userId: string, conversationId: string, limit = 200): MessageRow[] {
  const conversation = getConversation(userId, conversationId);
  if (!conversation) return [];
  return all<MessageRow>(
    'SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC LIMIT ?',
    [conversationId, Math.min(Math.max(limit, 1), 500)],
  );
}

export function deleteConversation(userId: string, id: string): boolean {
  return run('DELETE FROM conversations WHERE id = ? AND user_id = ?', [id, userId]).changes > 0;
}

export function historyForModel(
  userId: string,
  conversationId: string,
  limit = 12,
): Array<{ role: 'user' | 'assistant'; content: string }> {
  const rows = all<MessageRow>(
    `SELECT m.* FROM messages m JOIN conversations c ON c.id = m.conversation_id
     WHERE m.conversation_id = ? AND c.user_id = ? AND m.role IN ('user','assistant')
     ORDER BY m.created_at DESC, m.rowid DESC LIMIT ?`,
    [conversationId, userId, limit],
  );
  return rows
    .reverse()
    .map((r) => ({ role: r.role === 'user' ? ('user' as const) : ('assistant' as const), content: r.content }));
}
