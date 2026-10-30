import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import type { DatabaseSync } from 'node:sqlite';
import { logger } from '../logger.js';

/**
 * `node:sqlite` is loaded at runtime rather than through a static import so the
 * bundler never tries to resolve it as a package (Vite's builtin list predates
 * the module). Node >= 22.5 is required.
 */
const nodeRequire = createRequire(import.meta.url);
const sqliteModule = nodeRequire('node:sqlite') as typeof import('node:sqlite');
const DatabaseSyncCtor = sqliteModule.DatabaseSync;

/**
 * Persistence layer.
 *
 * The MVP uses SQLite through Node's built-in `node:sqlite` module (no native
 * compilation, works on Windows out of the box). Every query lives in a small
 * repository module, so moving to PostgreSQL later is a contained change:
 * the rest of the server only sees typed functions.
 */

export type SqlValue = string | number | null | Uint8Array;

let db: DatabaseSync | null = null;

export function initDatabase(file: string): DatabaseSync {
  if (db) return db;
  mkdirSync(path.dirname(file), { recursive: true });
  db = new DatabaseSyncCtor(file);
  // WAL improves concurrent reads; foreign keys must be enabled explicitly.
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec('PRAGMA busy_timeout = 5000');
  migrate(db);
  logger.info('database ready', { file, schemaVersion: currentVersion(db) });
  return db;
}

export function getDb(): DatabaseSync {
  if (!db) throw new Error('Database not initialised. Call initDatabase() first.');
  return db;
}

export function closeDatabase(): void {
  if (!db) return;
  db.close();
  db = null;
}

export function all<T>(sql: string, params: SqlValue[] = []): T[] {
  return getDb().prepare(sql).all(...params) as unknown as T[];
}

export function get<T>(sql: string, params: SqlValue[] = []): T | undefined {
  return getDb().prepare(sql).get(...params) as unknown as T | undefined;
}

export function run(sql: string, params: SqlValue[] = []): { changes: number; lastId: number } {
  const res = getDb().prepare(sql).run(...params);
  return { changes: Number(res.changes), lastId: Number(res.lastInsertRowid) };
}

/** Run a function inside a transaction; rolls back if it throws. */
export function tx<T>(fn: () => T): T {
  const database = getDb();
  database.exec('BEGIN IMMEDIATE');
  try {
    const result = fn();
    database.exec('COMMIT');
    return result;
  } catch (err) {
    database.exec('ROLLBACK');
    throw err;
  }
}

export function nowIso(): string {
  return new Date().toISOString();
}

// ---------------------------------------------------------------------------
// Migrations
// ---------------------------------------------------------------------------

interface Migration {
  version: number;
  name: string;
  sql: string;
  /**
   * True when the migration rebuilds a table other tables reference. Foreign
   * keys are disabled for the duration so the implicit DELETE on DROP TABLE
   * cannot cascade into sessions/tasks, then re-checked before commit.
   */
  foreignKeysOff?: boolean;
}

/** Exported for the upgrade test: it applies v1 by hand and then runs v2. */
export const MIGRATIONS: readonly Migration[] = [
  {
    version: 1,
    name: 'core',
    sql: /* sql */ `
      CREATE TABLE users (
        id            TEXT PRIMARY KEY,
        email         TEXT NOT NULL UNIQUE,
        name          TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        created_at    TEXT NOT NULL
      );

      CREATE TABLE sessions (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash   TEXT NOT NULL UNIQUE,
        created_at   TEXT NOT NULL,
        expires_at   TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        user_agent   TEXT
      );
      CREATE INDEX idx_sessions_user ON sessions(user_id);

      CREATE TABLE settings (
        user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key        TEXT NOT NULL,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
      );

      CREATE TABLE conversations (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      TEXT NOT NULL,
        agent_id   TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);

      CREATE TABLE messages (
        id              TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
        content         TEXT NOT NULL,
        agent_id        TEXT,
        language        TEXT,
        meta_json       TEXT,
        created_at      TEXT NOT NULL
      );
      CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

      CREATE TABLE tasks (
        id               TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title            TEXT NOT NULL,
        description      TEXT NOT NULL DEFAULT '',
        status           TEXT NOT NULL DEFAULT 'pending',
        priority         TEXT NOT NULL DEFAULT 'medium',
        due_at           TEXT,
        estimate_minutes INTEGER,
        project          TEXT,
        source           TEXT NOT NULL DEFAULT 'manual',
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        completed_at     TEXT
      );
      CREATE INDEX idx_tasks_user ON tasks(user_id, status, due_at);

      CREATE TABLE task_dependencies (
        task_id        TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        depends_on_id  TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        PRIMARY KEY (task_id, depends_on_id)
      );

      CREATE TABLE runs (
        id             TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id TEXT,
        agent_id       TEXT NOT NULL,
        intent         TEXT NOT NULL,
        language       TEXT NOT NULL DEFAULT 'en',
        status         TEXT NOT NULL DEFAULT 'planned',
        prompt_preview TEXT NOT NULL,
        stage_total    INTEGER NOT NULL DEFAULT 0,
        stage_done     INTEGER NOT NULL DEFAULT 0,
        error          TEXT,
        started_at     TEXT NOT NULL,
        finished_at    TEXT
      );
      CREATE INDEX idx_runs_user ON runs(user_id, started_at DESC);

      CREATE TABLE run_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        seq        INTEGER NOT NULL,
        stage      TEXT NOT NULL,
        status     TEXT NOT NULL,
        detail     TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX idx_run_events_run ON run_events(run_id, seq);

      CREATE TABLE approvals (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        run_id        TEXT,
        action_type   TEXT NOT NULL,
        payload_json  TEXT NOT NULL,
        risk          TEXT NOT NULL DEFAULT 'low',
        status        TEXT NOT NULL DEFAULT 'pending',
        summary       TEXT NOT NULL,
        result_json   TEXT,
        error         TEXT,
        created_at    TEXT NOT NULL,
        decided_at    TEXT,
        executed_at   TEXT
      );
      CREATE INDEX idx_approvals_user ON approvals(user_id, status, created_at DESC);

      CREATE TABLE memory_entries (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category     TEXT NOT NULL,
        title        TEXT NOT NULL,
        content      TEXT NOT NULL,
        source_type  TEXT NOT NULL CHECK (source_type IN ('user','ai','tool')),
        source_ref   TEXT,
        confidence   TEXT NOT NULL DEFAULT 'medium',
        project      TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
      );
      CREATE INDEX idx_memory_user ON memory_entries(user_id, category);

      CREATE TABLE audit_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT,
        actor      TEXT NOT NULL,
        action     TEXT NOT NULL,
        target     TEXT,
        detail_json TEXT,
        ip         TEXT,
        created_at TEXT NOT NULL
      );
      CREATE INDEX idx_audit_created ON audit_log(created_at DESC);

      CREATE TABLE usage_events (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider          TEXT NOT NULL,
        model             TEXT NOT NULL,
        prompt_tokens     INTEGER,
        completion_tokens INTEGER,
        cost_usd_estimate REAL,
        latency_ms        INTEGER NOT NULL,
        ok                INTEGER NOT NULL,
        created_at        TEXT NOT NULL
      );
      CREATE INDEX idx_usage_user ON usage_events(user_id, created_at DESC);

      CREATE TABLE research_notes (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        question      TEXT NOT NULL,
        jurisdiction  TEXT,
        summary       TEXT NOT NULL,
        sources_json  TEXT NOT NULL,
        status        TEXT NOT NULL,
        confidence    TEXT NOT NULL,
        created_at    TEXT NOT NULL
      );

      CREATE TABLE finance_entries (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind          TEXT NOT NULL CHECK (kind IN ('income','expense')),
        category      TEXT NOT NULL,
        amount_minor  INTEGER NOT NULL,
        currency      TEXT NOT NULL DEFAULT 'INR',
        occurred_on   TEXT NOT NULL,
        recurring     INTEGER NOT NULL DEFAULT 0,
        note          TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL
      );
      CREATE INDEX idx_finance_user ON finance_entries(user_id, occurred_on DESC);

      CREATE TABLE compliance_items (
        id             TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        jurisdiction   TEXT NOT NULL,
        area           TEXT NOT NULL,
        title          TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'to_review',
        source_url     TEXT,
        publisher      TEXT,
        published_on   TEXT,
        effective_on   TEXT,
        notes          TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
      );
      CREATE INDEX idx_compliance_user ON compliance_items(user_id, jurisdiction, area);
    `,
  },
  {
    version: 2,
    name: 'otp_sign_in',
    foreignKeysOff: true,
    sql: /* sql */ `
      -- users can now be identified by email, phone, or both. SQLite cannot
      -- relax NOT NULL in place, so the table is rebuilt and rows copied.
      CREATE TABLE users_new (
        id             TEXT PRIMARY KEY,
        email          TEXT UNIQUE,
        phone          TEXT UNIQUE,
        name           TEXT NOT NULL,
        password_hash  TEXT NOT NULL,
        password_salt  TEXT NOT NULL,
        email_verified INTEGER NOT NULL DEFAULT 0,
        phone_verified INTEGER NOT NULL DEFAULT 0,
        has_password   INTEGER NOT NULL DEFAULT 1,
        created_at     TEXT NOT NULL
      );

      INSERT INTO users_new (id, email, phone, name, password_hash, password_salt,
                             email_verified, phone_verified, has_password, created_at)
      SELECT id, lower(email), NULL, name, password_hash, password_salt, 1, 0, 1, created_at
      FROM users;

      DROP TABLE users;
      ALTER TABLE users_new RENAME TO users;

      CREATE TABLE otp_challenges (
        id          TEXT PRIMARY KEY,
        user_id     TEXT,
        channel     TEXT NOT NULL CHECK (channel IN ('email','phone')),
        target      TEXT NOT NULL,
        purpose     TEXT NOT NULL CHECK (purpose IN ('signup','login')),
        code_hash   TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        attempts    INTEGER NOT NULL DEFAULT 0,
        consumed_at TEXT,
        ip          TEXT,
        created_at  TEXT NOT NULL
      );
      CREATE INDEX idx_otp_target ON otp_challenges(target, purpose, created_at DESC);
      CREATE INDEX idx_otp_created ON otp_challenges(created_at);
    `,
  },
];

function currentVersion(database: DatabaseSync): number {
  const row = database.prepare('PRAGMA user_version').get() as
    | { user_version?: number }
    | undefined;
  return Number(row?.user_version ?? 0);
}

function migrate(database: DatabaseSync): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      applied_at TEXT NOT NULL
    );
  `);
  const row = database.prepare('SELECT MAX(version) AS v FROM schema_migrations').get() as
    | { v: number | null }
    | undefined;
  const applied = Number(row?.v ?? 0);

  for (const migration of MIGRATIONS) {
    if (migration.version <= applied) continue;
    // PRAGMA foreign_keys is a no-op inside a transaction, so it must be set
    // before BEGIN — and only when the migration asks for it.
    if (migration.foreignKeysOff) database.exec('PRAGMA foreign_keys = OFF');
    database.exec('BEGIN IMMEDIATE');
    try {
      database.exec(migration.sql);
      database
        .prepare('INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)')
        .run(migration.version, migration.name, nowIso());
      database.exec(`PRAGMA user_version = ${migration.version}`);
      database.exec('COMMIT');
      logger.info('migration applied', { version: migration.version, name: migration.name });
    } catch (err) {
      database.exec('ROLLBACK');
      if (migration.foreignKeysOff) database.exec('PRAGMA foreign_keys = ON');
      throw err;
    }
    if (migration.foreignKeysOff) {
      database.exec('PRAGMA foreign_keys = ON');
      const violations = database.prepare('PRAGMA foreign_key_check').all();
      if (violations.length > 0) {
        throw new Error(
          `Migration ${migration.version} left ${violations.length} foreign-key violation(s); restore from backup.`,
        );
      }
    }
  }
}
