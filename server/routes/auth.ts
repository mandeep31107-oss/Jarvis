import { randomUUID } from 'node:crypto';
import { Router, type Response } from 'express';
import { z } from 'zod';
import type { AppConfig } from '../config.js';
import { logger } from '../logger.js';
import {
  SESSION_COOKIE,
  countUsers,
  createSession,
  createUser,
  destroyOtherSessions,
  destroySession,
  findUserByEmail,
  findUserById,
  findUserByIdentifier,
  findUserByPhone,
  findUserSecretsById,
  hashPassword,
  markIdentifierVerified,
  passwordProblems,
  requireAuth,
  sessionCookieOptions,
  setPassword,
  unusablePassword,
  verifyPassword,
} from '../security/auth.js';
import { writeAudit } from '../services/audit.js';
import { deliverySummary, deliverEmailCode, deliverSmsCode } from '../services/delivery.js';
import {
  createChallenge,
  maskIdentifier,
  normalizeEmail,
  normalizePhone,
  purgeOldChallenges,
  recentRequestCount,
  secondsUntilNextRequest,
  verifyChallenge,
  type OtpChannel,
  type OtpPurpose,
} from '../services/otp.js';

const setupSchema = z.object({
  email: z.string().email().max(200),
  name: z.string().min(1).max(120),
  password: z.string().min(1).max(200),
});

const loginSchema = z
  .object({
    // `identifier` accepts an email address or a mobile number. `email` is kept
    // for backwards compatibility with older clients.
    identifier: z.string().min(3).max(254).optional(),
    email: z.string().max(254).optional(),
    password: z.string().min(1).max(200),
  })
  .refine((value) => Boolean(value.identifier || value.email), {
    message: 'Provide an email address or mobile number.',
  });

const otpRequestSchema = z.object({
  channel: z.enum(['email', 'phone']),
  target: z.string().min(3).max(254),
  purpose: z.enum(['signup', 'login']),
});

const otpVerifySchema = z.object({
  challengeId: z.string().uuid(),
  code: z
    .string()
    .min(6)
    .max(6)
    .regex(/^\d{6}$/, 'The code is 6 digits.'),
  name: z.string().min(1).max(120).optional(),
});

const passwordChangeSchema = z.object({
  currentPassword: z.string().max(200).optional(),
  newPassword: z.string().min(1).max(200),
});

const OTP_WINDOW_MINUTES = 15;
const OTP_MAX_PER_WINDOW = 3;

export function authRouter(config: AppConfig): Router {
  const router = Router();
  const delivery = deliverySummary(config);

  const startSession = (res: Response, userId: string, userAgent?: string): void => {
    const { token } = createSession(userId, config.sessionTtlHours, userAgent);
    res.cookie(SESSION_COOKIE, token, sessionCookieOptions(config.isProduction));
  };

  router.get('/status', (req, res) => {
    const emailMode = delivery.email;
    const smsMode = delivery.sms;
    res.json({
      needsSetup: countUsers() === 0,
      authenticated: Boolean(req.user),
      user: req.user ?? null,
      methods: {
        password: true,
        emailOtp: emailMode !== 'not-configured',
        phoneOtp: smsMode !== 'not-configured',
        emailOtpMode: emailMode,
        phoneOtpMode: smsMode,
        emailTransport: delivery.emailTransport,
        smsTransport: delivery.smsTransport,
        devCodesRevealed: config.otp.revealDevCodes,
        googleOAuth: false,
      },
      otp: { ttlSeconds: config.otp.ttlSeconds, maxAttempts: config.otp.maxAttempts },
    });
  });

  /** First-run owner creation with a password. Refuses once an owner exists. */
  router.post('/setup', (req, res) => {
    if (countUsers() > 0) {
      res.status(409).json({ error: 'already_configured', message: 'An owner account already exists. Please sign in.' });
      return;
    }
    const parsed = setupSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const email = normalizeEmail(parsed.data.email);
    if (!email) {
      res.status(400).json({ error: 'invalid_identifier', message: 'Enter a valid email address.' });
      return;
    }
    const problems = passwordProblems(parsed.data.password);
    if (problems.length > 0) {
      res.status(400).json({ error: 'weak_password', message: problems.join(' ') });
      return;
    }
    const { hash, salt } = hashPassword(parsed.data.password);
    const id = randomUUID();
    createUser({
      id,
      email,
      name: parsed.data.name.trim(),
      passwordHash: hash,
      passwordSalt: salt,
      emailVerified: false,
      hasPassword: true,
    });
    startSession(res, id, req.get('user-agent'));
    writeAudit({
      userId: id,
      actor: 'owner',
      action: 'auth.owner_created',
      target: 'password',
      detail: { channel: 'password' },
      ip: req.ip,
    });
    res.status(201).json({ user: findUserById(id) });
  });

  router.post('/login', (req, res) => {
    const parsed = loginSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request' });
      return;
    }
    const raw = (parsed.data.identifier ?? parsed.data.email ?? '').trim();
    const asEmail = normalizeEmail(raw);
    const asPhone = asEmail ? null : normalizePhone(raw, config.defaultCountryCode);
    const user = asEmail ? findUserByEmail(asEmail) : asPhone ? findUserByPhone(asPhone) : undefined;
    const target = asEmail ?? asPhone ?? 'invalid-identifier';
    if (!user || !user.hasPassword) {
      writeAudit({
        userId: user?.id ?? null,
        actor: 'anonymous',
        action: 'auth.login_failed',
        target,
        detail: { reason: 'unknown_or_passwordless' },
        ip: req.ip,
      });
      res.status(401).json({
        error: 'invalid_credentials',
        message: user
          ? 'This account has no password yet. Sign in with a one-time code, then set a password in Settings.'
          : 'Email or password is incorrect.',
      });
      return;
    }
    if (!verifyPassword(parsed.data.password, user.password_hash, user.password_salt)) {
      writeAudit({
        userId: user.id,
        actor: 'anonymous',
        action: 'auth.login_failed',
        target,
        detail: { reason: 'wrong_password' },
        ip: req.ip,
      });
      res.status(401).json({ error: 'invalid_credentials', message: 'Email or password is incorrect.' });
      return;
    }
    startSession(res, user.id, req.get('user-agent'));
    writeAudit({ userId: user.id, actor: 'owner', action: 'auth.login', detail: { method: 'password' }, ip: req.ip });
    res.json({ user: findUserById(user.id) });
  });

  /**
   * Step 1 of code sign-in: validate the identifier, apply limits, create a
   * challenge and ask the delivery service to send it.
   */
  router.post('/otp/request', async (req, res) => {
    const parsed = otpRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', issues: parsed.error.issues });
      return;
    }
    const channel = parsed.data.channel as OtpChannel;
    const purpose = parsed.data.purpose as OtpPurpose;

    const target =
      channel === 'email'
        ? normalizeEmail(parsed.data.target)
        : normalizePhone(parsed.data.target, config.defaultCountryCode);

    if (!target) {
      res.status(400).json({
        error: 'invalid_identifier',
        message:
          channel === 'email'
            ? 'Enter a valid email address, for example you@gmail.com.'
            : `Enter a valid mobile number, for example 98765 43210 or ${config.defaultCountryCode}9876543210.`,
      });
      return;
    }

    // Sign-up is only possible before the owner account exists.
    if (purpose === 'signup' && countUsers() > 0) {
      res.status(409).json({
        error: 'already_configured',
        message: 'This installation already has an owner. Sign in instead.',
      });
      return;
    }

    const existing =
      channel === 'email' ? findUserByEmail(target) : findUserByPhone(target);

    if (purpose === 'login' && !existing) {
      res.status(404).json({
        error: 'no_account',
        message:
          channel === 'email'
            ? 'No account uses that email address.'
            : 'No account is linked to that mobile number. Sign in with your email address instead.',
      });
      return;
    }
    if (purpose === 'signup' && existing) {
      res.status(409).json({ error: 'identifier_in_use', message: 'That identifier is already registered.' });
      return;
    }

    const mode = channel === 'email' ? delivery.email : delivery.sms;
    if (mode === 'not-configured') {
      res.status(503).json({
        error: 'delivery_not_configured',
        message:
          channel === 'email'
            ? 'Email sign-in is not configured on this server. Set MAIL_PROVIDER=smtp (and SMTP credentials), or sign in with a password.'
            : 'Mobile sign-in is not configured on this server. Set SMS_PROVIDER=twilio (and credentials), or sign in with email.',
      });
      return;
    }

    if (recentRequestCount(target, OTP_WINDOW_MINUTES) >= OTP_MAX_PER_WINDOW) {
      res.status(429).json({
        error: 'rate_limited',
        message: 'Too many codes requested for this identifier. Try again later.',
        retryAfterSeconds: secondsUntilNextRequest(target, OTP_WINDOW_MINUTES),
      });
      return;
    }

    purgeOldChallenges();
    const challenge = createChallenge({
      channel,
      target,
      purpose,
      userId: existing?.id ?? null,
      ttlSeconds: config.otp.ttlSeconds,
      secret: config.sessionSecret,
      ip: req.ip,
    });

    const outcome =
      channel === 'email'
        ? await deliverEmailCode(config, target, challenge.code)
        : await deliverSmsCode(config, target, challenge.code);

    if (!outcome.delivered) {
      // Consume the challenge: nobody received the code, so it must not work.
      verifyChallenge({ challengeId: challenge.id, code: '__invalid__', secret: config.sessionSecret, maxAttempts: 1 });
      writeAudit({
        userId: existing?.id ?? null,
        actor: 'system',
        action: 'auth.otp_delivery_failed',
        target: maskIdentifier(channel, target),
        detail: { channel, transport: outcome.transport, error: outcome.error ?? null },
        ip: req.ip,
      });
      res.status(502).json({
        error: 'delivery_failed',
        message: outcome.error ?? 'The message could not be delivered. Nothing was sent.',
      });
      return;
    }

    writeAudit({
      userId: existing?.id ?? null,
      actor: 'owner',
      action: 'auth.otp_requested',
      target: maskIdentifier(channel, target),
      detail: { channel, purpose, transport: outcome.transport },
      ip: req.ip,
    });

    const reveal = config.otp.revealDevCodes && outcome.transport === 'console';
    if (reveal) {
      logger.warn('development mode: one-time code revealed to the browser', { channel, purpose });
    }

    res.status(201).json({
      challengeId: challenge.id,
      channel,
      maskedTarget: maskIdentifier(channel, target),
      expiresInSeconds: config.otp.ttlSeconds,
      transport: outcome.transport,
      devCode: reveal ? challenge.code : undefined,
      message: reveal
        ? 'Development mode: nothing was actually sent. The code is shown below because MAIL_PROVIDER/SMS_PROVIDER are set to "console".'
        : 'If the details match an account, a one-time code is on its way.',
    });
  });

  /** Step 2: verify the code, then create the session (or the owner account). */
  router.post('/otp/verify', (req, res) => {
    const parsed = otpVerifySchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request', message: 'Enter the 6-digit code.' });
      return;
    }
    const result = verifyChallenge({
      challengeId: parsed.data.challengeId,
      code: parsed.data.code,
      secret: config.sessionSecret,
      maxAttempts: config.otp.maxAttempts,
    });

    if (!result.ok) {
      writeAudit({
        userId: null,
        actor: 'anonymous',
        action: 'auth.otp_failed',
        target: parsed.data.challengeId,
        detail: { reason: result.reason },
        ip: req.ip,
      });
      const status =
        result.reason === 'too_many_attempts'
          ? 429
          : result.reason === 'expired' || result.reason === 'already_used'
            ? 410
            : 400;
      res.status(status).json({
        error: result.reason,
        message:
          result.reason === 'invalid_code'
            ? `That code is not correct.${result.attemptsRemaining !== undefined ? ` ${result.attemptsRemaining} attempt(s) left.` : ''}`
            : result.reason === 'expired'
              ? 'That code has expired. Request a new one.'
              : result.reason === 'already_used'
                ? 'That code has already been used. Request a new one.'
                : result.reason === 'too_many_attempts'
                  ? 'Too many incorrect attempts. Request a new code.'
                  : 'That request is no longer valid. Start again.',
        attemptsRemaining: result.attemptsRemaining,
      });
      return;
    }

    const { challenge } = result;

    if (challenge.purpose === 'signup') {
      if (countUsers() > 0) {
        res.status(409).json({ error: 'already_configured', message: 'An owner already exists. Sign in instead.' });
        return;
      }
      const name = parsed.data.name?.trim();
      if (!name) {
        res.status(400).json({ error: 'name_required', message: 'Enter your name to finish creating the account.' });
        return;
      }
      const id = randomUUID();
      const placeholder = unusablePassword();
      createUser({
        id,
        email: challenge.channel === 'email' ? challenge.target : null,
        phone: challenge.channel === 'phone' ? challenge.target : null,
        name,
        passwordHash: placeholder.hash,
        passwordSalt: placeholder.salt,
        emailVerified: challenge.channel === 'email',
        phoneVerified: challenge.channel === 'phone',
        hasPassword: false,
      });
      startSession(res, id, req.get('user-agent'));
      writeAudit({
        userId: id,
        actor: 'owner',
        action: 'auth.owner_created',
        target: challenge.channel,
        detail: { method: 'otp', channel: challenge.channel },
        ip: req.ip,
      });
      res.status(201).json({
        user: findUserById(id),
        nextStep: 'set_password',
        message: 'Account created. Set a password so you can sign in without a code.',
      });
      return;
    }

    const user =
      challenge.user_id !== null
        ? findUserById(challenge.user_id)
        : findUserByIdentifier({
            email: challenge.channel === 'email' ? challenge.target : null,
            phone: challenge.channel === 'phone' ? challenge.target : null,
          });

    if (!user) {
      res.status(404).json({ error: 'no_account', message: 'That account no longer exists.' });
      return;
    }
    markIdentifierVerified(user.id, challenge.channel);
    startSession(res, user.id, req.get('user-agent'));
    writeAudit({
      userId: user.id,
      actor: 'owner',
      action: 'auth.login',
      target: challenge.channel,
      detail: { method: 'otp', channel: challenge.channel },
      ip: req.ip,
    });
    res.json({
      user: findUserById(user.id),
      nextStep: user.hasPassword ? null : 'set_password',
    });
  });

  router.post('/logout', (req, res) => {
    if (req.session) {
      destroySession(req.session.id);
      writeAudit({ userId: req.user?.id ?? null, actor: 'owner', action: 'auth.logout', ip: req.ip });
    }
    res.clearCookie(SESSION_COOKIE, { path: '/' });
    res.json({ ok: true });
  });

  router.get('/me', requireAuth, (req, res) => {
    res.json({ user: req.user });
  });

  /**
   * Sets or changes the password. Accounts created through a one-time code have
   * no usable password, so a valid session is enough to set one; accounts that
   * already have a password must present the current one.
   */
  router.post('/change-password', requireAuth, (req, res) => {
    const parsed = passwordChangeSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: 'invalid_request' });
      return;
    }
    const user = findUserSecretsById(req.user!.id);
    if (!user) {
      res.status(404).json({ error: 'not_found' });
      return;
    }
    if (user.hasPassword) {
      if (!parsed.data.currentPassword) {
        res.status(400).json({ error: 'current_password_required', message: 'Enter your current password.' });
        return;
      }
      if (!verifyPassword(parsed.data.currentPassword, user.password_hash, user.password_salt)) {
        res.status(401).json({ error: 'invalid_credentials', message: 'Current password is incorrect.' });
        return;
      }
    }
    const problems = passwordProblems(parsed.data.newPassword);
    if (problems.length > 0) {
      res.status(400).json({ error: 'weak_password', message: problems.join(' ') });
      return;
    }
    setPassword(user.id, parsed.data.newPassword);
    const signedOut = destroyOtherSessions(user.id, req.session?.id ?? null);
    writeAudit({
      userId: user.id,
      actor: 'owner',
      action: user.hasPassword ? 'auth.password_changed' : 'auth.password_set',
      ip: req.ip,
    });
    res.json({
      ok: true,
      message: user.hasPassword
        ? `Password changed. ${signedOut} other session(s) signed out.`
        : 'Password set. You can now sign in with it.',
      otherSessionsSignedOut: signedOut,
    });
  });

  return router;
}
