import { describe, expect, it } from 'vitest';
import { OfflineProvider } from '../server/ai/providers/offline.js';

function systemFor(agentName: string, language: 'en' | 'hi' | 'hinglish'): string {
  return `You are J.A.R.V.I.S.\nSPECIALIST ROLE: ${agentName}\nLANGUAGE: ${language}`;
}

describe('offline provider', () => {
  it('always returns a labelled template and never claims a model answer', async () => {
    const provider = new OfflineProvider();
    const result = await provider.complete({
      messages: [
        { role: 'system', content: systemFor('Business Strategist', 'en') },
        { role: 'user', content: 'I want to start a company' },
      ],
    });
    expect(result.provider).toBe('offline');
    expect(result.text).toContain('Business Strategist');
    expect(result.text).toContain('offline mode');
    expect(result.text).toContain('not a model answer');
    expect(result.usage.completionTokens).toBeGreaterThan(0);
    // It must not invent research findings or numbers.
    expect(result.text).not.toMatch(/\d+% of|market size of/i);
  });

  it('responds in the requested language', async () => {
    const provider = new OfflineProvider();
    const hindi = await provider.complete({
      messages: [
        { role: 'system', content: systemFor('Financial Planning', 'hi') },
        { role: 'user', content: 'budget banao' },
      ],
    });
    expect(hindi.text).toMatch(/[\u0900-\u097F]/);

    const hinglish = await provider.complete({
      messages: [
        { role: 'system', content: systemFor('Productivity', 'hinglish') },
        { role: 'user', content: 'aaj kya karu' },
      ],
    });
    expect(hinglish.text.toLowerCase()).toContain('next practical step');
  });
});
