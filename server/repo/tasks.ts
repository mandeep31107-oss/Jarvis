import { randomUUID } from 'node:crypto';
import { all, get, nowIso, run, tx } from '../db/index.js';

export type TaskStatus = 'pending' | 'in_progress' | 'blocked' | 'done' | 'cancelled';
export type TaskPriority = 'low' | 'medium' | 'high' | 'critical';

export interface Task {
  id: string;
  user_id: string;
  title: string;
  description: string;
  status: TaskStatus;
  priority: TaskPriority;
  due_at: string | null;
  estimate_minutes: number | null;
  project: string | null;
  source: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  depends_on: string[];
}

const VALID_STATUS: TaskStatus[] = ['pending', 'in_progress', 'blocked', 'done', 'cancelled'];
const VALID_PRIORITY: TaskPriority[] = ['low', 'medium', 'high', 'critical'];

export interface CreateTaskInput {
  title: string;
  description?: string;
  status?: TaskStatus;
  priority?: TaskPriority;
  dueAt?: string | null;
  estimateMinutes?: number | null;
  project?: string | null;
  source?: string;
  dependsOn?: string[];
}

export interface UpdateTaskInput {
  title?: string;
  description?: string;
  status?: TaskStatus;
  priority?: TaskPriority;
  dueAt?: string | null;
  estimateMinutes?: number | null;
  project?: string | null;
}

function hydrateTasks(rows: Array<Omit<Task, 'depends_on'>>): Task[] {
  return rows.map((row) => {
    const deps = all<{ depends_on_id: string }>(
      'SELECT depends_on_id FROM task_dependencies WHERE task_id = ?',
      [row.id],
    ).map((d) => d.depends_on_id);
    return { ...row, depends_on: deps };
  });
}

export function createTask(userId: string, input: CreateTaskInput): Task {
  const id = randomUUID();
  const ts = nowIso();
  const status = input.status ?? 'pending';
  return tx(() => {
    run(
      `INSERT INTO tasks (id, user_id, title, description, status, priority, due_at, estimate_minutes,
        project, source, created_at, updated_at, completed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        id,
        userId,
        input.title.slice(0, 300),
        (input.description ?? '').slice(0, 5000),
        status,
        input.priority ?? 'medium',
        input.dueAt ?? null,
        input.estimateMinutes ?? null,
        input.project ?? null,
        input.source ?? 'manual',
        ts,
        ts,
        status === 'done' ? ts : null,
      ],
    );
    for (const dep of input.dependsOn ?? []) {
      run('INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_id) VALUES (?, ?)', [id, dep]);
    }
    return getTask(userId, id)!;
  });
}

export function getTask(userId: string, id: string): Task | undefined {
  const row = get<Omit<Task, 'depends_on'>>('SELECT * FROM tasks WHERE id = ? AND user_id = ?', [id, userId]);
  return row ? hydrateTasks([row])[0] : undefined;
}

export function listTasks(
  userId: string,
  opts: { status?: TaskStatus; limit?: number; project?: string } = {},
): Task[] {
  const clauses = ['user_id = ?'];
  const params: Array<string | number> = [userId];
  if (opts.status) {
    clauses.push('status = ?');
    params.push(opts.status);
  }
  if (opts.project) {
    clauses.push('project = ?');
    params.push(opts.project);
  }
  params.push(Math.min(Math.max(opts.limit ?? 200, 1), 500));
  const rows = all<Omit<Task, 'depends_on'>>(
    `SELECT * FROM tasks WHERE ${clauses.join(' AND ')}
     ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
              COALESCE(due_at, '9999') ASC, created_at DESC
     LIMIT ?`,
    params as never,
  );
  return hydrateTasks(rows);
}

export function updateTask(userId: string, id: string, patch: UpdateTaskInput): Task | undefined {
  const existing = getTask(userId, id);
  if (!existing) return undefined;
  if (patch.status && !VALID_STATUS.includes(patch.status)) throw new Error('invalid_status');
  if (patch.priority && !VALID_PRIORITY.includes(patch.priority)) throw new Error('invalid_priority');

  const next = {
    title: patch.title ?? existing.title,
    description: patch.description ?? existing.description,
    status: patch.status ?? existing.status,
    priority: patch.priority ?? existing.priority,
    due_at: patch.dueAt === undefined ? existing.due_at : patch.dueAt,
    estimate_minutes: patch.estimateMinutes === undefined ? existing.estimate_minutes : patch.estimateMinutes,
    project: patch.project === undefined ? existing.project : patch.project,
  };
  const ts = nowIso();
  const completedAt =
    next.status === 'done' ? existing.completed_at ?? ts : null;
  run(
    `UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?, due_at = ?,
       estimate_minutes = ?, project = ?, updated_at = ?, completed_at = ?
     WHERE id = ? AND user_id = ?`,
    [
      next.title,
      next.description,
      next.status,
      next.priority,
      next.due_at,
      next.estimate_minutes,
      next.project,
      ts,
      completedAt,
      id,
      userId,
    ],
  );
  return getTask(userId, id);
}

export function deleteTask(userId: string, id: string): boolean {
  const res = run('DELETE FROM tasks WHERE id = ? AND user_id = ?', [id, userId]);
  return res.changes > 0;
}

export interface TaskStats {
  total: number;
  open: number;
  done: number;
  overdue: number;
  dueToday: number;
  blocked: number;
  byStatus: Record<string, number>;
}

export function taskStats(userId: string): TaskStats {
  const rows = all<{ status: string; due_at: string | null }>(
    'SELECT status, due_at FROM tasks WHERE user_id = ?',
    [userId],
  );
  const today = new Date().toISOString().slice(0, 10);
  const byStatus: Record<string, number> = {};
  let overdue = 0;
  let dueToday = 0;
  for (const row of rows) {
    byStatus[row.status] = (byStatus[row.status] ?? 0) + 1;
    if (row.due_at && row.status !== 'done' && row.status !== 'cancelled') {
      const dueDate = row.due_at.slice(0, 10);
      if (dueDate < today) overdue += 1;
      else if (dueDate === today) dueToday += 1;
    }
  }
  const done = byStatus.done ?? 0;
  return {
    total: rows.length,
    open: rows.length - done - (byStatus.cancelled ?? 0),
    done,
    overdue,
    dueToday,
    blocked: byStatus.blocked ?? 0,
    byStatus,
  };
}

/** Blocks a status change to done when an unfinished dependency exists. */
export function dependencyConflict(userId: string, taskId: string): string | null {
  const task = getTask(userId, taskId);
  if (!task) return null;
  for (const depId of task.depends_on) {
    const dep = getTask(userId, depId);
    if (dep && dep.status !== 'done' && dep.status !== 'cancelled') {
      return `Depends on "${dep.title}" which is still ${dep.status.replace('_', ' ')}.`;
    }
  }
  return null;
}
