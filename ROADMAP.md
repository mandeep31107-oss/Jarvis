# Roadmap

Nine phases. Each one is gated on the previous one being **verified**, not merely written, and
none of them may weaken the gates built in Phase 1.

The ordering is deliberate: the risk engine, permission engine, audit log and memory come first,
because a capability added before them has no place to be checked. Building computer control
before the permission engine would mean bolting safety onto something already dangerous.

---

## Phase 1 — Core runtime ✅ **complete**

Spec section 38. What the brief asks for and what shipped:

| Brief requires | Status |
|---|---|
| Agent runtime | ✅ supervisor with plan → check → execute → verify → report |
| Basic model router | ✅ 6 model specs, capability + cost + latency ranking, offline default |
| Text interface | ✅ CLI, 10 subcommands, 25-command REPL |
| Task manager | ✅ priority, dependencies, retry, pause, interrupt, resume, checkpoints |
| Basic memory | ✅ 9 categories, SQLite, soft delete, export, secret refusal |
| Logging | ✅ JSONL audit log, redacted on write, no delete |

Built ahead of schedule because later phases assume them: risk engine, policy engine, options
generator A–E, compliance knowledge layer, platform terms engine, revenue pipeline, document
writers, sandboxed code execution, web scaffolding, multilingual detection.

**Verification:** 522 tests, `ruff check` clean, CLI smoke-tested end to end.

---

## Phase 2 — Live research and controlled learning ✅ **mostly complete**

The research agent currently returns "I cannot do this" because it has no way to reach a source.
Phase 2 gives it one, behind the knowledge base's 8 gates.

- Search provider adapters (pluggable, credentials from the environment)
- Source authority scoring against the `Authority` scale already in `confidence.py`
- Fetch → validate → deduplicate → conflict-check → summarise → confidence → test → store
- **`robots.txt` respected; rate limits respected; no paywall circumvention.** A source behind a
  paywall is reported as unavailable, not worked around.
- Stale-entry revalidation queue (the plumbing — `needs_revalidation`, `mark_stale` — is already
  in `knowledge.py`)

**Gate to pass:** every stored item has a source, an effective date and a confidence; nothing is
stored that failed a gate.

---

## Phase 3 — Voice

The session model exists and is tested: interruption, suspend, resume, history, multilingual
routing. What is missing is audio.

- STT and TTS provider adapters (`SpeechToText` / `TextToSpeech` protocols are defined;
  `NullSpeechToText` raises rather than fabricating a transcript)
- Push-to-talk and wake-word, with the wake-word explicitly **not** usable as authentication
- Interruption handling against real audio timing
- Language auto-switch on the detected language of each utterance

**Hard constraint from the brief:** voice is never the sole authentication mechanism for a
high-risk operation, and "a person was detected" is not "the boss was detected". Risk gating
stays exactly where it is.

---

## Phase 4 — Vision and camera

- Capture adapters behind the existing `Device` consent gate and `PrivacyMode`
- A **visible** indicator whenever the camera is on; `camera.record_covert` stays hard-denied
- Screen reading for accessibility and automation support
- Redaction of faces and documents before anything is stored

**Hard constraint:** no covert recording, indicator always visible, user can disable at any time.

---

## Phase 5 — Computer use

The `host/` adapters are the seam. Linux `xdg-open` is the only working action today.

- Accessibility-API driven control per platform (AT-SPI / UI Automation / Android Accessibility)
- Element targeting by description, not by pixel coordinates alone
- Dry-run mode: show what would be clicked before clicking
- Per-application allowlists

**Hard constraint:** never bypass a login, a CAPTCHA, or a rate limit. `JARVIS_COMPUTER_USE`
stays off by default and is the master switch.

---

## Phase 6 — Revenue execution

Phase 1 analyses opportunities. Phase 6 acts on them.

- The approved-option execution path, through `Supervisor.propose()` like everything else
- Payment and payout integration **inside each platform's sanctioned channel only**
- Monitoring against the projection, with a report when reality diverges
- Tax estimation flagged as an estimate, with the disclaimer, never as advice

**Hard constraint:** every spend and every payout is HIGH or CRITICAL and asks. CRITICAL always
stops.

---

## Phase 7 — Dashboard

Spec section 29's 14 sections, as a local web UI.

- Live status, task queue, audit trail, memory browser, revenue pipeline, compliance coverage
- Read-only by default; any action button routes through the same policy engine
- Bound to localhost, no remote access without explicit configuration

---

## Phase 8 — Always-active operation

- Background loop honouring `PAUSE` / `STOP` at every iteration
- Rush-mode priority re-evaluation
- Startup with the OS — `autostart_instructions()` already returns per-platform steps and is
  gated HIGH; installing a startup entry stays an action the user approves
- Windows Task Scheduler, Linux systemd user unit, Android/Termux `boot`

**Hard constraint:** the authorised user can always stop it. A loop the user cannot halt is a
malfunction, not a feature.

---

## Phase 9 — Hardening and verification

- Fuzz the intent parser and the document writers
- Property tests over the risk engine: assert the ceiling never rises above the configured level
- Third-party dependency review before any dependency is added at all
- Penetration-style review of the sandbox escape surface
- Restore-from-backup drills for memory and the audit log

---

## What will never be built

These are not deferred. They are out of scope permanently, and the hard denials for them are
already in place and tested:

- CAPTCHA solving or bypass
- Authentication bypass, credential cracking, MFA bypass
- Rate-limit or paywall circumvention
- Bulk unsolicited messaging
- Fraudulent transactions
- Impersonating a person, or presenting as human when it is not
- Taking someone else's intellectual property
- Exporting credentials or secrets
- Covert recording or surveillance
- Disabling a safety control
- Any design in which the authorised user cannot stop the system

If one of these is requested, Jarvis refuses and offers a legal alternative. "Do whatever it
takes to make money" is not read as permission for any of them — the brief says so explicitly,
and the refusal path is the cheapest code in the repository.

---

## Progress

```
Phase 1  ████████████████████  complete (522 tests)
Phase 2  ████████████████░░░░  fetch+extract+verify done; no search provider
Phase 3  ██░░░░░░░░░░░░░░░░░░  session model done, no audio backend
Phase 4  ██░░░░░░░░░░░░░░░░░░  consent + privacy gates done, no capture backend
Phase 5  █░░░░░░░░░░░░░░░░░░░  xdg-open only
Phase 6  ██░░░░░░░░░░░░░░░░░░  analysis done, no execution
Phase 7  ░░░░░░░░░░░░░░░░░░░░  not started
Phase 8  █░░░░░░░░░░░░░░░░░░░  loop + autostart text only
Phase 9  ░░░░░░░░░░░░░░░░░░░░  not started
```
