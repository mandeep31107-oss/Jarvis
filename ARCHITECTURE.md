# Architecture

Phase 1 of 9. This document describes what is built and **why it is shaped this way**, so that
later phases can be added without weakening the gates.

---

## The layering

```
┌─────────────────────────────────────────────────────────────────┐
│ interaction/     CLI · REPL · intent parsing · language detect   │
│                  voice session model (no audio backend yet)      │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓ natural language → intent + language
┌─────────────────────────────────────────────────────────────────┐
│ core/supervisor  plan → check → execute → verify → report        │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓ AgentRequest / Execution
┌─────────────────────────────────────────────────────────────────┐
│ agents/          revenue · coding · documents · research ·       │
│                  compliance · webbuilder · perception · support  │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓ ActionRequest (the ONLY way out)
┌─────────────────────────────────────────────────────────────────┐
│ core/            risk → policy → tasks → memory/knowledge →      │
│                  router → audit                                  │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓ capability + consent gates
┌─────────────────────────────────────────────────────────────────┐
│ host/            Linux / Windows / Android adapters              │
└─────────────────────────────────────────────────────────────────┘
```

Dependencies point one way only. `core/` knows nothing about `agents/`; `agents/` know nothing
about `interaction/`. That is what lets a later phase add a web dashboard without touching the
risk engine.

---

## The five load-bearing invariants

Everything else in the codebase is detail. These five are the design.

### 1. `Supervisor.propose()` is the only path out

Every OS action, payment, outbound message, or file mutation is expressed as an `ActionRequest`
and passed through `Supervisor.propose()`. That method runs the risk engine, then the policy
engine, then audits the decision — **and audits refusals before raising**:

```python
try:
    decision = self.policy.gate(action, preapproved=preapproved)
except HardDenial as exc:
    self.audit.log(f"action.refused.{action.verb}", ..., result="refused")
    raise
```

That `try/except` exists because the first version did not have it, and a hard refusal left no
audit record at all. A refusal is the most important event in the log.

An agent that reaches around `propose()` to act directly is a bug, not an optimisation.

### 2. Confidence is a property of every claim, and results are verified by their weakest claim

`core/confidence.py` defines `Confidence` (HIGH / MEDIUM / LOW / UNKNOWN) and `Claim`. An
`AgentResult.confidence` is the **weakest** claim it contains. `Execution.verified` is True only
when that weakest claim is at least MEDIUM.

This is deliberately pessimistic. A report with nine solid claims and one guess is not a solid
report. Banned absolutes ("guaranteed", "100%", "risk-free") are stripped from rendered claims
rather than merely discouraged.

### 3. UNKNOWN is treated like DENIED

`TermsCheck.must_stop` is True when a platform rule is `DENIED` **and** when it is `UNKNOWN`.
Not knowing whether a platform permits something is not permission. Every compliance render
carries the disclaimer:

> This is information and risk analysis, not a substitute for qualified legal or tax advice.

### 4. Nothing is presented as verified that has not been checked

Every seeded regulation and platform rule ships with `last_verified: null`. Jurisdiction entries
age out at 365 days, platform rules at 180. `jarvis status` reports the backlog:

```
compliance  : 22 regulation(s) and 13 platform polic(y/ies) need verification
```

The seed data in `compliance/data/*.json` is a **starting point for verification, not a legal
database**. It is hedged on purpose — each entry states what it is *about*, not what it permits.

### 5. A capability that is not implemented says so

`host/` raises `CapabilityUnavailable` for anything with no backend, and `ConsentRequired` for
capture devices that have not been granted. `NullSpeechToText` / `NullTextToSpeech` raise rather
than return a fabricated transcript. `NullProvider.complete()` returns a `Completion` with
`degraded=True` and text that says outright it is a placeholder:

> "No model provider is configured, so this is a placeholder rather than an answer. Jarvis will
> not invent a response."

The shipped `PosixHost` has **no camera or microphone backend**, so `camera_on()` raises
`CapabilityUnavailable` — even with computer use enabled. The consent gate is exercised in tests
through a `CaptureHost` double that declares the capability.

---

## Risk and policy

`core/risk.py` assigns LOW / MEDIUM / HIGH / CRITICAL to an `ActionRequest` from ~19 rules.
`core/policy.py` turns that into ALLOW / NEEDS_APPROVAL / DENY / HARD_DENY.

```
LOW      → runs under autonomy=auto
MEDIUM   → asks
HIGH     → asks, and cannot be auto-approved by any autonomy level
CRITICAL → always stops
```

Two details that are easy to break:

- **Unmatched actions fall to HIGH, not LOW.** An action no rule recognises gets
  `unclassified.default` at HIGH. Unknown means cautious.
- **Hard denials match on `match="any"`.** `RiskRule` normally requires both the verb *and* the
  context regex to match. The 12 `FORBIDDEN` rules define only a verb, so they register with
  `match="any"`. Before this was fixed, hard denials never fired — including under an
  all-approving approver, which is the case that matters most.

The forbidden set is 12 rules. Each names a rule plus the verbs it catches:

| Rule | Verbs it catches |
|---|---|
| `auth.bypass` | `auth.bypass`, `auth.crack`, `login.bruteforce`, `mfa.bypass` |
| `captcha.bypass` | `captcha.solve`, `captcha.bypass` |
| `ratelimit.bypass` | `ratelimit.bypass`, `ratelimit.evade` |
| `paywall.bypass` | `paywall.bypass` |
| `unauthorised.access` | accessing something the user is not authorised for |
| `impersonation` | pretending to be a person or a human operator |
| `spam` | bulk unsolicited messaging |
| `fraud` | fraudulent transactions |
| `ip.theft` | taking someone else's intellectual property |
| `secret.exfiltration` | exporting credentials or secrets |
| `covert.surveillance` | recording without a visible indicator |
| `safety.override` | disabling a safety control |

Each rule carries a safer alternative, because a dead end is not an answer. A regression test
walks all 12 through `PolicyEngine.gate()` under an `AutoApprover(RiskLevel.CRITICAL)` — an
approver that says yes to everything — and asserts every one still raises `HardDenial`.

---

## Storage

| Data | Backend | Notes |
|---|---|---|
| Memory (9 categories) | SQLite, WAL | soft delete only; `forget` marks, `purge_deleted` removes |
| Knowledge base | SQLite | 8 ordered ingestion gates |
| Audit log | JSONL append-only | redacted on write; **no delete, by design** |
| Task checkpoints | JSON | crash recovery on next start |
| Documents | files | validated on read-back after writing |

The audit log has no delete method. That is not an oversight — an audit log the system can edit
is not an audit log. Redaction happens on the way *in*, so a credential never reaches the file.

Memory refuses three things: content matching a secret pattern, sensitive-category writes without
`authorised=True`, and writes while the category is disabled.

---

## Sandbox

`agents/coding.py` reviews code with `ast` before running any of it, then executes it in a
subprocess with:

- `python -I -S -E` (isolated, no site-packages, ignore environment)
- `setsid` so the whole process group can be killed
- `rlimits` on CPU, address space, file size and process count
- an environment scrubbed down to `PATH`, `PYTHONDONTWRITEBYTECODE`, `PYTHONIOENCODING`, `HOME`,
  `TMPDIR`

Unparseable or risky code is **never executed**; `run_tests` shells out to the project's own
runner rather than a re-implementation of it.

`run_tests` treats a zero-test run as a failure. `unittest discover` exits 0 when it finds no
`TestCase` classes, which would otherwise report "tests passed" for a project whose tests never
ran — a clean exit code is not a pass when nothing executed.

---

## Secrets

Three independent layers, because a name-based blocklist always misses something:

1. `is_secret_name()` drops variables whose *name* marks them secret (`KEY`, `TOKEN`, `SECRET`,
   `PASSWORD`, `CREDENTIAL`, and — because connection strings routinely embed passwords — `_URL`,
   `_URI`, `_DSN`, `_CONN`).
2. `redaction.scan()` matches credential *shapes* (PEM, AWS, GitHub, GitLab, Slack, Stripe,
   OpenAI, Google, Anthropic, Telegram, JWT, bearer tokens, URL credentials, `X = "…"`
   assignments). `redacted_view()` masks any value it flags, whatever the variable is called.
3. Memory and the audit log apply the scanner on the way in.

`jarvis config --show` renders `JARVIS_DATABASE_URL` as `***` and reports API keys only as
provider *names*. `jarvis check-secret` exits 1 on a hit, so it can guard a commit hook.

---

## Language

`interaction/lang.py` detects language by script first (Devanagari → Hindi, CJK → the relevant
language, Latin → lexicon), then by lexicon. Hinglish is detected as its own category because it
is Latin-script Hindi, which script detection alone cannot see.

Lexicon membership is a real trap, found the hard way: `"the"`, `"do"`, `"me"` and `"help"` were
all in the Hinglish lexicon, so `"Close the browser"` detected as Hindi. Text matching no
lexicon stays `unknown` with confidence 0 rather than defaulting to English — defaulting would
have made an untested detection path look correct.

Spec section 14's three examples all route correctly:

```
'Open my project'                -> intent=computer    lang=en
'Ab mera project kholo'          -> intent=computer    lang=hinglish
'What is open source software'   -> intent=research    lang=en
```

---

## Extending

- **Add an agent:** subclass `Agent`, implement `run`, register it in `runtime.build_runtime`.
  Return an `AgentResult` with claims. Do not call the host directly — propose an
  `ActionRequest`.
- **Add a host action:** add the capability string to the adapter's `supported`, implement it
  behind `_require()` and, for capture, `_require_consent()`.
- **Add a model:** register an `LLMProvider` with the router. Do not add a `NullProvider`
  fallback for a provider you have not configured — a previous version did this and silently
  substituted a stub, hiding the misconfiguration. `missing_providers()` exists to surface the gap.

---

## Research

`research/` is three modules with one job each:

- `fetch.py` — `HttpFetcher` and `FileFetcher`. Guards: SSRF (every resolved
  address checked against `ipaddress.is_global`), scheme allowlist, optional host
  allowlist, `robots.txt`, per-host rate limiting, size/timeout/redirect caps,
  content-type allowlist, and an identifying User-Agent. A 401/403/407/451 is
  reported as `access_denied` and never retried another way.
- `extract.py` — stdlib `HTMLParser` text extraction. Scripts, styles and
  navigation are dropped; `<title>` needs an explicit exception because it lives
  inside the otherwise-skipped `<head>`.
- `pipeline.py` — `fetch → extract → assess authority → ingest`, producing a
  `RetrievalRecord` with one of five outcomes: `stored`, `rejected`, `refused`,
  `thin`, or an access denial.

Fetching is **off by default** (`JARVIS_FETCH`). Network access is a capability
the operator grants, not a convenience that is assumed.

Two decisions worth knowing:

- **Authority is never inflated.** The default is `UNVERIFIED`, and the knowledge
  base's `authority_check` gate rejects it. Promotion happens only on evidence —
  an official domain suffix, or the operator's own trusted list — and the reason
  is stored alongside the score.
- **A document the user hands over is vouched for by the user**, recorded as
  `official_secondary` with that reason spelled out. This applies only to
  `file://` sources. Fetching a page over the network does not make anyone vouch
  for it.

---

## What is deliberately not here

- No general web search. `research` reads a URL you give it, but it cannot go
  looking. A search provider is still Phase 2 work.
- No audio backend. The voice *session* model (interruption, suspend, resume, history) is real
  and tested; nothing produces or consumes sound.
- No browser or GUI automation. `xdg-open` on Linux is the only working OS action.
- No dashboard UI. Phase 7.
- No PPTX. Behind the `docs` extra.

Each of these raises or reports honestly rather than returning a plausible-looking stub result.
