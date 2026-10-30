import { describe, expect, it } from 'vitest';
import { missingSourceWarning, validateCompletion } from '../server/ai/validation.js';
import { hashPassword, passwordProblems, verifyPassword } from '../server/security/auth.js';
import { htmlToText } from '../server/tools/research.js';

describe('output validation', () => {
  it('corrects a fabricated action claim when nothing was executed', () => {
    const result = validateCompletion('I have sent the email to the client.', { executedActionTypes: [] });
    expect(result.fabricatedActionClaims.length).toBeGreaterThan(0);
    expect(result.text).toContain('Correction from the QA agent');
    expect(result.warnings.length).toBeGreaterThan(0);
  });

  it('does not add a correction when an action really executed', () => {
    const result = validateCompletion('I have sent the email to the client.', {
      executedActionTypes: ['send_message'],
    });
    expect(result.fabricatedActionClaims).toHaveLength(0);
    expect(result.text).not.toContain('Correction from the QA agent');
  });

  it('redacts credential-like strings', () => {
    const result = validateCompletion('Here is the key sk-abcdefghijklmnopqrstuvwx and ghp_aaaaaaaaaaaaaaaaaaaaa', {
      executedActionTypes: [],
    });
    expect(result.text).not.toContain('sk-abcdefghijklmnopqrstuvwx');
    expect(result.text).not.toContain('ghp_aaaaaaaaaaaaaaaaaaaaa');
    expect(result.redactions).toBe(2);
  });

  it('flags factual claims that cite no source', () => {
    expect(missingSourceWarning('In 2025, 63% of startups failed according to the report.', 'research')).toBeTruthy();
    expect(missingSourceWarning('See https://example.gov.in/act for the rule.', 'research')).toBeNull();
    expect(missingSourceWarning('I cannot confirm this without a source.', 'marketing')).toBeNull();
  });
});

describe('password handling', () => {
  it('verifies a correct password and rejects a wrong one', () => {
    const { hash, salt } = hashPassword('correct horse battery 9');
    expect(verifyPassword('correct horse battery 9', hash, salt)).toBe(true);
    expect(verifyPassword('wrong password 9', hash, salt)).toBe(false);
  });

  it('never stores the plaintext password', () => {
    const { hash, salt } = hashPassword('supersecret123');
    expect(hash).not.toContain('supersecret123');
    expect(salt).not.toContain('supersecret123');
    expect(hash.length).toBeGreaterThan(40);
  });

  it('produces different hashes for the same password (unique salt)', () => {
    const a = hashPassword('same-password-1');
    const b = hashPassword('same-password-1');
    expect(a.hash).not.toBe(b.hash);
  });

  it('enforces a minimum strength policy', () => {
    expect(passwordProblems('short')).not.toHaveLength(0);
    expect(passwordProblems('alllettersonly')).not.toHaveLength(0);
    expect(passwordProblems('goodpass123')).toHaveLength(0);
  });
});

describe('research HTML handling', () => {
  it('extracts readable text and strips scripts', () => {
    const parsed = htmlToText(
      '<html><head><title>Official Notice</title><style>body{}</style></head><body><script>alert(1)</script><h1>Rule</h1><p>Effective 1 April 2026.</p></body></html>',
    );
    expect(parsed.title).toBe('Official Notice');
    expect(parsed.text).toContain('Effective 1 April 2026');
    expect(parsed.text).not.toContain('alert(1)');
    expect(parsed.text).not.toContain('body{}');
  });
});
