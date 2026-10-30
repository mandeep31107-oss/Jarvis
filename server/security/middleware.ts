import type { NextFunction, Request, Response } from 'express';
import rateLimit from 'express-rate-limit';

/**
 * Cross-site request forgery defence.
 *
 * Session cookies are SameSite=Strict, and every state-changing request must
 * also carry a custom `X-JARVIS-Request` header. Cross-origin scripts cannot
 * send custom headers without a CORS preflight, which this server never grants.
 */
export function csrfGuard(req: Request, res: Response, next: NextFunction): void {
  const method = req.method.toUpperCase();
  if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') {
    next();
    return;
  }
  if (req.get('x-jarvis-request') !== '1') {
    res.status(403).json({
      error: 'missing_request_header',
      message: 'State-changing requests must include the X-JARVIS-Request header.',
    });
    return;
  }
  next();
}

export function securityHeaders(isProduction: boolean) {
  return (_req: Request, res: Response, next: NextFunction): void => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('X-Permitted-Cross-Domain-Policies', 'none');
    res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
    res.setHeader('Permissions-Policy', 'microphone=(self), camera=(), geolocation=()');
    // NOTE: no X-Frame-Options DENY and no frame-ancestors lock-down, because
    // the owner may legitimately run this dashboard inside an embedded preview.
    // The session cookie is SameSite=Strict, so framing cannot ride a session.
    if (isProduction) {
      res.setHeader(
        'Content-Security-Policy',
        [
          "default-src 'self'",
          "script-src 'self'",
          "style-src 'self' 'unsafe-inline'",
          "img-src 'self' data:",
          "connect-src 'self'",
          "font-src 'self' data:",
          "object-src 'none'",
          "base-uri 'self'",
          "form-action 'self'",
        ].join('; '),
      );
    }
    next();
  };
}

export const apiLimiter = rateLimit({
  windowMs: 60_000,
  limit: 240,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  // Health checks and preview probes must never be throttled.
  skip: (req) => req.path === '/health',
});

export const authLimiter = rateLimit({
  windowMs: 15 * 60_000,
  limit: 20,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message: { error: 'too_many_attempts', message: 'Too many authentication attempts. Try again later.' },
});
