# Security model

This document states what is protected, how, and what is **not** protected yet. It is written
to be read before you expose the app to anyone but yourself.

## Threat model

Assets, in order of value:

1. **Provider API keys and the session secret** — direct financial and account risk.
2. **Owner data** — conversations, finance records, memory, compliance notes.
3. **Actions taken on the owner's behalf** — anything external or destructive.
4. **Integrity of the assistant's instructions** — prompt injection via retrieved content.

Assumed adversaries: hostile web pages fetched by the research tool, malicious text inside
pasted documents, someone on the same network as a carelessly exposed port, and a compromised
model provider.

## Controls implemented

### Authentication and sessions

- First-run owner creation only; once a user exists, `/api/auth/setup` returns 409.
- Passwords hashed with **scrypt** (64-byte key, per-user random salt), compared with
  `timingSafeEqual`. No plaintext password is ever stored or logged.
- Password policy: ≥10 characters, at least one letter and one digit.
- Session tokens are 32 random bytes, stored **hashed** (SHA-256) — a database leak does not
  yield usable cookies.
- Cookie: `HttpOnly`, `SameSite=Strict`, `Secure` in production, 7-day lifetime.
- Changing a password deletes every other session.
- Login failures return an identical response for unknown user and wrong password, and are
  audit-logged.

### Request integrity

- **CSRF**: `SameSite=Strict` plus a required `X-JARVIS-Request: 1` header on every
  state-changing request. Cross-origin scripts cannot add custom headers without a preflight,
  which this server never grants.
- Rate limits: 240 requests/minute per IP on `/api`, 20 attempts/15 minutes on `/api/auth`.
- Request bodies capped at 256 KB; all inputs validated with Zod before touching a repository.
- Security headers: `X-Content-Type-Options`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy`, a restrictive `Permissions-Policy`, and a strict
  `Content-Security-Policy` in production (`default-src 'self'`, no inline scripts).

### Least privilege

- Every side effect is a registered action with a schema, risk level and approval policy.
  There is no arbitrary code-execution endpoint.
- External actions always require explicit owner approval; rejected approvals execute nothing.
- Approval payloads are re-validated at execution time, so a tampered payload cannot slip
  through the queue.
- Action ids are UUIDs scoped by `user_id` in every query.

### Secrets

- Secrets live only in `.env` (git-ignored) and in the server process. The public settings
  endpoint exposes capability *names*, never keys — there is an automated check in the tests
  that the API never returns an `AI_API_KEY` style value.
- The logger redacts any field whose key looks like password/secret/token/key/session/cookie.
- Model output is scanned for credential patterns (`sk-…`, `AIza…`, `ghp_…`, `Bearer …`) and
  redacted before display.
- The browser bundle contains no secrets: it is built from `web/src` only.

### Untrusted content and prompt injection

- Fetched pages are converted to text and stored as data. When injected into a prompt they are
  wrapped in `<untrusted_content>` with an explicit rule that content is never an instruction.
- Research content cannot change permissions, the action registry, the autonomy level or the
  system prompt — those are server-owned code and environment, not conversation state.
- The model can only *propose* actions; the server validates the payload and applies policy.
- Generated answers that claim an action happened when it did not are corrected by the
  deterministic validator before the owner sees them.

### Research fetch hardening (SSRF)

- Only `http`/`https`, maximum 4 redirects (each hop re-validated).
- DNS is resolved and every returned address checked: loopback, private ranges, link-local
  (including `169.254.169.254` cloud metadata), CGNAT, unique-local IPv6 and `.local`/`.internal`
  hostnames are refused.
- Optional hostname allowlist via `RESEARCH_ALLOWED_HOSTS`.
- 512 KB response cap, 15 s timeout, text-only handling, scripts/styles stripped.

### Auditing and observability

- `audit_log` records authentication events, task/memory/finance/compliance changes, approval
  requests and decisions, executed/failed actions, and failed chat turns with the error.
- `usage_events` records tokens, latency and estimated cost per request.
- Logs are structured JSON on stdout/stderr with no request bodies.

## Known gaps — read these

| Gap | Risk | Mitigation now | Planned |
| --- | --- | --- | --- |
| No speaker verification | Any voice can trigger a transcript | Voice never authorizes anything; approvals are explicit clicks | Enrolment + thresholds as a *second* factor |
| No 2FA / passkeys | Password is a single factor | Strong hashing, rate limit, local-only deployment | WebAuthn second factor |
| No CSRF token rotation | Custom header + SameSite is sufficient for this threat model | Header + Strict cookies | Signed double-submit token when introducing cross-site clients |
| In-memory SSE bus | Events are not shared across instances | Single-process design | Redis pub/sub |
| No sandboxed code execution | Nothing runs models' code — deliberate | Not implemented rather than unsafe | Isolated worker with resource limits |
| No encryption at rest | Local file readable by the OS user | OS disk encryption | SQLCipher or Postgres TDE |
| No automated backup | Data loss on disk failure | Copy `data/` while stopped | Scheduled encrypted backup |
| Rate limits are per-IP | NAT/proxy shares a bucket | Acceptable for a single owner | Per-user buckets |
| Not legal/compliance certified | — | UI states "not legal advice" | Professional review |

## Operational guidance

- Run it on `localhost` or behind a reverse proxy with TLS. Do not expose port 5173 publicly.
- Set `NODE_ENV=production` and a real `SESSION_SECRET` for anything long-lived.
- Rotate the provider key periodically; if a key leaks, revoke it at the provider first — it is
  not stored anywhere else, so no local cleanup can undo the exposure.
- Keep `ALLOW_OUTBOUND_FETCH=false` unless you actively need retrieval.
- Back up `data/jarvis.sqlite` while the server is stopped, and treat that file as sensitive.
- Review the Activity/Audit trail after any unusual behaviour.

## Reporting a problem

If you find a vulnerability, do not open a public issue with reproduction details. Contact the
repository owner privately and include the affected endpoint, the impact and a minimal
reproduction.

## Honest statement

No software is perfectly secure, and this is a young codebase built by a small effort. The
controls above are implemented and tested where tests are indicated, but this document is not
a certification, and the application does not claim to be "fully compliant" with any
regulation.
