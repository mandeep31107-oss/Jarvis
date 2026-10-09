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

**Verification:** 778 tests, `ruff check` clean, CLI smoke-tested end to end.

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

## Phase 3 — Voice ⚠️ **audio core built; transcription is not**

**Built and tested** (`jarvis/voice/`, stdlib only — `audioop` is deprecated and slated for
removal in 3.13, so it is not used):

- 16-bit PCM WAV reading and writing, RMS, zero-crossing rate, envelopes, stereo interleave
- Voice-activity detection calibrated against each recording's own noise floor, not a fixed
  threshold; endpointing keeps pauses between words inside one utterance while splitting on a
  real change of mind; a minimum duration rejects clicks
- Barge-in detection: `VoiceSession.detect_barge_in()` decides from the samples, ignoring
  speech that belongs to the original request so Jarvis does not interrupt itself
- Verified against hand-derivable values: a 440 Hz sine reads 878 Hz of zero crossings and
  RMS 0.3535 (= 0.5/√2); a phrase at 0.4–1.0 s is detected at 0.39–1.01 s

**Still missing:**

- STT and TTS provider adapters (`SpeechToText` / `TextToSpeech` protocols are defined;
  `NullSpeechToText` raises rather than fabricating a transcript). Turning samples into words
  needs a model and none is bundled.
- Push-to-talk and wake-word, with the wake-word explicitly **not** usable as authentication
- Language auto-switch on the detected language of each utterance

**Hard constraint from the brief:** voice is never the sole authentication mechanism for a
high-risk operation, and "a person was detected" is not "the boss was detected". Risk gating
stays exactly where it is.

---

## Phase 4 — Vision ⚠️ **codec and analysis built; capture and meaning are not**

**Built and tested** (`jarvis/vision/`, no third-party dependency):

- A PNG reader and writer on `zlib`/`struct` alone: greyscale, grey+alpha, RGB, RGBA and
  paletted images, 1/2/4/8-bit depths, all five scanline filters, CRC verified per chunk
- Frame analysis: brightness, luma histogram, dominant colour, edge density, and frame-to-frame
  motion with a normalised centre of activity
- Interlaced and 16-bit images are **refused with a reason** rather than mis-decoded
- Cross-verified against ImageMagick: 48 pixel comparisons across all five filter types,
  greyscale and 1-bit/8-bit palettes, zero mismatches; ImageMagick also reads back what the
  writer produces. Three real bugs were found this way, including one that returned correct
  dimensions with every pixel zero.
- `VisionAgent` reports measurements and records `interpreted=False`

**Still missing:**

- Camera capture. This sandbox has no `/dev/video*`, so a capture adapter cannot be verified
  here and is not written. It would sit behind the existing `Device` consent gate.
- Scene interpretation. Describing what an image *means* needs a vision model; without one the
  agent says so instead of guessing.
- A **visible** indicator whenever the camera is on; `camera.record_covert` stays hard-denied
- Redaction of faces and documents before anything is stored

**Hard constraint:** no covert recording, indicator always visible, user can disable at any time.

---

## Phase 5 — Computer use ⚠️ **process layer built; GUI control is not**

**Built and tested** (`jarvis/host/procfs.py`):

- The process table read straight from `/proc`: pid, name, full command line, state, parent,
  owner, RSS, and CPU seconds converted from clock ticks (reported raw they would overstate
  CPU use a hundredfold)
- `/proc/<pid>/stat` parsed around the parenthesised `comm`, which may contain spaces and even
  `) (` — splitting on whitespace shifts every later field and yields nonsense rather than an
  error
- `AppInfo.running` is now derived from the process table. It was hardcoded `False`, which made
  the field a lie rather than an absence.
- Cross-checked against `ps`, which reads `/proc` by its own route: 76 shared PIDs, zero name
  and zero parent mismatches
- Listing processes needs no `JARVIS_COMPUTER_USE` switch — observing is not controlling
- Screenshot, UI control and shortcuts name the missing display server instead of saying
  "not implemented", so a policy refusal is distinguishable from an environment with nothing
  to look at

**Still missing:**

- Accessibility-API driven control per platform (AT-SPI / UI Automation / Android Accessibility)
- Element targeting by description, not by pixel coordinates alone
- Dry-run mode: show what would be clicked before clicking
- Per-application allowlists

**Hard constraint:** never bypass a login, a CAPTCHA, or a rate limit. `JARVIS_COMPUTER_USE`
stays off by default and is the master switch.

---

## Phase 6 — Revenue execution ⚠️ **execution and monitoring built; money is not**

**Built and tested** (`jarvis/revenue/execution.py`):

- `Executor` runs an approved plan's steps in order, asking the policy engine about **each step
  individually** at the moment it runs. Approval of a plan is not approval of every consequence
  of it, so a forbidden verb inside an approved plan is still refused, and a refusal stops the
  run rather than being stepped over.
- An unapproved plan executes nothing. Approvals are recorded per plan, with who and when, and
  are never inferred.
- A durable ledger under the runtime home, so a plan survives a restart and can be audited. A
  corrupt ledger is quarantined to `.corrupt` rather than overwritten — erasing the audit trail
  to make a run work is not acceptable.
- `Monitor` evaluates thresholds and reports only breaches; a metric that is not being measured
  is not reported as healthy. Thresholds come from the model's own numbers, so the break-even
  alert fires where the plan actually stops working.
- `RevenueAgent.plan_from_assessment()` turns requirements into steps and
  `monitor_rules()` derives what to watch

**Deliberately never automated:** moving money, opening accounts, verifying identity, signing
contracts, filing tax, accepting terms. These are reported as `blocked_on_user` — a state
distinct from `failed`, because one means a person must act and the other means Jarvis tried and
could not.

**Still missing:**

- Payment and payout integration **inside each platform's sanctioned channel only**
- Tax estimation flagged as an estimate, with the disclaimer, never as advice

**Hard constraint:** every spend and every payout is HIGH or CRITICAL and asks. CRITICAL always
stops.

---

## Phase 7 — Dashboard ✅ **complete**

Spec section 29's 14 sections, as a local web UI. Shipped.

- `jarvis dashboard --port 8642` serves `/`, `/api/status`, `/api/health`
- Read-only by default: `POST /api/request` returns 403 unless `--allow-actions`
  is passed, and even then the request goes through the runtime, so the risk and
  policy engines still decide
- Security headers on every response (`X-Frame-Options: DENY`,
  `Content-Security-Policy: default-src 'self'`, `nosniff`, `no-referrer`)
- No external assets: the page is one self-contained document

Also shipped: the PPTX writer, completing the document set. Verified by opening
the output with the real `python-pptx`, not only with our own validator.

---

## Phase 8 — Always-active operation ✅ **core complete**

- ✅ Background loop honouring `PAUSE` / `STOP` at every iteration, with a
  heartbeat, audited start/stop, and a `stop()` that joins the thread
- ✅ Rush-mode priority re-evaluation (`core/rush.py`): plans at most three
  tasks, defers rather than cancels, and never accelerates work that needs
  approval
- Remaining: startup with the OS is instructions only, not an installer
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
Phase 1  ████████████████████  complete
Phase 2  ████████████████████  fetch+extract+verify, plus a bundled PyPI lookup
Phase 3  ██████████░░░░░░░░░░  WAV I/O + DSP + VAD + barge-in; transcription needs a model
Phase 4  ██████████░░░░░░░░░░  PNG codec + frame analysis; no camera, no scene meaning
Phase 5  ████████░░░░░░░░░░░░  /proc process table; GUI control needs a display server
Phase 6  ██████████████░░░░░░  plan → approve → execute → monitor; money always needs you
Phase 7  ████████████████████  complete (14 sections, read-only)
Phase 8  ████████████████░░░░  loop + rush mode done; OS autostart is text only
Phase 9  ░░░░░░░░░░░░░░░░░░░░  not started
```
