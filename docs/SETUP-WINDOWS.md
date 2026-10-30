# Windows setup, step by step

Written for Windows 10/11 with PowerShell. Every command is copy-pasteable. If you are on
macOS or Linux, the same commands work in bash/zsh with `cp` instead of `Copy-Item`.

---

## 1. Install Node.js 22

J.A.R.V.I.S. needs **Node 22.12 or newer** because it uses Node's built-in SQLite driver
(no native compilation, nothing to install for the database).

1. Go to <https://nodejs.org> and download the **LTS** installer (22.x or newer).
2. Run the installer with default options.
3. **Close and reopen PowerShell**, then check:

```powershell
node -v      # must print v22.12.0 or higher
npm -v
```

If `node` is not recognised, the installer did not add Node to PATH — reinstall and tick
"Add to PATH".

## 2. Get the project

```powershell
cd $HOME
git clone <your-repo-url> Jarvis
cd Jarvis
```

No git? Download the ZIP from GitHub, extract it, then `cd` into the folder.

## 3. Create your configuration

```powershell
Copy-Item .env.example .env
```

Generate a session secret and paste it into `.env` as `SESSION_SECRET`:

```powershell
node -e "console.log(require('crypto').randomBytes(48).toString('base64url'))"
```

Minimal `.env` to start (offline mode, no API key):

```ini
NODE_ENV=development
PORT=5173
SESSION_SECRET=<the long string you generated>
AI_PROVIDER=offline
AUTONOMY_LEVEL=2
ALLOW_OUTBOUND_FETCH=false
WAKE_PHRASE=jarvis
```

> `.env` is git-ignored. Never paste its contents into a chat, an issue or a screenshot.

## 4. Install dependencies

```powershell
npm install
```

Expect ~20 seconds and a few hundred MB in `node_modules`. If PowerShell blocks scripts:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 5. Start it

```powershell
npm run dev
```

You should see structured log lines ending with:

```
{"level":"info","msg":"J.A.R.V.I.S. online","url":"http://localhost:5173",...}
```

Open <http://localhost:5173>. First run shows **owner enrolment** — pick a name, email and a
password of at least 10 characters containing a letter and a number. There is no reset, so
store it in your password manager.

## 6. Add a real language model (recommended)

Pick **one** provider, edit `.env`, restart with `Ctrl+C` then `npm run dev` again.

### Option A — hosted API (best quality, pay per token)

```ini
AI_PROVIDER=openai
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
AI_API_KEY=sk-...
```

Any OpenAI-compatible endpoint works (Groq, Together, OpenRouter, vLLM, LM Studio). Set the
matching `AI_BASE_URL` and `AI_MODEL`, and update the two price variables so the cost
estimates are meaningful:

```ini
AI_PRICE_INPUT_PER_MTOK=0.15
AI_PRICE_OUTPUT_PER_MTOK=0.60
```

Check current prices with your provider — the defaults are illustrative, not authoritative.

### Option B — local model (free, private, slower)

1. Install Ollama from <https://ollama.com/download>.
2. In PowerShell: `ollama pull llama3.1:8b`
3. `.env`:

```ini
AI_PROVIDER=ollama
AI_BASE_URL=http://127.0.0.1:11434/v1
AI_MODEL=llama3.1:8b
AI_API_KEY=
AI_PRICE_INPUT_PER_MTOK=0
AI_PRICE_OUTPUT_PER_MTOK=0
```

### Verify the model

In the app, the header pill turns green (**model ready**). Or:

```powershell
Invoke-RestMethod http://localhost:5173/api/health | ConvertTo-Json -Depth 4
```

Expect `"aiProvider": "ok"`. If it says `not_configured`, the key is missing — check for
trailing spaces in `.env`.

## 7. Enable internet research (optional)

```ini
ALLOW_OUTBOUND_FETCH=true
RESEARCH_ALLOWED_HOSTS=              # empty = any public host; private IPs always blocked
```

Note what this does **not** do: there is still no search engine. J.A.R.V.I.S. can only fetch
URLs you supply, and each fetch requires your approval.

## 8. Run the tests and a production build

```powershell
npm test          # 66 tests: routing, finance, validation, security, API, UI render
npm run typecheck # strict TypeScript
npm run build     # web/dist + dist/server
npm start         # production build on http://localhost:5173
```

`npm start` refuses to boot if `SESSION_SECRET` is missing — that is deliberate.

## 9. Where your data lives

- `data\jarvis.sqlite` — users, conversations, tasks, approvals, memory, audit log
- `data\jarvis.sqlite-wal`, `-shm` — SQLite write-ahead log files (normal)
- Delete the whole `data` folder to start from scratch (this erases everything)

Back up by copying `data\` while the server is stopped.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Cannot find module 'node:sqlite'` | Node is older than 22.5 — upgrade Node |
| `SESSION_SECRET is required` | Production mode needs it; add it to `.env` |
| Port already in use | Change `PORT` in `.env`, or stop the other process |
| Header says *model offline* | `AI_API_KEY` missing/whitespace, or wrong `AI_BASE_URL` |
| Reply fails with `Provider rejected the credentials` | Bad/expired API key |
| `Provider did not respond within … ms` | Raise `AI_TIMEOUT_MS`, or use a faster model |
| Voice orb does nothing | Use Chrome/Edge; allow microphone permission for the site |
| No voices when speaking | Windows speech voices missing — Settings → Time & language → Speech |
| `npm install` native build errors | This project has **no** native dependencies; delete `node_modules` and retry |
| Login says invalid credentials after restart | `SESSION_SECRET` changed — sessions are invalidated by design |

## Keeping it running

For daily use, `npm run dev` is fine. For something closer to production on your own PC:

```powershell
npm run build
$env:NODE_ENV="production"
npm start
```

That serves the built dashboard with a strict Content-Security-Policy. Do not expose the port
to the public internet without HTTPS, a reverse proxy and a review of
[docs/SECURITY.md](SECURITY.md) — the current build is designed for local or trusted use.
