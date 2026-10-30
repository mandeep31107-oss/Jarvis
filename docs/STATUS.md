# Build status

Last verified: **30 October 2026** · version `0.1.0` · branch `arena/5d46126e-jarvis`

This file is the single source of truth for what is real. If the UI and this document ever
disagree, treat this document as correct and open an issue.

## Milestones

| # | Milestone | State | What exists | What is missing |
| --- | --- | --- | --- | --- |
| 1 | Foundation | ✅ done | Repo structure, responsive dashboard, validated config, SQLite schema + migrations, structured logging, health check, 110 tests | CI pipeline, Docker image |
| 2 | Conversation | ✅ done | Provider abstraction (offline / any OpenAI-compatible / Ollama), SSE token streaming, persistent threads, browser voice in + out, EN/HI/Hinglish detection, barge-in interruption | Speaker verification / enrolment; local Whisper STT; server-side TTS |
| 3 | Task management | ✅ done¹ | Tasks with priority, due dates, dependencies, statuses; approval centre; audit trail; live run events with real stage progress | Background worker/queue, reminders, calendar sync, push notifications |
| 4 | Research | 🟡 partial | SSRF-guarded URL fetch, verbatim source storage with dates, research library UI, approval gate | **No search API**, no automatic discovery, no version comparison or diffing |
| 5 | Coding & websites | ⛔ not started | Design/architecture guidance from the Coding and Website agents | Sandboxed execution, file editing, website preview, deployment |
| 6 | Specialist agents | 🟡 partial | All 12 agents defined with tools, permissions, status and honest notes; deterministic routing; QA validation; workflow dashboard | Multi-agent chaining (one specialist per turn today), agent-to-agent review |
| 7 | Communication integrations | ⛔ not started | Integration requirements documented; draft-only social agent marked planned | WhatsApp/Telegram/Instagram/LinkedIn credentials, unified inbox, send/delivery states |
| 8 | Compliance monitoring | 🟡 partial | Jurisdiction-first register with publication vs effective dates and status workflow | Automated monitoring, change detection, alerts, remediation checklists |
| 9 | Controlled autonomy | 🟡 partial | Levels 1 and 2 enforced end-to-end; global approval policy; per-action risk | Level 3 scheduler, recurring jobs, rate/resource budgets, emergency stop switch |
| 10 | Production readiness | 🟡 partial | Strict typecheck, 110 tests, production build with CSP, graceful shutdown, documented security model | CI, backup automation, uptime monitoring, mobile device testing, load testing |

¹ Runs execute inside the HTTP request. There is no separate job queue, so "background"
execution means "while the browser stream is open".

## Verified facts about this build

- `npm test` → **110 passing tests** across routing, finance maths, output validation, password
  handling, HTML sanitising, the full HTTP API (auth, CSRF, tasks, chat streaming, approvals,
  autonomy policy, research failure, finance, compliance, secret exposure) and render tests
  for all 11 views, plus sign-in/sign-up (email and mobile one-time codes, delivery honesty,
  rate limits) and the v1→v2 schema migration.
- `npm run typecheck` → clean, strict mode, server and web.
- `npm run build` → `web/dist` (~70 KB JS gzipped) + `dist/server`; production server serves
  the SPA with a strict CSP and reports `status: ok` from `/api/health`.
- Conversation, tasks, approvals, audit, memory, research notes, finance and compliance
  endpoints were exercised end-to-end against the running server.

## Sign-in and sign-up

| Method | State | Notes |
| --- | --- | --- |
| Password with email | ✅ | scrypt, 10+ characters, letter + digit |
| Password with mobile number | ✅ | The login field accepts either identifier |
| Email one-time code (sign-up and sign-in) | ✅ | Real SMTP delivery once `MAIL_PROVIDER=smtp`; Gmail needs an App Password |
| Mobile one-time code (sign-up and sign-in) | ✅ | Real SMS once `SMS_PROVIDER=twilio` is configured |
| Development console transport | ✅ | Code logged and shown on screen, clearly labelled; nothing is sent |
| Production without credentials | ✅ honest | Methods reported unavailable; API returns 503 instead of pretending |
| Google one-tap | ⛔ | Disabled button with the reason (`GOOGLE_CLIENT_ID` required) |
| Link a phone to an existing account | ⛔ | Not built; accounts created by mobile can sign in by mobile |
| Password reset by email | ⛔ | Not built; use a code or set the password from Settings while signed in |

## Known limitations

| Limitation | Impact | Workaround |
| --- | --- | --- |
| Offline provider returns templates, not reasoning | No real intelligence without a provider | Set `AI_PROVIDER` + key, or use Ollama |
| No web search API | Cannot discover sources; only fetches URLs you provide | Paste links in chat and approve the fetch |
| Routing is keyword-based | Unusual phrasing falls back to the coordinator | Say the domain explicitly ("legal", "budget", "tasks") |
| One specialist per turn | No automatic multi-agent debate | Ask a follow-up |
| Memory retrieval is term overlap, not embeddings | May miss semantically related facts | Use clear keywords in memory titles |
| SQLite single-writer | Concurrent heavy writes queue | Fine for one owner |
| No calendar integration | Deadlines are internal only | Export in a later milestone |
| Voice is browser-dependent | Chrome/Edge required; wake phrase is keyword spotting | Type instead; approvals are always explicit |
| Runs cannot survive a page reload | Long turns are lost if the tab closes | Re-ask; the partial run is marked cancelled |
| No backup automation | Disk failure loses data | Copy `data/` while stopped |
| Sign-in codes are only as private as the mailbox/SIM | Anyone with the inbox or SIM can sign in | Keep 2-Step Verification on the mailbox; review the audit trail |
| Gmail requires an App Password | Your normal Google password will not work over SMTP | Create one at myaccount.google.com/apppasswords |
| Twilio charges per SMS and may need DLT registration in India | Mobile codes cost money | Email codes are free within Gmail's sending limits |

## Security considerations in this build

Implemented: scrypt password hashing, hashed session tokens, HttpOnly + SameSite=Strict
cookies, CSRF header requirement, rate limits, strict security headers with production CSP,
Zod validation on every input, UUID-scoped queries, credential redaction in logs and in model
output, SSRF protection for research fetches, untrusted-content isolation, approval gating for
external actions, and an audit trail for authentication, data changes, approvals and failures.

Not implemented: speaker verification, 2FA/WebAuthn, encryption at rest, automated backups,
multi-instance event distribution, sandboxed code execution.

Full detail: [SECURITY.md](SECURITY.md).

## Estimated running cost

Prices change and must be verified with each provider — these are planning figures, not quotes.

| Mode | Cost | Notes |
| --- | --- | --- |
| Offline templates | ₹0 | No network calls at all |
| Local model (Ollama) | ₹0 + electricity | Needs ~8 GB RAM for a 7–8B model at usable speed |
| Hosted small model | ≈ $0.0002–0.002 per turn | A 700-token prompt + 250-token answer at typical small-model prices; 100 turns/day ≈ $1–6/month |
| Hosting | ₹0 locally; ~$5/month for a small VPS | Not required for personal use on your own PC |

Token counts and estimated cost are recorded per request (`/api/system/usage`), so real spend
is measurable rather than guessed.

## Next milestone (recommended order)

### M4a — Make research genuinely useful (no external search key needed)
- Allowlist-based source subscriptions: you register official pages (regulator, platform
  policy, docs changelog); a scheduled job fetches, hashes and diffs them.
- Store versions, show what changed, raise an in-app alert and a proposed checklist.
- *Prerequisites:* `ALLOW_OUTBOUND_FETCH=true`, a list of 3–10 URLs you actually trust,
  a decision on whether scheduled fetching is acceptable.

### M5a — Sandboxed coding workspace
- Project registry, read-only file browser, diff preview, test runner in a container or
  restricted child process with CPU/memory/time limits.
- *Prerequisites:* Docker (or a decision to run tests in a temp directory), repository path
  permissions, explicit approval flow for writes.

### M7a — One messaging integration, done properly
- Telegram first (simplest official bot API), draft → approve → send, delivery status, failure
  handling, unified inbox rows.
- *Prerequisites:* a bot token from @BotFather, a decision on which chat id is the owner,
  and a written rule for what may auto-send.

### M10a — Operability
- CI (typecheck + test + build on push), encrypted backup script, `/api/health` uptime
  monitoring, structured error alerting, mobile device pass.

## Questions worth answering before the next milestone

1. **Hardware and OS** — which Windows machine, how much RAM, and is Ollama acceptable there?
2. **Budget** — is a hosted model at a few dollars a month acceptable, or should everything stay
   local/free?
3. **Primary use** — startup building, personal productivity, or client work? That decides
   whether research or messaging comes next.
4. **Jurisdiction to start with** — India is assumed (`IN`); US or others can be added, but the
   register starts empty on purpose.
