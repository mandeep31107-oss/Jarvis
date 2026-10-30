import { randomUUID } from 'node:crypto';
import { all, get, nowIso, run } from '../db/index.js';

export type ComplianceStatus = 'to_review' | 'in_progress' | 'done' | 'not_applicable' | 'needs_legal_advice';

export interface ComplianceItem {
  id: string;
  user_id: string;
  jurisdiction: string;
  area: string;
  title: string;
  status: ComplianceStatus;
  source_url: string | null;
  publisher: string | null;
  published_on: string | null;
  effective_on: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export const COMPLIANCE_AREAS = [
  'company_formation',
  'taxation',
  'privacy_data',
  'intellectual_property',
  'employment',
  'consumer_protection',
  'cybersecurity',
  'ai_regulation',
  'marketing_advertising',
  'cross_border',
  'financial_services',
  'software_licensing',
  'platform_terms',
  'api_usage',
  'data_retention',
  'communications_consent',
] as const;

export type ComplianceArea = (typeof COMPLIANCE_AREAS)[number];

export function createComplianceItem(
  userId: string,
  input: {
    jurisdiction: string;
    area: ComplianceArea;
    title: string;
    status?: ComplianceStatus;
    sourceUrl?: string | null;
    publisher?: string | null;
    publishedOn?: string | null;
    effectiveOn?: string | null;
    notes?: string;
  },
): ComplianceItem {
  if (!COMPLIANCE_AREAS.includes(input.area)) throw new Error('invalid_area');
  const id = randomUUID();
  const ts = nowIso();
  run(
    `INSERT INTO compliance_items (id, user_id, jurisdiction, area, title, status, source_url,
       publisher, published_on, effective_on, notes, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id,
      userId,
      input.jurisdiction.toUpperCase().slice(0, 8),
      input.area,
      input.title.slice(0, 300),
      input.status ?? 'to_review',
      input.sourceUrl ?? null,
      input.publisher ?? null,
      input.publishedOn ?? null,
      input.effectiveOn ?? null,
      (input.notes ?? '').slice(0, 5000),
      ts,
      ts,
    ],
  );
  return get<ComplianceItem>('SELECT * FROM compliance_items WHERE id = ?', [id])!;
}

export function listComplianceItems(userId: string): ComplianceItem[] {
  return all<ComplianceItem>(
    'SELECT * FROM compliance_items WHERE user_id = ? ORDER BY jurisdiction, area, created_at DESC',
    [userId],
  );
}

export function updateComplianceItem(
  userId: string,
  id: string,
  patch: Partial<Pick<ComplianceItem, 'status' | 'notes' | 'source_url' | 'publisher' | 'published_on' | 'effective_on' | 'title'>>,
): ComplianceItem | undefined {
  const existing = get<ComplianceItem>('SELECT * FROM compliance_items WHERE id = ? AND user_id = ?', [id, userId]);
  if (!existing) return undefined;
  run(
    `UPDATE compliance_items SET title = ?, status = ?, notes = ?, source_url = ?, publisher = ?,
       published_on = ?, effective_on = ?, updated_at = ? WHERE id = ? AND user_id = ?`,
    [
      patch.title ?? existing.title,
      patch.status ?? existing.status,
      patch.notes ?? existing.notes,
      patch.source_url ?? existing.source_url,
      patch.publisher ?? existing.publisher,
      patch.published_on ?? existing.published_on,
      patch.effective_on ?? existing.effective_on,
      nowIso(),
      id,
      userId,
    ],
  );
  return get<ComplianceItem>('SELECT * FROM compliance_items WHERE id = ?', [id]);
}

export function deleteComplianceItem(userId: string, id: string): boolean {
  return run('DELETE FROM compliance_items WHERE id = ? AND user_id = ?', [id, userId]).changes > 0;
}
