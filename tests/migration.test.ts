/**
 * Upgrade path: an existing v1 database (email-only owner, live session) must
 * survive the v2 "otp_sign_in" migration with its data intact.
 *
 * The migration rebuilds `users`, which SQLite cannot do inside an active
 * foreign-key cascade for `sessions`, so this is the risky step worth testing.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { MIGRATIONS, all, closeDatabase, get, initDatabase } from '../server/db/index.js';

const sqliteModule = (createRequire(import.meta.url) as (id: string) => unknown)(
  'node:sqlite',
) as typeof import('node:sqlite');

let dir: string;

afterAll(() => {
  closeDatabase();
  if (dir) rmSync(dir, { recursive: true, force: true });
});

describe('schema upgrade from v1', () => {
  it('keeps the owner, the session and the settings when v2 is applied', () => {
    dir = mkdtempSync(path.join(tmpdir(), 'jarvis-migration-'));
    const file = path.join(dir, 'jarvis.sqlite');
    const v1 = MIGRATIONS.find((migration) => migration.version === 1);
    expect(v1).toBeTruthy();

    // Build a v1 database exactly as the first release left it.
    const raw = new sqliteModule.DatabaseSync(file);
    raw.exec('PRAGMA foreign_keys = ON');
    raw.exec(`CREATE TABLE schema_migrations (
      version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)`);
    raw.exec(v1!.sql);
    raw
      .prepare('INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, ?, ?)')
      .run('core', new Date().toISOString());
    raw.exec('PRAGMA user_version = 1');
    raw
      .prepare(
        `INSERT INTO users (id, email, name, password_hash, password_salt, created_at)
         VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run('u-old', 'Owner@Example.com', 'Owner', 'hash', 'salt', new Date().toISOString());
    raw
      .prepare(
        `INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, last_seen_at, user_agent)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run('s-old', 'u-old', 'token-hash', new Date().toISOString(), new Date(Date.now() + 86_400_000).toISOString(), new Date().toISOString(), 'test');
    raw
      .prepare('INSERT INTO settings (user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)')
      .run('u-old', 'voice', '{"voiceURI":"en-IN"}', new Date().toISOString());
    raw.close();

    initDatabase(file);

    const user = get<{
      id: string;
      email: string | null;
      phone: string | null;
      has_password: number;
      email_verified: number;
      phone_verified: number;
    }>('SELECT * FROM users WHERE id = ?', ['u-old']);
    expect(user).toMatchObject({
      id: 'u-old',
      email: 'owner@example.com',
      phone: null,
      has_password: 1,
      email_verified: 1,
      phone_verified: 0,
    });

    const session = get<{ id: string }>('SELECT id FROM sessions WHERE id = ?', ['s-old']);
    expect(session?.id).toBe('s-old');

    const settings = all<{ key: string }>('SELECT key FROM settings WHERE user_id = ?', ['u-old']);
    expect(settings.map((row) => row.key)).toEqual(['voice']);

    const tables = all<{ name: string }>(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'otp_challenges'",
    );
    expect(tables).toHaveLength(1);

    const version = get<{ user_version: number }>('PRAGMA user_version');
    expect(version?.user_version).toBe(2);
  });

  it('reports no foreign-key violations after the rebuild', () => {
    const violations = all<Record<string, unknown>>('PRAGMA foreign_key_check');
    expect(violations).toEqual([]);
  });
});
