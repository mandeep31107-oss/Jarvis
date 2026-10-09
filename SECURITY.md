# Security model

This document states what Jarvis will and will not do, how that is enforced in code rather than
by convention, and what is **not** defended against yet. Read the last section — an honest
threat model matters more than a reassuring one.

---

## The core position

Jarvis is designed so that the safe path is the default path and the unsafe path is unreachable,
rather than designed so that unsafe things are possible but discouraged.

Three consequences of that:

1. **Refusing is cheaper than complying.** A hard denial is a raised exception with a safer
   alternative attached. It is less code than doing the thing.
2. **Not knowing is not permission.** Unknown platform terms stop a plan exactly like denied
   ones. An unverified regulation is reported as unverified.
3. **The user can always stop it, and can see that it stopped.** `/stop` and `emergency_stop()`
   work in every state, including mid-task. The stop is recorded in the audit log, surfaced as
   `run_state: emergency_stopped` in `status()`, printed at the top of `jarvis status`, and it
   **refuses new work** until `/resume` — so "stop" means stop, not "finish what is running". A
   system the authorised user cannot halt, or cannot confirm has halted, is a malfunction.

---

## Secrets

No credential appears in source, in memory, in the audit log, or in any rendered output.

Three independent layers, because a name-based blocklist always misses something:

| Layer | Where | What it does |
|---|---|---|
| Name filter | `config.is_secret_name` | Drops variables named `*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*CREDENTIAL*`, and `*_URL` / `*_URI` / `*_DSN` / `*_CONN` (connection strings routinely embed passwords) |
| Shape scanner | `util/redaction.scan` | Matches PEM blocks, AWS keys, GitHub/GitLab/Slack/Stripe/OpenAI/Google/Anthropic/Telegram tokens, JWTs, bearer tokens, URL credentials, and `X = "value"` assignments |
| Write-time gate | `memory.remember`, `AuditLog.record` | Applies the scanner before the value reaches storage |

`jarvis config --show` renders `JARVIS_DATABASE_URL` as `***` and lists API keys as provider
*names* only (`configured_providers: [OPENAI]`). `jarvis check-secret` exits 1 on a hit so it can
guard a commit hook or CI step.

The shape scanner has been wrong before in a way worth recording: it originally read a named
regex group that four of its own patterns do not define, so it raised `IndexError` instead of
reporting. A redaction scanner that crashes fails *open*. It is now covered by tests that run
every pattern.

---

## Risk and permission

Every action that reaches the outside world is an `ActionRequest` through
`Supervisor.propose()`. There is no other path.

```
LOW      reads, searches, memory lookups           → runs under autonomy=auto
MEDIUM   sending a message, writing a file         → asks
HIGH     installing software, spending, OS changes → asks; never auto-approved
CRITICAL irreversible or money-moving              → always stops
```

Two properties are load-bearing and are pinned by tests:

- **Unmatched actions fall to HIGH.** An action no rule recognises gets `unclassified.default` at
  HIGH. Unknown means cautious, not permissive.
- **Hard denials cannot be approved.** All 12 forbidden rules are walked through
  `PolicyEngine.gate()` under an `AutoApprover(RiskLevel.CRITICAL)` — an approver that says yes to
  everything — as `jarvis`, `owner`, `boss`, `admin` and `root`. Every one still raises
  `HardDenial`. A control test confirms the same fixture *does* approve a normal HIGH-risk
  action, so the assertions are not passing vacuously.

That second test exists because the first version was broken: the rule engine required both the
verb *and* the context regex to match, and the hard denials only define a verb. They never fired.
The suite passed. Nothing caught it until a test was written specifically to walk the forbidden
set.

---

## Sandbox

`agents/coding.py` reviews code with `ast` before executing any of it, and refuses to run code
that does not parse or that touches a risky module or call.

Execution happens in a subprocess with:

- `python -I -S -E` — isolated mode, no site-packages, ignore environment variables
- `os.setsid()` — a new process group, so the whole tree can be killed
- `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`, `RLIMIT_NPROC` — CPU seconds, address space, bytes
  per file, and no child processes
- an environment scrubbed to `PATH`, `PYTHONDONTWRITEBYTECODE`, `PYTHONIOENCODING`, `HOME`,
  `TMPDIR`

Untrusted code is never run on the host interpreter. `run_tests` invokes the project's own runner
rather than a re-implementation, and treats a **zero-test run as a failure** — `unittest discover`
exits 0 when it finds no `TestCase` classes, which would otherwise report "tests passed" for a
project whose tests never ran.

---

## Privacy

### Camera and microphone

Capture devices require explicit, revocable consent recorded in `consent.json`. Without it,
`ConsentRequired` is raised. With computer use disabled, `CapabilityUnavailable` is raised first.

`camera.record_covert` is a hard denial. There is no code path to record without a visible
indicator, and no flag to add one.

The shipped `PosixHost` has **no camera or microphone backend at all** — `camera_on()` raises
`CapabilityUnavailable` even with computer use enabled. The consent gate is exercised in tests
through a `CaptureHost` double that declares the capability, so the gate is covered without
pretending a backend exists.

### Memory

Nine categories, all soft-deleted. The user can view, search, edit, delete, disable and export
every record. The `financial` category is sensitive and refuses writes without
`authorised=True`.

The audit log has **no delete method**. That is deliberate: an audit log the system can edit is
not an audit log. It is redacted on the way in, so a credential never reaches the file.

### Language

No conversation content leaves the machine. There is no telemetry and no analytics. With no
provider configured — the default — nothing is sent anywhere.

---

## What is not defended against yet

Stated plainly, because a threat model that omits its gaps is worse than no threat model.

- **The sandbox is `rlimits` + process isolation, not a container or a VM.** It stops runaway CPU,
  memory and disk, and it removes environment variables. It does not provide a filesystem or
  network namespace. Code run in it can still read files the user can read. Phase 9 adds a
  namespace or container boundary; until then, treat sandboxed code as semi-trusted.
- **There is no authentication on the REPL.** Anyone with shell access to the machine can drive
  Jarvis. That is the same trust boundary as the shell itself, and it is acceptable for a
  single-user local tool — but it is not a boundary Jarvis adds.
- **The audit log is a local file with no integrity chain.** It is append-only by API and has no
  delete method, but a user with filesystem access can edit it. Tamper-evidence (hash chaining)
  is Phase 9.
- **`.env` files are plaintext on disk.** This is normal for local tooling, but it means the file
  permissions and the disk encryption are the real controls. Jarvis never writes a secret to
  `.env`; it only reads.
- **No signed or verified compliance data.** The seed data ships unverified with
  `last_verified: null`. It is a starting point for checking primary sources, not a legal
  database. Every compliance render carries the disclaimer.
- **Model providers are trusted once configured.** Output from a provider is treated as
  untrusted text for the purposes of memory and knowledge ingestion (it goes through the 8
  gates), but a prompt-injection attempt in fetched content is not yet modelled as a threat.
  That is a Phase 2 concern and it is not solved.

---

## Reporting a security problem

Open a private report rather than a public issue. Include what you did, what you expected, and
what happened. If it involves a credential, do not paste the credential — `jarvis check-secret`
will confirm whether a string is secret-shaped without revealing it.
