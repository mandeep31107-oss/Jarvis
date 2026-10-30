import { all, get, nowIso, run } from '../db/index.js';
import type { AppConfig } from '../config.js';

export interface VoiceSettings {
  mode: 'text' | 'voice' | 'both';
  language: 'en-IN' | 'hi-IN' | 'en-GB' | 'en-US';
  rate: number;
  pitch: number;
  wakeEnabled: boolean;
  wakePhrase: string;
  readNotifications: boolean;
  privacyMode: boolean;
  reducedMotion: boolean;
}

export const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  mode: 'both',
  language: 'en-IN',
  rate: 1,
  pitch: 1,
  wakeEnabled: false,
  wakePhrase: 'jarvis',
  readNotifications: false,
  privacyMode: true,
  reducedMotion: false,
};

export function getSetting<T>(userId: string, key: string): T | null {
  const row = get<{ value_json: string }>('SELECT value_json FROM settings WHERE user_id = ? AND key = ?', [
    userId,
    key,
  ]);
  if (!row) return null;
  try {
    return JSON.parse(row.value_json) as T;
  } catch {
    return null;
  }
}

export function setSetting(userId: string, key: string, value: unknown): void {
  run(
    `INSERT INTO settings (user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)
     ON CONFLICT(user_id, key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at`,
    [userId, key, JSON.stringify(value), nowIso()],
  );
}

export function getAllSettings(userId: string): Record<string, unknown> {
  const rows = all<{ key: string; value_json: string }>('SELECT key, value_json FROM settings WHERE user_id = ?', [
    userId,
  ]);
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    try {
      out[row.key] = JSON.parse(row.value_json);
    } catch {
      out[row.key] = null;
    }
  }
  return out;
}

export function getVoiceSettings(userId: string, config: AppConfig): VoiceSettings {
  const stored = getSetting<Partial<VoiceSettings>>(userId, 'voice');
  const merged = { ...DEFAULT_VOICE_SETTINGS, ...(stored ?? {}) };
  if (!stored?.wakePhrase) merged.wakePhrase = config.wakePhrase;
  return merged;
}

export function getEffectiveAutonomy(userId: string, config: AppConfig): 1 | 2 | 3 {
  const stored = getSetting<number>(userId, 'autonomyLevel');
  if (stored === 1 || stored === 2 || stored === 3) return stored;
  return config.autonomyLevel;
}

export function effectiveConfig(userId: string, config: AppConfig): AppConfig {
  return { ...config, autonomyLevel: getEffectiveAutonomy(userId, config) };
}
