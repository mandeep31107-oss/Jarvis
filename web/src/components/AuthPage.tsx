import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from '../lib/api';
import type { AuthMethods, OtpChallenge } from '../lib/types';
import { Notice, Pill } from './ui';

type Tab = 'login' | 'signup';
type Method = 'password' | 'email' | 'phone';
type Step = 'identify' | 'code' | 'set-password';

/**
 * Start screen with sign-in and sign-up.
 *
 * Three real methods:
 *   password  — email + password (the original path)
 *   email OTP — works with any mailbox including Gmail
 *   phone OTP — SMS via Twilio, or the development console
 *
 * Google's one-tap OAuth is intentionally not wired: it needs a Google Cloud
 * OAuth client and is shown as unavailable rather than faked.
 */
export function AuthPage({
  initialTab,
  allowSignup,
  methods,
  policy,
  onSuccess,
}: {
  initialTab: Tab;
  allowSignup: boolean;
  methods: AuthMethods;
  policy?: { ttlSeconds: number; maxAttempts: number };
  onSuccess: () => void;
}) {
  const ttlSeconds = policy?.ttlSeconds ?? 300;
  const maxAttempts = policy?.maxAttempts ?? 5;
  const [tab, setTab] = useState<Tab>(allowSignup ? initialTab : 'login');
  // On a fresh install the first tab is owner creation; lead with the email code
  // when this server can actually deliver one.
  const [method, setMethod] = useState<Method>(
    initialTab === 'signup' && methods.emailOtp ? 'email' : 'password',
  );
  const [step, setStep] = useState<Step>('identify');

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');

  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [code, setCode] = useState('');
  const [cooldown, setCooldown] = useState(0);

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Browsers block first-party cookies inside third-party frames, which breaks
  // sessions in an embedded preview. Better to say so than to fail mysteriously.
  const [embedded, setEmbedded] = useState(false);
  useEffect(() => {
    try {
      setEmbedded(window.self !== window.top);
    } catch {
      setEmbedded(true);
    }
  }, []);

  const isSignup = tab === 'signup';
  const identifier = method === 'phone' ? phone.trim() : email.trim();

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = window.setTimeout(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(id);
  }, [cooldown]);

  const fail = (err: unknown, fallback: string) => {
    setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback);
  };

  const resetToIdentify = () => {
    setStep('identify');
    setChallenge(null);
    setCode('');
    setError(null);
  };

  const switchTab = (next: Tab) => {
    setTab(next);
    setStep('identify');
    setChallenge(null);
    setCode('');
    setError(null);
    setNotice(null);
    setMethod(next === 'signup' ? (methods.emailOtp ? 'email' : 'password') : 'password');
  };

  const requestCode = useCallback(
    async (purpose: 'signup' | 'login') => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const result = await api.post<OtpChallenge>('/auth/otp/request', {
          channel: method === 'phone' ? 'phone' : 'email',
          target: method === 'phone' ? phone.trim() : email.trim(),
          purpose,
        });
        setChallenge(result);
        setStep('code');
        setCode('');
        setCooldown(30);
        if (!result.devCode) setNotice(result.message);
      } catch (err) {
        fail(err, 'Could not request a code.');
      } finally {
        setBusy(false);
      }
    },
    [email, method, phone],
  );

  const verifyCode = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ nextStep: string | null }>('/auth/otp/verify', {
        challengeId: challenge.challengeId,
        code: code.trim(),
        ...(isSignup ? { name: name.trim() } : {}),
      });
      if (result.nextStep === 'set_password') {
        setStep('set-password');
        setNotice('Account ready. Set a password so you can sign in without a code next time.');
      } else {
        onSuccess();
      }
    } catch (err) {
      fail(err, 'That code could not be verified.');
      setCode('');
    } finally {
      setBusy(false);
    }
  };

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (isSignup) {
        await api.post('/auth/setup', { email: email.trim(), name: name.trim(), password });
      } else {
        await api.post('/auth/login', { identifier: email.trim(), password });
      }
      onSuccess();
    } catch (err) {
      fail(err, isSignup ? 'Could not create the account.' : 'Could not sign in.');
    } finally {
      setBusy(false);
    }
  };

  const savePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setError('The two passwords do not match.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.post('/auth/change-password', { newPassword });
      onSuccess();
    } catch (err) {
      fail(err, 'Could not set the password.');
    } finally {
      setBusy(false);
    }
  };

  const submitIdentify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (method === 'password') {
      await submitPassword(event);
      return;
    }
    await requestCode(isSignup ? 'signup' : 'login');
  };

  const methodUnavailable =
    (method === 'email' && !methods.emailOtp) || (method === 'phone' && !methods.phoneOtp);

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="auth__mark">
          <div className="brand__mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="3.2" />
              <circle cx="12" cy="12" r="8" opacity="0.45" />
              <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" opacity="0.7" />
            </svg>
          </div>
          <div>
            <div className="auth__title">J.A.R.V.I.S.</div>
            <div className="auth__sub">
              {isSignup ? 'Create the owner account' : 'Sign in to your operating system'}
            </div>
          </div>
        </div>

        <div className={`auth-tabs ${allowSignup ? '' : 'auth-tabs--single'}`} role="tablist" aria-label="Sign in or sign up">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'login'}
            className={`auth-tab ${tab === 'login' ? 'auth-tab--active' : ''}`}
            onClick={() => switchTab('login')}
          >
            Sign in
          </button>
          {allowSignup && (
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'signup'}
              className={`auth-tab ${tab === 'signup' ? 'auth-tab--active' : ''}`}
              onClick={() => switchTab('signup')}
            >
              Create owner
            </button>
          )}
        </div>

        {!isSignup && allowSignup && (
          <Notice tone="cyan" title="No account yet">
            This is a fresh installation. Switch to <strong>Create owner</strong> to enrol yourself.
          </Notice>
        )}

        {error && (
          <Notice tone="red" title="Could not continue" onDismiss={() => setError(null)}>
            {error}
          </Notice>
        )}
        {notice && !error && (
          <Notice tone="cyan" onDismiss={() => setNotice(null)}>
            {notice}
          </Notice>
        )}

        {step === 'identify' && (
          <>
            <div className="auth-methods" role="group" aria-label="Sign-in method">
              <button
                type="button"
                className={`auth-method ${method === 'password' ? 'auth-method--active' : ''}`}
                onClick={() => setMethod('password')}
                aria-pressed={method === 'password'}
              >
                Password
              </button>
              <button
                type="button"
                className={`auth-method ${method === 'email' ? 'auth-method--active' : ''} ${methods.emailOtp ? '' : 'auth-method--off'}`}
                onClick={() => setMethod('email')}
                aria-pressed={method === 'email'}
                title={methods.emailOtp ? 'A 6-digit code is emailed to you' : 'Email delivery is not configured'}
              >
                Email code
                {!methods.emailOtp && <span className="auth-method__flag">off</span>}
              </button>
              <button
                type="button"
                className={`auth-method ${method === 'phone' ? 'auth-method--active' : ''} ${methods.phoneOtp ? '' : 'auth-method--off'}`}
                onClick={() => setMethod('phone')}
                aria-pressed={method === 'phone'}
                title={methods.phoneOtp ? 'A 6-digit code is sent by SMS' : 'SMS delivery is not configured'}
              >
                Mobile code
                {!methods.phoneOtp && <span className="auth-method__flag">off</span>}
              </button>
            </div>

            {methodUnavailable && (
              <Notice tone="amber" title="This method is unavailable on this server">
                {method === 'email'
                  ? 'Email codes need MAIL_PROVIDER=smtp plus SMTP credentials (for Gmail: an app password).'
                  : 'Mobile codes need SMS_PROVIDER=twilio plus credentials. Nothing was sent.'}
              </Notice>
            )}

            <form className="auth__form" onSubmit={submitIdentify}>
              {isSignup && (
                <label className="field">
                  <span className="field__label">Your name</span>
                  <input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="name" maxLength={120} />
                </label>
              )}

              {method === 'phone' ? (
                <label className="field">
                  <span className="field__label">Mobile number</span>
                  <input
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    required
                    autoComplete="tel"
                    inputMode="tel"
                    placeholder="98765 43210"
                  />
                  <span className="field__hint">Indian numbers can be entered without +91.</span>
                </label>
              ) : (
                <label className="field">
                  <span className="field__label">{method === 'password' && !isSignup ? 'Email or mobile number' : 'Email'}</span>
                  <input
                    type={method === 'password' && !isSignup ? 'text' : 'email'}
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoComplete="username"
                    placeholder="you@gmail.com"
                  />
                  {method === 'password' && !isSignup && (
                    <span className="field__hint">Either works if it is linked to your account.</span>
                  )}
                </label>
              )}

              {method === 'password' && (
                <label className="field">
                  <span className="field__label">Password</span>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    minLength={isSignup ? 10 : 1}
                    autoComplete={isSignup ? 'new-password' : 'current-password'}
                  />
                  {isSignup && (
                    <span className="field__hint">At least 10 characters, with a letter and a number.</span>
                  )}
                </label>
              )}

              <button
                className="btn btn--primary btn--md"
                type="submit"
                disabled={busy || methodUnavailable || (isSignup && !allowSignup)}
              >
                {busy
                  ? 'Please wait…'
                  : method === 'password'
                    ? isSignup
                      ? 'Create account'
                      : 'Sign in'
                    : 'Send code'}
              </button>
            </form>

            {method !== 'password' && (
              <p className="auth__foot">
                {isSignup ? 'A code will be sent' : 'A code is sent'} to{' '}
                {method === 'phone' ? 'your mobile' : 'your inbox'} — Gmail, Outlook or any other
                provider. It expires in {Math.round(ttlSeconds / 60)} minutes and can be used once.
                {methods.emailOtpMode === 'console' || methods.phoneOtpMode === 'console' ? (
                  <>
                    {' '}
                    <strong>Development mode:</strong> no real message is sent — the code appears on the
                    next screen and in the server log.
                  </>
                ) : null}
              </p>
            )}

            <div className="auth-google">
              <button type="button" className="btn btn--subtle btn--md" disabled title="Requires Google OAuth credentials">
                <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true">
                  <path fill="#ea4335" d="M12 4.5c1.8 0 3.4.6 4.6 1.8l3.4-3.4C18 1 15.3 0 12 0 7.3 0 3.3 2.7 1.4 6.6l3.9 3C6.2 6.7 8.9 4.5 12 4.5Z" opacity=".9" />
                  <path fill="#4285f4" d="M23.5 12.3c0-.8-.1-1.5-.2-2.3H12v4.5h6.4c-.3 1.5-1.1 2.7-2.4 3.6l3.7 2.9c2.2-2 3.8-5 3.8-8.7Z" opacity=".9" />
                  <path fill="#34a853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.7-2.9c-1.1.7-2.5 1.2-4.2 1.2-3.1 0-5.8-2.2-6.7-5.1l-3.9 3C3.3 21.3 7.3 24 12 24Z" opacity=".9" />
                  <path fill="#fbbc05" d="M5.3 14.3a7.4 7.4 0 0 1 0-4.6l-3.9-3a12 12 0 0 0 0 10.6l3.9-3Z" opacity=".9" />
                </svg>
                Continue with Google
              </button>
              <span className="tiny muted-2">
                Not configured. One-tap Google sign-in needs a Google Cloud OAuth client
                (<code>GOOGLE_CLIENT_ID</code>) and is planned — use an email code for a Gmail address
                instead.
              </span>
            </div>
          </>
        )}

        {step === 'code' && challenge && (
          <form className="auth__form" onSubmit={verifyCode}>
            <div className="row row--between">
              <div>
                <div className="field__label">Code sent to</div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 13.5 }}>{challenge.maskedTarget}</div>
              </div>
              <Pill tone={challenge.transport === 'console' ? 'amber' : 'green'}>
                {challenge.transport === 'console' ? 'dev console' : challenge.transport}
              </Pill>
            </div>

            {challenge.devCode && (
              <Notice tone="amber" title="Development mode — nothing was sent">
                Your code is <strong style={{ letterSpacing: '0.18em', fontFamily: 'var(--mono)' }}>{challenge.devCode}</strong>.
                In production, <code>MAIL_PROVIDER=smtp</code> or <code>SMS_PROVIDER=twilio</code> sends it
                for real and it is never shown here.
              </Notice>
            )}

            <label className="field">
              <span className="field__label">6-digit code</span>
              <input
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                required
                minLength={6}
                placeholder="••••••"
                style={{ letterSpacing: '0.35em', fontFamily: 'var(--mono)', fontSize: 17 }}
              />
              <span className="field__hint">
                {challenge.expiresInSeconds} seconds to use it. The code is void after {maxAttempts}{' '}
                wrong attempts.
              </span>
            </label>

            <button className="btn btn--primary btn--md" type="submit" disabled={busy || code.length !== 6}>
              {busy ? 'Verifying…' : isSignup ? 'Verify and create account' : 'Verify and sign in'}
            </button>

            <div className="row" style={{ justifyContent: 'space-between' }}>
              <button type="button" className="btn btn--subtle btn--sm" onClick={resetToIdentify}>
                Change {method === 'phone' ? 'number' : 'email'}
              </button>
              <button
                type="button"
                className="btn btn--subtle btn--sm"
                disabled={busy || cooldown > 0}
                onClick={() => void requestCode(isSignup ? 'signup' : 'login')}
              >
                {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend code'}
              </button>
            </div>
          </form>
        )}

        {step === 'set-password' && (
          <form className="auth__form" onSubmit={savePassword}>
            <Notice tone="green" title="You are signed in">
              Set a password now, or skip and use a code next time.
            </Notice>
            <label className="field">
              <span className="field__label">New password</span>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                minLength={10}
                required
              />
              <span className="field__hint">At least 10 characters, with a letter and a number.</span>
            </label>
            <label className="field">
              <span className="field__label">Confirm password</span>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="new-password"
                minLength={10}
                required
              />
            </label>
            <button className="btn btn--primary btn--md" type="submit" disabled={busy}>
              {busy ? 'Saving…' : 'Save password and continue'}
            </button>
            <button type="button" className="btn btn--subtle btn--sm" onClick={onSuccess}>
              Skip for now
            </button>
          </form>
        )}

        {embedded && (
          <Notice tone="amber" title="Embedded preview">
            Browsers block the session cookie inside a third-party frame, so signing in here may
            loop back to this screen. Open the preview in its own tab (or run it locally) and it
            will work.
          </Notice>
        )}

        <p className="auth__foot">
          Passwords are hashed with scrypt. Codes are stored only as an HMAC, expire in{' '}
          {Math.round(ttlSeconds / 60)} minutes and die after {maxAttempts} wrong attempts. Session
          cookies are HTTP-only and SameSite=Strict.
        </p>
      </div>
    </div>
  );
}
