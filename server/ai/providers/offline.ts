import type { AiProvider, CompletionRequest, CompletionResult } from '../types.js';

/**
 * Offline responder used when no external model is configured.
 *
 * It is deliberately honest: it does not invent facts, statistics, laws or
 * research results. It returns structured, agent-specific working guidance
 * built from static templates so the whole application (runs, approvals,
 * tasks, audit trail) can be exercised without any API key or network access.
 */
export class OfflineProvider implements AiProvider {
  readonly id = 'offline';
  readonly model = 'jarvis-offline-templates';

  isConfigured(): boolean {
    return true;
  }

  describe(): string {
    return 'offline: deterministic templates, no language model, no internet';
  }

  async complete(req: CompletionRequest): Promise<CompletionResult> {
    const started = Date.now();
    const lastUser = [...req.messages].reverse().find((m) => m.role === 'user');
    const system = req.messages.find((m) => m.role === 'system')?.content ?? '';
    const text = renderOfflineAnswer(lastUser?.content ?? '', system);
    return {
      text,
      usage: { promptTokens: estimateTokens(req.messages.map((m) => m.content).join(' ')), completionTokens: estimateTokens(text) },
      provider: this.id,
      model: this.model,
      latencyMs: Date.now() - started,
      finishReason: 'offline',
    };
  }
}

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

const AGENT_LABELS: Array<[RegExp, string]> = [
  [/business strategist/i, 'Business Strategist'],
  [/startup operations/i, 'Startup Operations'],
  [/financial planning/i, 'Financial Planning'],
  [/coding agent/i, 'Coding'],
  [/website development/i, 'Website Development'],
  [/research agent/i, 'Research'],
  [/legal and compliance/i, 'Legal & Compliance Research'],
  [/marketing/i, 'Marketing'],
  [/social media/i, 'Social Media'],
  [/productivity/i, 'Productivity'],
  [/security/i, 'Security'],
  [/quality assurance/i, 'Quality Assurance'],
];

function detectAgentLabel(system: string): string {
  for (const [re, label] of AGENT_LABELS) if (re.test(system)) return label;
  return 'Coordinator';
}

function renderOfflineAnswer(userText: string, system: string): string {
  const agent = detectAgentLabel(system);
  const language = /LANGUAGE:\s*(hinglish|hi)/i.exec(system)?.[1]?.toLowerCase() ?? 'en';
  const question = userText.trim().slice(0, 240) || '(no message text)';

  if (language === 'hi') {
    return [
      `**${agent} — ऑफ़लाइन मोड**`,
      '',
      `आपने लिखा: "${question}"`,
      '',
      'अभी कोई भाषा मॉडल सेट नहीं है, इसलिए मैं केवल एक ढाँचा दे सकता हूँ। मैं कोई तथ्य, आँकड़ा या कानून अपने आप से नहीं बनाऊँगा।',
      '',
      '**अगला व्यावहारिक कदम**',
      '1. पहले लक्ष्य को एक पंक्ति में लिखिए — क्या बनना चाहिए और किस तारीख तक।',
      '2. जो मान्यताएँ हैं उन्हें अलग सूची में रखिए (असत्यापित)।',
      '3. इस हफ़्ते पूरा हो सकने वाला सबसे छोटा प्रयोग चुनिए।',
      '',
      'वास्तविक तर्क के लिए `.env` में AI_PROVIDER और AI_API_KEY सेट कीजिए, फिर सर्वर रीस्टार्ट कीजिए।',
      '',
      '_(ऑफ़लाइन टेम्पलेट उत्तर — यह भाषा मॉडल का उत्तर नहीं है।)_',
    ].join('\n');
  }

  if (language === 'hinglish') {
    return [
      `**${agent} — offline mode**`,
      '',
      `Aapne likha: "${question}"`,
      '',
      'Abhi koi language model configured nahi hai, so main sirf ek structure de sakta hoon. Main koi fact, statistic ya legal rule khud se invent nahi karunga.',
      '',
      '**Next practical step**',
      '1. Goal ek line mein likho: exact outcome + deadline.',
      '2. Assumptions ko alag list karo (unverified).',
      '3. Is hafte ka pehla chhota experiment choose karo.',
      '',
      'Real reasoning ke liye `.env` mein AI_PROVIDER + AI_API_KEY set karke restart karo.',
      '',
      '_(Offline template response — not a model answer.)_',
    ].join('\n');
  }

  return [
    `**${agent} — offline mode**`,
    '',
    `You asked: "${question}"`,
    '',
    'No language model is configured, so I will not guess at facts, figures, law or research results. Here is a structure you can act on right now:',
    '',
    '**1. Define the outcome**',
    '- One sentence: what should exist when this is done?',
    '- A date, and how you will know it worked.',
    '',
    '**2. Separate facts from assumptions**',
    '- Facts you already have (with source and date).',
    '- Assumptions that still need evidence — label them unverified.',
    '',
    '**3. Choose the smallest next experiment**',
    '- Something finishable this week, with an observable result.',
    '- Note what would make you stop or change direction.',
    '',
    '**4. Track it**',
    '- Create the task in the Tasks view so progress is recorded, not remembered.',
    '',
    'To get real reasoning and research, set `AI_PROVIDER` and `AI_API_KEY` in `.env`, then restart the server.',
    '',
    '_(Offline template response — not a model answer. No internet research was performed.)_',
  ].join('\n');
}
