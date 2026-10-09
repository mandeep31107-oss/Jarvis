# Jarvis

An autonomous personal agent that does useful work — research, analysis, documents, code,
compliance review, and revenue evaluation — under an explicit risk and permission model.

**Current state: Phase 1 of 9.** The core runtime is implemented and tested. Voice, camera
control, and browser automation are interface-shaped stubs that refuse honestly rather than
pretend to work. See [ROADMAP.md](ROADMAP.md) for what exists and what does not.

```
pip install -e .
jarvis status
jarvis "summarise the EU rules on selling digital services"
jarvis revenue "websites for local clinics"
jarvis lock                 # emergency stop - persists across restarts
jarvis resume               # clear it
jarvis                      # interactive REPL
```

---

## The one rule that shapes everything

The build brief says *"do whatever it takes to make money"* and, in the next breath, that this
must never be read as permission to break rules. Those two sentences are not in tension here —
the second one is the constraint that makes the first one safe to act on.

So Jarvis is built so that refusing is the **cheapest possible code path**:

- A hard-denied action (solving a CAPTCHA, bypassing a login, bypassing a rate limit or paywall,
  sending spam, impersonating a person, moving money fraudulently, exporting secrets, covert
  recording, disabling a safety control) raises `HardDenial` and **cannot be approved by anyone**.
  Not by the user, not by a higher autonomy level, not by a callback that returns `True`.
- Every refusal comes with a legal alternative, because a dead end is not an answer.
- Anything Jarvis will not do, it says so plainly. It does not sugar-coat an approach that will
  fail.

There are **zero runtime dependencies**. The core runs on the standard library alone.

---

## What actually works today

| Capability | Status | Notes |
|---|---|---|
| Agent runtime, supervisor, event bus | ✅ working | 10/11 agents report ready |
| Task manager (priority, retry, pause, interrupt, resume) | ✅ working | checkpointer + crash recovery |
| Risk engine + permission engine | ✅ working | LOW / MEDIUM / HIGH / CRITICAL, 12 hard denials |
| Option generator A–E | ✅ working | safe / fast / profitable / conservative / cancel |
| Long-term memory (9 categories) | ✅ working | SQLite, soft delete, export, secrets refused |
| Compliance knowledge layer | ✅ working | 10 jurisdictions, confidence + source + age |
| Platform terms engine | ✅ working | 13 platforms, STOPs on conflict *and* on unknown |
| Revenue pipeline | ✅ working | 10 stages, scam screening, expected value |
| Documents: XLSX, DOCX, PDF, CSV, Markdown | ✅ working | written from scratch, validated on read-back |
| Coding: review + sandboxed execution | ✅ working | `ast` review, subprocess sandbox, rlimits |
| Website/app scaffolding | ✅ working | plan → scaffold → validate |
| Model router | ✅ working | offline by default; cloud models need a provider |
| Multilingual text (EN / Hindi / Hinglish) | ✅ working | script-then-lexicon detection |
| Audit log | ✅ working | JSONL, redacted, no delete by design |
| Voice (STT / TTS / interruption) | ⚠️ **stub** | session model + interruption semantics work; **no audio backend** |
| Vision / camera | ⚠️ **stub** | consent + privacy-mode gates work; **no capture backend** |
| Computer control | ⚠️ **stub** | Linux `xdg-open` only; no keyboard/mouse control |
| Web scraping / live research | ❌ **not built** | returns a structured "I cannot do this" |
| Dashboard UI | ❌ **not built** | Phase 7 |
| PPTX | ❌ **not built** | behind the `docs` extra |

Every ⚠️ and ❌ above raises `CapabilityUnavailable` or returns an explicit "not implemented"
outcome. Nothing fabricates a result.

---

## Architecture

```
interaction/   CLI · intent parsing · language detection · voice session model
    ↓
core/supervisor.py     plan → check → execute → verify → report
    ↓
agents/                revenue · coding · documents · research · compliance · …
    ↓
core/                  risk · policy · tasks · memory · knowledge · router · audit
    ↓
host/                  capability-gated OS adapters (Linux / Windows / Android)
```

Full detail in [ARCHITECTURE.md](ARCHITECTURE.md).

Two invariants worth knowing before you touch the code:

1. **`Supervisor.propose()` is the only path in.** Every OS action, every payment, every message
   goes through the risk engine and the policy engine. An agent that reaches around it is a bug.
2. **Every claim carries a confidence level.** An agent result is `verified` only when its
   weakest claim is at least MEDIUM confidence. "Weakest" is deliberate — one uncertain claim
   makes the whole result unverified.

---

## Configuration

Copy `.env.example` to `.env`. Nothing is required; the defaults are safe.

```bash
JARVIS_HOME=~/.jarvis          # where memory, audit log and documents live
JARVIS_AUTONOMY=auto           # auto | ask | review
JARVIS_JURISDICTION=IN         # IN US CA UK EU AU SG UAE JP KR
JARVIS_LANGUAGE_POLICY=match   # match | en | hi | hinglish
JARVIS_COMPUTER_USE=off        # set to 'on' to permit OS control at all
JARVIS_CAPTURE_DEVICES=        # comma-separated: camera, mic, screen
JARVIS_ALWAYS_ACTIVE=off       # background loop (Phase 9)
```

Secrets are read from the environment, never from source and never from memory.
`jarvis check-secret` scans text for credential-shaped content and exits 1 on a hit, so it can
guard a commit hook or CI step. `jarvis config --show` prints the effective configuration with
secret-named values masked: `JARVIS_DATABASE_URL` renders as `***`, and API keys appear only as
provider *names* (`configured_providers: [OPENAI]`), never as values.

---

## The REPL

```
$ jarvis
/help
/revenue websites for local clinics
/build ecommerce output/shop
/doc xlsx Q3 Projection
/memory search clinic
/audit
/pause            /resume            /lock            /unlock
```

24 slash commands (`/help` lists them). Unknown commands, bad arguments, and agent failures are
caught and reported; the REPL does not crash on a bad line.

`/lock` (or `jarvis lock`) halts all tasks, kills capture devices, engages the host privacy lock
and refuses new work. The lock is **persisted to disk**, so opening a new shell does not undo it —
`jarvis resume` clears it. Every stop and resume is audited, and the state is shown as
`run_state: EMERGENCY_STOPPED` at the top of `jarvis status`. No autonomy level or actor bypasses
it. If the state file cannot be read, Jarvis assumes it is still stopped rather than guessing
that it may run.

---

## Verification

```bash
python -m pytest tests/ -q        # 456 tests
ruff check src tests              # clean
```

The suite covers the pieces that matter for safety, not just the happy path: that hard denials
still deny under an all-approving approver, that a refused action is audited *before* the
exception propagates, that an empty `.env.example` is not flagged as containing a secret, that a
zero-test run is not reported as a pass, that memory refuses to store a credential, that
`UNKNOWN` platform terms stop a plan just like `DENIED` does.

Tests that would pass for the wrong reason have been rewritten when found. Two examples caught
during Phase 1:

- `redaction.scan` raised `IndexError` on four of its own patterns because it read a named group
  that those patterns do not define.
- The hard-denial rules never fired, because the rule engine required both the verb *and* the
  context regex to match, and the hard denials only define a verb.

---

## Legal and tax output

Everything in `jarvis/compliance` is information and risk analysis, **not a substitute for
qualified legal or tax advice**. It is appended to every compliance render, in code, not by
convention.

Every seeded regulation and platform rule ships with `last_verified: null`. Jarvis will not
present an unverified rule as current: rules age out after 365 days (jurisdictions) and 180 days
(platforms), and `jarvis status` reports how many are overdue. The seed data is a **starting
point for verification, not a legal database** — treat every entry as a prompt to check the
primary source.

---

## Roadmap

Phases 2–9: live research with source validation → voice and vision → computer use →
dashboard → always-active operation → hardening. Each phase is gated on the previous one being
verified, and none of them may weaken the gates built in Phase 1.

See [ROADMAP.md](ROADMAP.md) and [SECURITY.md](SECURITY.md).
