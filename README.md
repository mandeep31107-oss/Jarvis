# J.A.R.V.I.S. — personal AI operating system

A modular, permission-controlled assistant: one coordinator that routes work to specialist
agents, shows its real execution, records what it did, and asks before it touches anything
outside this application.

This repository contains **Milestone 1–3** of the ten-milestone plan: a working foundation,
real conversation (text + browser voice), task management, orchestrator runs with live
workflow events, an approval centre, memory, a research library, finance forecasting, a
compliance register and a full audit trail.

> **Honest status.** Nothing in this build is a mock-up, and nothing is over-claimed. Where a
> capability does not exist (messaging integrations, web search, code execution, deployment,
> speaker verification), the UI and API say so explicitly. See [docs/STATUS.md](docs/STATUS.md).

---

## Quick start (Windows PowerShell)

```powershell
git clone <your-repo-url> Jarvis
cd Jarvis
Copy-Item .env.example .env
node -e "console.log(require('crypto').randomBytes(48).toString('base64url'))"   # copy into SESSION_SECRET
npm install
npm run dev
```

Open <http://localhost:5173>. The first visit shows a one-time **owner enrolment** screen with
three real ways to sign in — and after enrolment the same three ways work from the sign-in tab:

| Method | Configuration needed |
| --- | --- |
| Password (email **or** mobile number) | none |
| Email 6-digit code | `MAIL_PROVIDER=smtp` + SMTP credentials (Gmail App Password works) |
| Mobile 6-digit code | `SMS_PROVIDER=twilio` + Twilio credentials |

With no credentials both code methods run in **development console mode**: the code is logged
and shown on screen, and the UI says plainly that nothing was sent. In `NODE_ENV=production`
they are reported as unavailable and the API returns `503 delivery_not_configured` — this build
never pretends a message went out. Configure real delivery in
[docs/SETUP-WINDOWS.md](docs/SETUP-WINDOWS.md) (steps 5a and 5b).

Google one-tap sign-in is **not** implemented: the button is disabled with an explanation
because it needs a Google Cloud OAuth client. There is no password-reset email yet either —
an email code (if configured) is the recovery path.

No API key is required to start. With the default `AI_PROVIDER=offline` the assistant replies
from labelled offline templates so every workflow can be exercised. To get real reasoning,
set a provider (see below) and restart.

Full Windows walkthrough: [docs/SETUP-WINDOWS.md](docs/SETUP-WINDOWS.md).

## Connecting a language model

Edit `.env` — never put a key in the frontend, a commit or a chat message.

```ini
# Any OpenAI-compatible endpoint:
AI_PROVIDER=openai
AI_BASE_URL=https://api.openai.com/v1        # or https://api.groq.com/openai/v1, OpenRouter, vLLM…
AI_MODEL=gpt-4o-mini
AI_API_KEY=sk-…

# or a local model on your own machine (no key, no per-token cost):
AI_PROVIDER=ollama
AI_BASE_URL=http://127.0.0.1:11434/v1
AI_MODEL=llama3.1:8b
AI_API_KEY=
```

Restart the server. The header pill changes from *model offline* to *model ready*, and
`GET /api/health` reports the provider state. There is no automatic fallback to fabricated
answers: if the provider fails, the turn fails visibly with a fix hint.

## Everyday commands

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server + Vite middleware on <http://localhost:5173> (binds `0.0.0.0`) |
| `npm test` | 110 automated tests (routing, finance maths, validation, security, auth/OTP, migrations, HTTP API, UI render) |
| `npm run typecheck` | Strict TypeScript check for server and web |
| `npm run build` | Production build (`web/dist` + `dist/server`) |
| `npm start` | Run the production build (requires `SESSION_SECRET`) |

## What works today

| Area | Status | Notes |
| --- | --- | --- |
| Sign-in: password, email code, mobile code | ✅ | scrypt passwords; HMAC-hashed single-use codes; real SMTP/Twilio delivery once configured |
| Sign-in: Google one-tap / passkeys | ⛔ planned | Button is disabled with an explanation; no OAuth client |
| Conversation (text + streaming) | ✅ | Provider-abstracted, SSE token streaming, 12-agent routing |
| Voice input / output / wake phrase | ✅ | Browser Web Speech API; Chrome or Edge; language, rate, pitch, barge-in |
| Speaker verification / enrolment | ⛔ planned | Voice is never treated as authorization |
| Tasks, priorities, dependencies, deadlines | ✅ | Dependency-aware completion, audit trail |
| Orchestrator runs + live workflow monitor | ✅ | Real stage events, real stage-based progress, cancel |
| Approval centre | ✅ | External actions always gated; payload visible before approval |
| Persistent memory | ✅ | Categories, owner-stated vs inferred labels, delete |
| Research library | ✅ (limited) | Stores real URLs and dates; **no search API**, fetch must be enabled |
| Finance records + cash-flow/runway forecast | ✅ | Deterministic maths with stated assumptions; can never move money |
| Compliance register | ✅ (limited) | Jurisdiction-first tracking; no automated monitoring |
| Audit log + usage/cost estimates | ✅ | Structured logs, redaction, per-request token estimates |
| WhatsApp / Telegram / Instagram / LinkedIn | ⛔ planned | Requires official API credentials; none connected |
| Code execution, file editing, deployment | ⛔ planned | Sandboxed execution is a later milestone |

Milestone-by-milestone detail, limitations and the next steps:
[docs/STATUS.md](docs/STATUS.md). Reproducible verification for every acceptance criterion:
[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md).

## How it is built

```
server/            Node + Express + TypeScript API (all secrets live here)
  config.ts        Zod-validated environment configuration
  security/        scrypt passwords, sessions, CSRF guard, headers, rate limits
  services/otp.ts  One-time codes: normalisation, HMAC hashing, limits, single use
  services/delivery.ts  SMTP (nodemailer) and Twilio senders with honest delivery states
  db/              SQLite schema + migrations + typed query helpers
  repo/            One module per entity — the only place SQL is written
  ai/              Provider abstraction, routing, prompt building, output validation
  agents/          The 12 specialist definitions (data, not classes)
  actions/         The only side effects the assistant can request, with risk policy
  tools/           Finance maths (pure) and SSRF-guarded research fetch
  services/        Runs/events, approvals, audit, usage
  routes/          HTTP API
  app.ts           Middleware + router assembly, frontend mounting
web/               React + TypeScript dashboard (Vite), no CSS framework
  src/views/       One file per screen
  src/lib/         API client, SSE parser, voice hook, hooks/helpers
  src/components/  Design system (panels, pills, toasts, safe markdown renderer)
tests/             Vitest: unit, HTTP integration, and view render tests
docs/              Setup, architecture, security, status, acceptance procedures
```

Key design rules:

- **Every side effect is a named action** with a Zod schema, a risk level, an `external`
  flag and an approval policy. The conversation layer cannot call anything else.
- **The model proposes, the server decides.** Action proposals come from deterministic
  phrasing detection, so an unpredictable model cannot quietly create records.
- **Retrieved web content is data, never instructions.** It is wrapped in
  `<untrusted_content>` and never re-enters the permission system.
- **No invented progress.** Stage progress is completed events divided by total stages.
- **One language, one toolchain.** TypeScript on both sides, SQLite via Node's built-in
  driver, no native compilation — the same commands work on Windows, macOS and Linux.

Deeper explanation of components, request lifecycle, data model and extension points:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Security model and known gaps:
[docs/SECURITY.md](docs/SECURITY.md).

## Requirements

- Node.js **22.12 or newer** (uses the built-in `node:sqlite` module)
- A modern browser for the dashboard; Chrome or Edge for voice features
- No Docker, no external database, no build tooling beyond `npm install`

## Data and privacy

Everything you enter lives in `data/jarvis.sqlite` (git-ignored). Conversation text and
relevant memories are sent to your configured model provider — that is unavoidable if you
use a hosted model. Use `AI_PROVIDER=ollama` to keep everything on your machine.

## Licence

Private project — no licence granted. See the repository owner before redistributing.
