import { randomBytes } from 'node:crypto';
import path from 'node:path';
import { z } from 'zod';

/**
 * Configuration is validated once at boot. Anything invalid stops the server
 * instead of failing later in a request handler.
 *
 * Secrets (AI keys, session secret) live only here, on the server. They are
 * never sent to the browser: the public settings endpoint exposes non-secret
 * capability information only.
 */

export type AiProviderId = 'offline' | 'openai' | 'ollama';

const envSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  PORT: z.coerce.number().int().min(1).max(65535).default(5173),
  DATA_DIR: z.string().min(1).default('data'),
  SESSION_SECRET: z.string().optional(),
  SESSION_TTL_HOURS: z.coerce.number().int().min(1).max(24 * 365).default(168),
  AI_PROVIDER: z.enum(['offline', 'openai', 'ollama']).default('offline'),
  AI_BASE_URL: z.string().url().default('https://api.openai.com/v1'),
  AI_MODEL: z.string().min(1).default('gpt-4o-mini'),
  AI_API_KEY: z.string().optional(),
  AI_TIMEOUT_MS: z.coerce.number().int().min(1000).max(300_000).default(45_000),
  AI_MAX_OUTPUT_TOKENS: z.coerce.number().int().min(64).max(8192).default(900),
  AI_PRICE_INPUT_PER_MTOK: z.coerce.number().min(0).default(0.15),
  AI_PRICE_OUTPUT_PER_MTOK: z.coerce.number().min(0).default(0.6),
  AUTONOMY_LEVEL: z.coerce.number().int().min(1).max(3).default(2),
  ALLOW_OUTBOUND_FETCH: z
    .string()
    .default('false')
    .transform((v) => v.toLowerCase() === 'true'),
  RESEARCH_ALLOWED_HOSTS: z.string().default(''),
  WAKE_PHRASE: z.string().min(2).max(40).default('jarvis'),

  // --- one-time passcodes (email / SMS sign-in) -------------------------
  OTP_TTL_SECONDS: z.coerce.number().int().min(60).max(1800).default(300),
  OTP_MAX_ATTEMPTS: z.coerce.number().int().min(1).max(10).default(5),
  DEFAULT_COUNTRY_CODE: z
    .string()
    .regex(/^\+[1-9]\d{0,3}$/, 'Use E.164 format, e.g. +91')
    .default('+91'),

  // --- email delivery ---------------------------------------------------
  AUTH_RATE_LIMIT: z.coerce.number().int().min(1).max(10_000).default(20),
  MAIL_PROVIDER: z.enum(['console', 'smtp']).default('console'),
  SMTP_HOST: z.string().optional(),
  SMTP_PORT: z.coerce.number().int().min(1).max(65535).default(587),
  SMTP_SECURE: z
    .string()
    .default('false')
    .transform((v) => v.toLowerCase() === 'true'),
  SMTP_USER: z.string().optional(),
  SMTP_PASS: z.string().optional(),
  MAIL_FROM: z.string().optional(),

  // --- SMS delivery -----------------------------------------------------
  SMS_PROVIDER: z.enum(['console', 'twilio']).default('console'),
  TWILIO_ACCOUNT_SID: z.string().optional(),
  TWILIO_AUTH_TOKEN: z.string().optional(),
  TWILIO_FROM: z.string().optional(),
});

export interface AppConfig {
  env: 'development' | 'production' | 'test';
  isProduction: boolean;
  port: number;
  dataDir: string;
  dbFile: string;
  sessionSecret: string;
  sessionTtlHours: number;
  authRateLimit: number;
  /** True when SESSION_SECRET was missing and a throwaway one was generated. */
  ephemeralSessionSecret: boolean;
  ai: {
    provider: AiProviderId;
    baseUrl: string;
    model: string;
    apiKey: string | null;
    timeoutMs: number;
    maxOutputTokens: number;
    priceInputPerMTok: number;
    priceOutputPerMTok: number;
  };
  autonomyLevel: 1 | 2 | 3;
  otp: {
    ttlSeconds: number;
    maxAttempts: number;
    /** Codes may be revealed to the browser only in non-production + console delivery. */
    revealDevCodes: boolean;
  };
  mail: {
    provider: 'console' | 'smtp';
    host: string | null;
    port: number;
    secure: boolean;
    user: string | null;
    pass: string | null;
    from: string;
  };
  sms: {
    provider: 'console' | 'twilio';
    accountSid: string | null;
    authToken: string | null;
    from: string | null;
  };
  defaultCountryCode: string;
  allowOutboundFetch: boolean;
  researchAllowedHosts: string[];
  wakePhrase: string;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const parsed = envSchema.safeParse(env);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((i) => `  - ${i.path.join('.') || '(root)'}: ${i.message}`)
      .join('\n');
    throw new Error(`Invalid configuration:\n${issues}`);
  }
  const e = parsed.data;

  let sessionSecret = e.SESSION_SECRET?.trim() ?? '';
  let ephemeral = false;
  if (sessionSecret.length === 0) {
    if (e.NODE_ENV === 'production') {
      throw new Error(
        'SESSION_SECRET is required when NODE_ENV=production. Generate one with: ' +
          'node -e "console.log(require(\'crypto\').randomBytes(48).toString(\'base64url\'))"',
      );
    }
    sessionSecret = randomBytes(48).toString('base64url');
    ephemeral = true;
  } else if (sessionSecret.length < 32) {
    throw new Error('SESSION_SECRET must be at least 32 characters long.');
  }

  if (e.AI_PROVIDER !== 'offline' && !e.AI_API_KEY && e.AI_PROVIDER === 'openai') {
    // Ollama typically needs no key; hosted OpenAI-compatible APIs do.
    throw new Error(`AI_PROVIDER=${e.AI_PROVIDER} requires AI_API_KEY to be set.`);
  }

  if (e.MAIL_PROVIDER === 'smtp' && (!e.SMTP_HOST || !e.SMTP_USER || !e.SMTP_PASS)) {
    throw new Error('MAIL_PROVIDER=smtp requires SMTP_HOST, SMTP_USER and SMTP_PASS.');
  }
  if (e.SMS_PROVIDER === 'twilio' && (!e.TWILIO_ACCOUNT_SID || !e.TWILIO_AUTH_TOKEN || !e.TWILIO_FROM)) {
    throw new Error('SMS_PROVIDER=twilio requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM.');
  }

  const dataDir = path.resolve(process.cwd(), e.DATA_DIR);

  return {
    env: e.NODE_ENV,
    isProduction: e.NODE_ENV === 'production',
    port: e.PORT,
    dataDir,
    dbFile: path.join(dataDir, 'jarvis.sqlite'),
    sessionSecret,
    sessionTtlHours: e.SESSION_TTL_HOURS,
    /** Auth requests allowed per IP per 15 minutes (tests raise this). */
    authRateLimit: e.AUTH_RATE_LIMIT,
    ephemeralSessionSecret: ephemeral,
    ai: {
      provider: e.AI_PROVIDER,
      baseUrl: e.AI_BASE_URL.replace(/\/+$/, ''),
      model: e.AI_MODEL,
      apiKey: e.AI_API_KEY?.trim() ? e.AI_API_KEY.trim() : null,
      timeoutMs: e.AI_TIMEOUT_MS,
      maxOutputTokens: e.AI_MAX_OUTPUT_TOKENS,
      priceInputPerMTok: e.AI_PRICE_INPUT_PER_MTOK,
      priceOutputPerMTok: e.AI_PRICE_OUTPUT_PER_MTOK,
    },
    autonomyLevel: e.AUTONOMY_LEVEL as 1 | 2 | 3,
    otp: {
      ttlSeconds: e.OTP_TTL_SECONDS,
      maxAttempts: e.OTP_MAX_ATTEMPTS,
      // Never reveal passcodes in production, whatever the delivery provider is.
      revealDevCodes: e.NODE_ENV !== 'production',
    },
    mail: {
      provider: e.MAIL_PROVIDER,
      host: e.SMTP_HOST ?? null,
      port: e.SMTP_PORT,
      secure: e.SMTP_SECURE,
      user: e.SMTP_USER ?? null,
      pass: e.SMTP_PASS ?? null,
      from: e.MAIL_FROM ?? e.SMTP_USER ?? 'jarvis@localhost',
    },
    sms: {
      provider: e.SMS_PROVIDER,
      accountSid: e.TWILIO_ACCOUNT_SID ?? null,
      authToken: e.TWILIO_AUTH_TOKEN ?? null,
      from: e.TWILIO_FROM ?? null,
    },
    defaultCountryCode: e.DEFAULT_COUNTRY_CODE,
    allowOutboundFetch: e.ALLOW_OUTBOUND_FETCH,
    researchAllowedHosts: e.RESEARCH_ALLOWED_HOSTS.split(',')
      .map((h) => h.trim().toLowerCase())
      .filter(Boolean),
    wakePhrase: e.WAKE_PHRASE.toLowerCase(),
  };
}
