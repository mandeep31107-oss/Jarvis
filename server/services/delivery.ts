import type { AppConfig } from '../config.js';
import { logger, errorMessage } from '../logger.js';

/**
 * Email and SMS delivery for one-time codes.
 *
 * Three states per channel:
 *   'ready'          — a real provider is configured (SMTP / Twilio)
 *   'console'        — development only: the code is written to the server log
 *                      and may be revealed in the browser when
 *                      NODE_ENV !== 'production'
 *   'not-configured' — production without credentials; the endpoint refuses
 *                      rather than pretending a message was sent
 *
 * Nothing here ever claims delivery unless the provider accepted the message.
 */

export type DeliveryMode = 'ready' | 'console' | 'not-configured';
export type DeliveryTransport = 'smtp' | 'twilio' | 'console';

export interface DeliveryOutcome {
  delivered: boolean;
  transport: DeliveryTransport | null;
  error?: string;
}

export function emailDeliveryMode(config: AppConfig): DeliveryMode {
  if (config.mail.provider === 'smtp') return 'ready';
  return config.isProduction ? 'not-configured' : 'console';
}

export function smsDeliveryMode(config: AppConfig): DeliveryMode {
  if (config.sms.provider === 'twilio') return 'ready';
  return config.isProduction ? 'not-configured' : 'console';
}

function emailBody(code: string, ttlMinutes: number): string {
  return [
    'Your J.A.R.V.I.S. sign-in code',
    '',
    `    ${code}`,
    '',
    `This code expires in ${ttlMinutes} minute(s) and can be used once.`,
    'If you did not request it, ignore this message — nothing has changed on your account.',
    '',
    '— J.A.R.V.I.S.',
  ].join('\n');
}

export async function deliverEmailCode(
  config: AppConfig,
  to: string,
  code: string,
): Promise<DeliveryOutcome> {
  const mode = emailDeliveryMode(config);
  const ttlMinutes = Math.max(1, Math.round(config.otp.ttlSeconds / 60));

  if (mode === 'not-configured') {
    return {
      delivered: false,
      transport: null,
      error: 'Email delivery is not configured on this server (set MAIL_PROVIDER=smtp).',
    };
  }

  if (mode === 'console') {
    logger.warn('DEVELOPMENT ONLY — email code not sent, shown here instead', {
      to,
      code,
      note: 'Configure MAIL_PROVIDER=smtp for real delivery.',
    });
    return { delivered: true, transport: 'console' };
  }

  try {
    const { default: nodemailer } = await import('nodemailer');
    const transporter = nodemailer.createTransport({
      host: config.mail.host ?? undefined,
      port: config.mail.port,
      secure: config.mail.secure,
      auth:
        config.mail.user && config.mail.pass
          ? { user: config.mail.user, pass: config.mail.pass }
          : undefined,
      connectionTimeout: 10_000,
      greetingTimeout: 10_000,
      socketTimeout: 15_000,
    });
    const info = await transporter.sendMail({
      from: config.mail.from,
      to,
      subject: 'Your J.A.R.V.I.S. sign-in code',
      text: emailBody(code, ttlMinutes),
    });
    const accepted = Array.isArray(info.accepted) ? info.accepted.length > 0 : Boolean(info.accepted);
    if (!accepted) {
      return { delivered: false, transport: 'smtp', error: 'The mail server did not accept the recipient.' };
    }
    logger.info('email code sent', { to, messageId: info.messageId ?? null });
    return { delivered: true, transport: 'smtp' };
  } catch (err) {
    const message = errorMessage(err);
    logger.warn('email delivery failed', { error: message });
    return {
      delivered: false,
      transport: 'smtp',
      error: `The mail server rejected the message: ${message.slice(0, 160)}`,
    };
  }
}

export async function deliverSmsCode(
  config: AppConfig,
  to: string,
  code: string,
): Promise<DeliveryOutcome> {
  const mode = smsDeliveryMode(config);
  const ttlMinutes = Math.max(1, Math.round(config.otp.ttlSeconds / 60));

  if (mode === 'not-configured') {
    return {
      delivered: false,
      transport: null,
      error: 'SMS delivery is not configured on this server (set SMS_PROVIDER=twilio).',
    };
  }

  if (mode === 'console') {
    logger.warn('DEVELOPMENT ONLY — SMS not sent, code shown here instead', {
      to,
      code,
      note: 'Configure SMS_PROVIDER=twilio for real delivery.',
    });
    return { delivered: true, transport: 'console' };
  }

  const sid = config.sms.accountSid;
  const token = config.sms.authToken;
  const from = config.sms.from;
  if (!sid || !token || !from) {
    return { delivered: false, transport: 'twilio', error: 'Twilio credentials are incomplete.' };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12_000);
  try {
    const body = new URLSearchParams({
      To: to,
      From: from,
      Body: `J.A.R.V.I.S. code: ${code}. Expires in ${ttlMinutes} min. Never share this code.`,
    });
    const res = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${encodeURIComponent(sid)}/Messages.json`, {
      method: 'POST',
      headers: {
        authorization: `Basic ${Buffer.from(`${sid}:${token}`).toString('base64')}`,
        'content-type': 'application/x-www-form-urlencoded',
      },
      body,
      signal: controller.signal,
    });
    if (!res.ok) {
      // Read the provider error but never echo credentials.
      const text = await res.text().catch(() => '');
      let detail = text.slice(0, 200);
      try {
        const json = JSON.parse(text) as { message?: string; code?: number };
        detail = json.message ? `${json.message}${json.code ? ` (${json.code})` : ''}` : detail;
      } catch {
        /* keep the raw snippet */
      }
      logger.warn('sms delivery failed', { status: res.status });
      return { delivered: false, transport: 'twilio', error: `SMS provider error HTTP ${res.status}: ${detail}` };
    }
    logger.info('sms code sent', { to });
    return { delivered: true, transport: 'twilio' };
  } catch (err) {
    logger.warn('sms delivery failed', { error: errorMessage(err) });
    return {
      delivered: false,
      transport: 'twilio',
      error: `Could not reach the SMS provider: ${errorMessage(err).slice(0, 120)}`,
    };
  } finally {
    clearTimeout(timer);
  }
}

/** Human-readable summary for the auth status endpoint and the UI. */
export function deliverySummary(config: AppConfig): {
  email: DeliveryMode;
  sms: DeliveryMode;
  emailTransport: DeliveryTransport | null;
  smsTransport: DeliveryTransport | null;
} {
  const email = emailDeliveryMode(config);
  const sms = smsDeliveryMode(config);
  return {
    email,
    sms,
    emailTransport: email === 'ready' ? 'smtp' : email === 'console' ? 'console' : null,
    smsTransport: sms === 'ready' ? 'twilio' : sms === 'console' ? 'console' : null,
  };
}
