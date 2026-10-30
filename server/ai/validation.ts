/**
 * Output validation.
 *
 * Two things must never happen in this system:
 *   1. J.A.R.V.I.S. claiming an external action happened when it did not.
 *   2. A credential leaking into a rendered response.
 *
 * These checks are deterministic and cheap, so they run on every completion.
 */

const FAKE_ACTION_PATTERNS: Array<{ re: RegExp; label: string }> = [
  { re: /\b(i (have|'ve) (sent|emailed|messaged|posted|published|deployed|paid|transferred|booked|purchased))\b/i, label: 'claimed action performed' },
  { re: /\b(message|email|invoice|post|payment|transfer) (has been|was) (sent|delivered|published|made|completed|processed)\b/i, label: 'claimed delivery' },
  { re: /\b(successfully|i just) (sent|published|deployed|paid|transferred|submitted)\b/i, label: 'claimed successful action' },
  { re: /\byour (payment|transfer) (is|was) complete\b/i, label: 'claimed financial action' },
];

const SECRET_PATTERNS: RegExp[] = [
  /\bsk-[A-Za-z0-9_-]{16,}\b/g,
  /\bsk-ant-[A-Za-z0-9_-]{16,}\b/g,
  /\bAIza[A-Za-z0-9_-]{20,}\b/g,
  /\bghp_[A-Za-z0-9]{20,}\b/g,
  /\bBearer\s+[A-Za-z0-9._-]{20,}\b/g,
];

export interface ValidationResult {
  text: string;
  warnings: string[];
  fabricatedActionClaims: string[];
  redactions: number;
}

export interface ValidationContext {
  /** Action types that were actually executed during this turn. */
  executedActionTypes: string[];
}

export function validateCompletion(rawText: string, ctx: ValidationContext): ValidationResult {
  let text = rawText.trim();
  const warnings: string[] = [];
  let redactions = 0;

  for (const pattern of SECRET_PATTERNS) {
    text = text.replace(pattern, () => {
      redactions += 1;
      return '[redacted-credential]';
    });
  }
  if (redactions > 0) warnings.push('A credential-like string was removed from the response.');

  const fabricated: string[] = [];
  if (ctx.executedActionTypes.length === 0) {
    for (const { re, label } of FAKE_ACTION_PATTERNS) {
      if (re.test(text)) fabricated.push(label);
    }
  }

  if (fabricated.length > 0) {
    warnings.push('The response implied an external action was completed; a correction was appended.');
    text +=
      '\n\n> ⚠ **Correction from the QA agent:** no external action was actually executed in this turn. ' +
      'Nothing was sent, published, paid or deployed. Any such step must go through the approval centre.';
  }

  if (text.length < 20) {
    warnings.push('The response was unusually short.');
  }

  return {
    text,
    warnings,
    fabricatedActionClaims: fabricated,
    redactions,
  };
}

/** Very small heuristic to flag answers that cite nothing while making factual claims. */
export function missingSourceWarning(text: string, agentId: string): string | null {
  const citable = ['research', 'legal-compliance', 'business-strategist'];
  if (!citable.includes(agentId)) return null;
  const makesFactualClaim =
    /\b(according to|government|regulator|regulation|legislation|survey|white ?paper|study found)\b/i.test(text) ||
    /\b\d+(\.\d+)?\s?%/.test(text) ||
    /\b(19|20)\d\d\b/.test(text);
  const hasLink = /https?:\/\//i.test(text);
  if (makesFactualClaim && !hasLink) {
    return 'This answer makes factual claims but cites no source. Treat those claims as unverified.';
  }
  return null;
}
