# Architecture

A single Node process serves both the API and the dashboard. One SQLite file holds all state.
No queue broker, no external service, no native modules — the whole system can be understood,
backed up and moved in an afternoon.

```
Browser (React SPA)
   │  HTTPS / fetch + SSE
   ▼
Express app ── security middleware ── routes ── services ── repos ── SQLite
   │                                  │
   │                                  ├── AI provider (offline | OpenAI-compatible | Ollama)
   │                                  ├── agent registry (routing + personas)
   │                                  └── action registry (the only side effects)
   └── Vite middleware (dev) / static build (prod)
```

## Folder map

| Path | Responsibility |
| --- | --- |
| `server/index.ts` | Boot: config → database → provider → HTTP server → graceful shutdown |
| `server/app.ts` | Middleware order, API mounting, frontend mounting, error handler |
| `server/config.ts` | Zod-validated env; refuses insecure production boot |
| `server/logger.ts` | JSON logging with credential redaction |
| `server/db/index.ts` | `node:sqlite` driver, migrations, typed `all/get/run/tx` helpers |
| `server/repo/*.ts` | One module per entity. The only place SQL lives |
| `server/security/auth.ts` | scrypt hashing, session create/read/destroy, auth middleware |
| `server/security/middleware.ts` | Security headers, CSRF guard, rate limiters |
| `server/ai/providers/*` | Provider abstraction (`complete`, optional `completeStream`) |
| `server/ai/routing.ts` | Language detection, deterministic intent router, intent labels |
| `server/ai/orchestrator.ts` | Context assembly, prompt building, action proposals, generation |
| `server/ai/validation.ts` | Fabricated-action detection, credential redaction, source warnings |
| `server/agents/registry.ts` | The 12 specialist definitions |
| `server/actions/index.ts` | Action schemas, risk, approval policy, executors |
| `server/tools/finance.ts` | Pure finance maths (runway, forecast, summarise) |
| `server/tools/research.ts` | SSRF-guarded fetch, HTML→text, size/time limits |
| `server/services/*` | Runs + events, approvals, audit, usage |
| `server/routes/*` | HTTP surface (thin: validation → service → JSON) |
| `web/src/App.tsx` | Auth gate + dashboard shell + settings state |
| `web/src/views/*` | One screen per file |
| `web/src/lib/voice.ts` | SpeechRecognition/SpeechSynthesis hook with wake phrase and barge-in |
| `web/src/lib/api.ts` | Typed fetch client + SSE parsers |
| `web/src/components/*` | Design system, including a safe (non-HTML) markdown renderer |

## Request lifecycle of one chat turn

1. `POST /api/chat/message` (JSON, requires session + `X-JARVIS-Request`).
2. The route stores the user message and loads the last 12 messages for context.
3. `prepareTurn()`:
   - detects language (Devanagari / Hinglish markers / English),
   - routes to a specialist with the deterministic keyword router (returns confidence and the
     matched keywords, which are shown in the run),
   - retrieves memories by term overlap and snapshots open tasks,
   - builds the system prompt: identity, non-negotiable rules, language, autonomy, agent
     persona, honest capability list, owner context,
   - derives action proposals from imperative phrasing only.
4. A `runs` row is created and stage events are written as work actually happens:
   `received → context → routed → generating → validating → finalized`.
5. The provider is called. If it supports streaming, tokens are forwarded as SSE `delta`
   events; otherwise the full answer arrives as one delta.
6. `validateCompletion()` scans for action claims that did not happen and for credential-like
   strings, appending a correction or redaction.
7. Each proposal goes through `proposeAction()`: schema validation → approval policy →
   queue for the owner or execute immediately → audit entry → run event.
8. The assistant message is stored with metadata (agent, confidence, warnings, actions,
   provider, latency, tokens, estimated cost), the run is finalised, and a `done` event
   carries the authoritative final text.

The SSE stream is the transport, but nothing about correctness depends on it: every stage is
also persisted, and `/api/runs/:id` reconstructs the same timeline later.

## Data model

| Table | Purpose |
| --- | --- |
| `users` | Owner account (scrypt hash + salt) |
| `sessions` | Hashed session tokens, expiry, last seen |
| `settings` | Per-owner voice/autonomy preferences |
| `conversations`, `messages` | Threads and turns, with per-message metadata JSON |
| `tasks`, `task_dependencies` | Work items and blockers |
| `runs`, `run_events` | Execution records and their real event stream |
| `approvals` | Proposed actions, their decisions, results and errors |
| `memory_entries` | Durable context, category + source type + confidence |
| `audit_log` | Who did what, when, from where |
| `usage_events` | Provider, model, tokens, latency, estimated cost |
| `research_notes` | Question, jurisdiction, verbatim source excerpts |
| `finance_entries` | Income/expense records in minor units |
| `compliance_items` | Jurisdiction, area, publication and effective dates |

Migrations are additive and tracked in `schema_migrations` plus `PRAGMA user_version`.

## Permissions model

Two axes decide whether an action runs:

- **Autonomy level** (1 suggest / 2 approved execution / 3 bounded automation), stored per
  owner with an environment default.
- **Action risk** (`low`, `medium`, `high`) and **externality** (`external`).

| Situation | Result |
| --- | --- |
| External action (network fetch, future messaging) | Always queued for approval |
| High-risk action | Always queued for approval |
| Level 1 (suggest only) | Every change queued |
| Level 2+, low risk, internal | Executed immediately, still recorded in approvals + audit |
| Level 3 | Same as level 2 today; no scheduler exists yet |

Actions are the *only* reachable side effects. There is no generic "run code" or "call
arbitrary tool" path, which is what keeps the permission story auditable.

## Adding a specialist agent

1. Append an entry to `AGENTS` in `server/agents/registry.ts` (id, name, role, mission,
   tools, permissions, output format, status, honest status note, system prompt, keywords,
   accent).
2. If it needs a new side effect, add an action in `server/actions/index.ts` and include the
   agent id in that action's `agentIds`.

No other file needs to change: routing, prompt building, the agents screen and the approval
policy all read from the registry.

## Adding an AI provider

Implement `AiProvider` (`id`, `model`, `isConfigured`, `describe`, `complete`, optional
`completeStream`) in `server/ai/providers/` and register it in `createProvider()`. The
orchestrator only depends on the interface.

## Frontend decisions

- **No CSS framework.** A ~900-line design system gives exact control over the
  navy/holographic look, keeps the bundle at ~70 KB gzipped, and avoids a build-time
  dependency that a beginner would have to learn.
- **No router library.** Hash navigation is a dozen lines and survives reloads.
- **Model output is rendered as React nodes**, never `dangerouslySetInnerHTML`, so a
  malicious completion cannot inject markup or script.
- **Views render honest empty states** rather than sample data, and every "planned" chip is
  driven by the server's capability report.

## Deliberate trade-offs

| Decision | Why | Cost |
| --- | --- | --- |
| SQLite over PostgreSQL | Zero setup on Windows, single-file backup | Single-writer; move to Postgres when multi-user |
| Keyword routing over LLM classification | Free, deterministic, inspectable, works offline | Misses unusual phrasings; router is a small file to extend |
| Term-overlap memory retrieval | No embedding cost | Weaker recall than vector search |
| Runs execute inside the HTTP request | Simple, no job queue needed for chat | A turn cannot outlive the tab; long background jobs need a worker |
| In-process event bus for SSE | No Redis in the MVP | Move to Redis pub/sub when running multiple instances |
| Node's `node:sqlite` (experimental) | No native compilation on Windows | API may change; the driver is isolated in `server/db/index.ts` |
