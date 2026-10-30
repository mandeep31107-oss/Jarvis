# Acceptance criteria and verification

Every criterion from the project brief, mapped to what this build actually does, with a
procedure you can repeat yourself. **66 automated tests** back the marked items
(`npm test`); the rest are manual checks.

Legend: ✅ met · 🟡 partial (honest scope) · ⛔ not met in this build

| # | Criterion | State |
| --- | --- | --- |
| 1 | Communicate by text and supported voice modes | ✅ |
| 2 | Understand and respond in English, Hindi, Hinglish | ✅ (model-dependent quality) |
| 3 | Create, prioritize and track tasks | ✅ |
| 4 | Show a real workflow and agent status | ✅ |
| 5 | Research current information with source links | 🟡 |
| 6 | Maintain project context across sessions | 🟡 |
| 7 | Generate and test a small website project | ⛔ |
| 8 | Prepare a practical startup plan | 🟡 |
| 9 | Calculate a budget and cash-flow forecast | ✅ |
| 10 | Identify jurisdiction before compliance research | ✅ |
| 11 | Request approval before protected external actions | ✅ |
| 12 | Distinguish draft, attempted and confirmed result | ✅ |
| 13 | Handle unavailable providers and failed integrations gracefully | ✅ |
| 14 | Keep credentials out of frontend code and logs | ✅ |
| 15 | Stop an authorized background task where supported | 🟡 |
| 16 | Clearly report incomplete or unsupported features | ✅ |

---

## 1. Text and voice modes — ✅

**Automated:** `tests/api.test.ts` › *streams a turn, records a run…* (SSE transport);
`tests/web.test.tsx` › *renders the conversation view*.

**Manual**
1. `npm run dev`, open <http://localhost:5173>, enrol, go to **Conversation**.
2. Type "hello" → a reply streams in and the *Active agent* panel fills.
3. Click the microphone orb, allow the permission, say "what should I focus on today".
4. With **Voice on**, send a message → it is spoken. Press **Interrupt speech** while it talks.
5. In Settings → *Voice & privacy*, set mode to **Text only** → replies are silent.

**Expected:** the orb pulses while listening, the transcript appears, spoken output stops on
interrupt. In a browser without the Web Speech API (e.g. Firefox) the orb reports that voice
input is unavailable instead of failing silently.

## 2. English, Hindi, Hinglish — ✅

**Automated:** `tests/routing.test.ts` › *language detection* (Devanagari, Hinglish, English).

**Manual:** send "मुझे एक नया व्यवसाय शुरू करना है", then
"mujhe ek startup shuru karna hai, kya karna chahiye", then an English question. Each reply
carries a language pill (hi / hinglish) in the message header.

**Scope:** detection is heuristic and passed to the model as an instruction. Reply quality in
Hindi/Hinglish depends on the model you configure; the offline provider returns fixed
templates in the right language only for hi/hinglish hints.

## 3. Tasks — ✅

**Automated:** `tests/api.test.ts` › *tasks* (create/list/complete, dependency block, input
validation); `tests/routing.test.ts` › *action proposals*.

**Manual**
1. Say: "Jarvis, create a task to interview five customers tomorrow."
2. Open **Tasks** → the task exists with due date tomorrow and source *created by J.A.R.V.I.S.*
3. Add a second task, set its dependency on the first (API), try to mark it done → the API
   returns `409 dependency_blocked` and the UI shows the reason.
4. Mark a task done → completion timestamp is set and the audit log records it.

## 4. Real workflow and agent status — ✅

**Automated:** `tests/api.test.ts` verifies run stages `routed`, `generating`, `validating`
exist for a completed turn.

**Manual:** send a message with **Workflow monitor** open in another tab. Stage events appear
live. Click a run → the timeline shows each stage with timestamps. Progress is
`stage_done / stage_total` and is labelled "stages", not a fake percentage counter.

## 5. Research with source links — 🟡 partial

**Automated:** `tests/api.test.ts` › *fails honestly when outbound fetching is disabled*.

**Why partial:** there is no search API, so J.A.R.V.I.S. cannot discover URLs. It can fetch
URLs **you** supply, and each fetch requires approval.

**Manual (fetch disabled — default)**
1. Send: "research this https://example.com/page for me".
2. Open **Approvals** → a `web_research` request is waiting with its payload.
3. Approve → it fails with `Outbound network access is disabled…`, the approval shows
   `failed`, and the research library shows a note with status `failed`.
   Nothing is invented to fill the gap.

**Manual (fetch enabled)**
1. Set `ALLOW_OUTBOUND_FETCH=true`, restart.
2. Repeat the steps above → sources are stored with URL, retrieval date and a verbatim
   excerpt; the note status becomes `sources_retrieved`.

## 6. Context across sessions — 🟡 partial

**Manual**
1. Say: "Jarvis, remember that all budgeting is in INR."
2. Sign out, sign back in, open **Memory** → the entry exists, labelled *owner-stated*.
3. Start a new conversation and ask "what currency do I use for budgeting?" → the memory is
   included in the prompt context and the reply respects it.

**Scope:** memory is global to the owner, with a `project` field reserved. Hard per-project
context boundaries are not enforced yet.

## 7. Generate and test a website — ⛔ not met

The Coding and Website agents give architecture and code guidance, but there is **no**
file editing, code execution, test runner, preview or deployment. The UI marks these as
planned. Do not expect a runnable site from this build.

## 8. Practical startup plan — 🟡 partial

**Manual:** ask "I want to start an AI company, help me plan the first month."
The Business Strategist answers with problem → customer → evidence gaps → risks → a
recommended next test, and can create the tasks. The full Phase A–D workspace (business plan,
PRD, competitor matrix, version history) is not built yet; plans live in conversation and tasks.

## 9. Budget and cash-flow forecast — ✅

**Automated:** `tests/finance.test.ts` (runway, break-even, assumptions, currency filtering,
clamping) and `tests/api.test.ts` › *records finance entries and computes a forecast*.

**Manual:** **Finance** → record an expense → the summary updates. In *Cash-flow & runway
forecast* enter starting cash ₹50,000, revenue ₹0, costs ₹15,000, 12 months → runway 3.33
months, the month cash goes negative is flagged, and the panel lists assumptions and warnings
("forecast, not actual results").

## 10. Jurisdiction before compliance research — ✅

**Automated:** `tests/routing.test.ts` routes GST questions to the compliance agent;
`tests/api.test.ts` verifies publication and effective dates are stored separately and that
invalid areas are rejected.

**Manual:** ask about a regulation without naming a country → the reply asks for the
jurisdiction (enforced by the agent's system prompt) instead of assuming. The register stores
`IN` by default and every row keeps publication date, effective date, publisher and source URL.

## 11. Approval before protected external actions — ✅

**Automated:** `tests/api.test.ts` › *queues actions for approval at autonomy level 1*,
*rejects an approval without executing it*, *refuses to decide the same approval twice*;
`tests/routing.test.ts` › *queues every change at autonomy level 1*.

**Manual:** approve and reject cards in **Approvals**; confirm rejection created nothing, and
reusing a decided approval returns `already_decided`.

**Policy:** external actions always require approval; high-risk actions always require
approval; level 1 queues everything.

## 12. Draft vs attempted vs confirmed — ✅

**Automated:** `tests/validation.test.ts` › *corrects a fabricated action claim…*.

**Manual:** every assistant message shows action chips with their real status
(`pending`, `auto_executed`, `approved`, `rejected`, `failed`) plus the execution result or
error. Sending that cannot happen is listed under *Deliberately unavailable* rather than
simulated.

## 13. Graceful provider and integration failure — ✅

**Manual**
1. Set `AI_PROVIDER=openai` with a wrong key, restart, send a message → the turn fails with
   `Provider rejected the credentials (HTTP 401)` and a hint pointing at `.env`. No fallback
   answer is fabricated.
2. Set `AI_BASE_URL` to an unreachable host → timeout error, run marked `failed`, usage record
   marked unsuccessful.
3. Ask about WhatsApp → the agent states no integration is connected.

## 14. Credentials out of frontend and logs — ✅

**Automated:** `tests/api.test.ts` › *never returns provider credentials or the session secret
to the client* (uses sentinel values across capabilities, settings, health and briefing).
`tests/validation.test.ts` verifies credential-like strings in model output are redacted.

**Manual:** `git grep -i "api_key" web/` → only text labels, no values. Inspect the built
bundle in `web/dist/assets/*.js` → no key material. Check a server log line → fields named
like `token`, `secret`, `key` are `[redacted]`.

## 15. Stop an authorized task — 🟡 partial

**Manual:** start a turn with a slow provider, then press **Stop** in the composer or **Cancel**
in the Workflow monitor. The provider request is aborted, the run is marked `cancelled`, and
the UI says so.

**Scope:** cancellation works for the in-flight request in this process. Because runs are not
queued background jobs, there is nothing to cancel after a request ends
(`not_running_in_this_process` is returned honestly).

## 16. Report incomplete features — ✅

**Manual:** open **Agents** → three groups (active / limited / planned). Open **Approvals** →
*Deliberately unavailable* lists send, publish, money movement and deployment with reasons.
**Settings** → *Speaker verification is not implemented*. Offline replies are labelled
"Offline template response — not a model answer".

---

## Running the automated suite

```bash
npm ci          # or npm install
npm test        # 66 tests
npm run typecheck
npm run build
```

Expected tail:

```
Test Files  5 passed (5)
     Tests  66 passed (66)
```

## Manual end-to-end smoke test (5 minutes)

```powershell
npm run dev
```

1. Enrol as owner → dashboard loads with an honest warning list.
2. Conversation: "create a task to validate my pricing tomorrow" → task chips appear, task
   exists in **Tasks**.
3. Ask a legal question → routed to *Legal & Compliance Research*, which asks for jurisdiction.
4. Approvals: nothing pending (low-risk internal record auto-executed at level 2) → set
   autonomy to 1 in Settings, repeat step 2 → the action is now pending; approve it.
5. Finance: record ₹1,500 expense, run a 6-month forecast → runway and warnings shown.
6. Ethics check: ask "did you email the investor?" → the answer says nothing was sent.
