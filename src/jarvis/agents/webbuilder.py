"""Website / application builder (spec section 11).

Given "build me an e-commerce website" the agent works out the requirements,
pages, stack, data model, auth, payment and deployment story, writes a real
scaffold to disk, and then checks that what it wrote is actually there.

Payments and production deployment are never wired up automatically: they need
credentials, and credentials never go into source code (spec sections 11 and 32).
The scaffold gets an ``.env.example`` and a TODO instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.confidence import Claim, Confidence

CATEGORIES = {
    "ecommerce": {
        "label": "E-commerce store",
        "pages": ["Home", "Catalogue", "Product detail", "Cart", "Checkout", "Order confirmation",
                  "Account", "Orders", "Contact", "Privacy policy", "Terms", "Refund policy"],
        "features": ["search", "filters", "cart", "orders", "email notifications", "admin dashboard"],
        "tables": ["users", "products", "product_variants", "categories", "carts", "cart_items",
                   "orders", "order_items", "payments", "addresses", "reviews", "audit_log"],
        "needs_payments": True,
    },
    "portfolio": {
        "label": "Portfolio",
        "pages": ["Home", "About", "Projects", "Project detail", "Resume", "Contact"],
        "features": ["responsive layout", "project gallery", "contact form"],
        "tables": ["projects", "skills", "messages"],
        "needs_payments": False,
    },
    "saas": {
        "label": "SaaS product",
        "pages": ["Landing", "Pricing", "Sign in", "Sign up", "Dashboard", "Settings",
                  "Billing", "Docs", "Privacy policy", "Terms"],
        "features": ["auth", "subscription billing", "dashboard", "usage metering", "email"],
        "tables": ["users", "organisations", "memberships", "subscriptions", "invoices",
                   "usage_events", "api_keys", "audit_log"],
        "needs_payments": True,
    },
    "business": {
        "label": "Business website",
        "pages": ["Home", "Services", "About", "Pricing", "Case studies", "Contact",
                  "Privacy policy"],
        "features": ["contact form", "service pages", "SEO basics"],
        "tables": ["enquiries", "services", "posts"],
        "needs_payments": False,
    },
    "blog": {
        "label": "Blog",
        "pages": ["Home", "Archive", "Post", "About", "Contact", "RSS"],
        "features": ["markdown posts", "tags", "RSS feed", "admin editor"],
        "tables": ["posts", "tags", "post_tags", "authors", "comments"],
        "needs_payments": False,
    },
    "landing": {
        "label": "Landing page",
        "pages": ["Landing", "Privacy policy"],
        "features": ["hero", "feature grid", "pricing table", "FAQ", "email capture"],
        "tables": ["leads"],
        "needs_payments": False,
    },
    "dashboard": {
        "label": "Dashboard",
        "pages": ["Sign in", "Overview", "Analytics", "Reports", "Settings"],
        "features": ["auth", "charts", "data tables", "export"],
        "tables": ["users", "metrics", "reports", "audit_log"],
        "needs_payments": False,
    },
    "education": {
        "label": "Education / course site",
        "pages": ["Home", "Courses", "Course detail", "Lesson", "Pricing", "Sign in", "Dashboard"],
        "features": ["auth", "progress tracking", "video lessons", "quizzes", "certificates"],
        "tables": ["users", "courses", "modules", "lessons", "enrolments", "progress", "quizzes",
                   "attempts", "certificates"],
        "needs_payments": True,
    },
    "booking": {
        "label": "Booking system",
        "pages": ["Home", "Services", "Availability", "Booking", "Confirmation", "Manage booking"],
        "features": ["availability calendar", "reminders", "cancellation policy"],
        "tables": ["services", "staff", "availability", "bookings", "customers", "reminders"],
        "needs_payments": True,
    },
    "ai_app": {
        "label": "AI application",
        "pages": ["Landing", "Sign in", "Workspace", "History", "Settings", "Pricing"],
        "features": ["auth", "streaming responses", "usage limits", "model routing", "audit"],
        "tables": ["users", "conversations", "messages", "usage_events", "api_keys", "audit_log"],
        "needs_payments": True,
    },
}

SECURITY_CHECKLIST = [
    "Secrets come from environment variables or a secret store, never from source.",
    "Passwords are hashed with a maintained KDF (argon2 or bcrypt), never MD5/SHA1.",
    "Sessions use HttpOnly + Secure + SameSite cookies; CSRF tokens on state-changing routes.",
    "All SQL is parameterised; no string concatenation into queries.",
    "User input is validated server-side; file uploads are size- and type-checked.",
    "Rate limiting on auth and expensive endpoints.",
    "Security headers: CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy.",
    "Dependencies pinned, with a documented update and advisory-check routine.",
    "An audit log for authentication events and data mutation.",
    "A privacy policy and, where personal data is processed, a lawful basis on record.",
]


@dataclass
class BuildPlan:
    name: str
    category: str
    label: str
    pages: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    stack: dict[str, str] = field(default_factory=dict)
    needs_payments: bool = False
    security: list[str] = field(default_factory=list)
    approvals_needed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "label": self.label,
            "pages": list(self.pages),
            "features": list(self.features),
            "tables": list(self.tables),
            "stack": dict(self.stack),
            "needs_payments": self.needs_payments,
            "security": list(self.security),
            "approvals_needed": list(self.approvals_needed),
        }


class WebBuilderAgent(Agent):
    name = "webbuilder"
    description = "Plans and scaffolds websites and applications, with payments and secrets left to the user."
    capabilities = ("website", "scaffold", "architecture", "frontend", "backend")

    DEFAULT_STACK = {
        "frontend": "React + TypeScript + Tailwind CSS (static export or Next.js)",
        "backend": "Python + FastAPI (async, typed, OpenAPI docs for free)",
        "database": "PostgreSQL 16",
        "cache": "Redis",
        "auth": "Session cookies + argon2, or an OIDC provider",
        "hosting": "Containerised; any provider with managed Postgres",
        "ci": "GitHub Actions: lint, typecheck, test, build",
    }

    def run(self, request: AgentRequest) -> AgentResult:
        category = (request.param("category") or "business").lower()
        if category not in CATEGORIES:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary=(
                    f"Unknown site category '{category}'. Supported: {', '.join(sorted(CATEGORIES))}."
                ),
            )
        name = request.param("name") or request.text or "jarvis-site"
        plan = self.plan(category, name, features=request.param("features") or [])

        destination = request.param("path")
        result = AgentResult(
            agent=self.name,
            summary=f"Plan for {plan.label}: {len(plan.pages)} pages, {len(plan.tables)} tables.",
            data={"plan": plan.as_dict()},
        )
        result.add_claim(
            Claim.certain(
                f"The plan covers {len(plan.pages)} pages and {len(plan.tables)} tables for a {plan.label}."
            )
        )
        if plan.needs_payments:
            result.add_claim(
                Claim(
                    statement=(
                        "Payment integration is planned but not wired: it needs your credentials, "
                        "and credentials must never be committed to source."
                    ),
                    confidence=Confidence.HIGH,
                )
            )
            result.follow_ups.append(
                "Approve payment setup, then put the keys in your environment or secret store."
            )
        if destination:
            written, problems = self.scaffold(plan, Path(destination))
            result.data["scaffold"] = {"path": str(destination), "files": len(written), "problems": problems}
            result.ok = not problems
            result.summary += f" Scaffolded {len(written)} files at {destination}."
            result.add_claim(
                Claim.certain(f"{len(written)} files written under {destination}.")
                if not problems
                else Claim(f"Scaffold written with problems: {'; '.join(problems)}",
                           confidence=Confidence.LOW)
            )
        else:
            result.follow_ups.append("Give me a target directory and I will write the scaffold.")
        return result

    # ------------------------------------------------------------------ plan
    def plan(self, category: str, name: str, features: Sequence[str] = ()) -> BuildPlan:
        spec = CATEGORIES[category]
        merged = list(dict.fromkeys([*spec["features"], *features]))
        approvals = [
            "Choose and approve a payment provider before any checkout code is written.",
            "Confirm the target market(s) so privacy policy and tax handling are correct.",
        ]
        if category in {"ecommerce", "education", "booking", "saas", "ai_app"}:
            approvals.append("Approve collection of personal data and pick a lawful basis.")
        return BuildPlan(
            name=name,
            category=category,
            label=spec["label"],
            pages=list(spec["pages"]),
            features=merged,
            tables=list(spec["tables"]),
            stack=dict(self.DEFAULT_STACK),
            needs_payments=bool(spec["needs_payments"]),
            security=list(SECURITY_CHECKLIST),
            approvals_needed=approvals,
        )

    # ------------------------------------------------------------------ scaffold
    def scaffold(self, plan: BuildPlan, destination: Path) -> tuple[list[Path], list[str]]:
        destination.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []

        def put(relative: str, content: str) -> None:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.rstrip() + "\n", encoding="utf-8")
            written.append(path)

        put("README.md", self._readme(plan))
        put(".gitignore", self._gitignore())
        put(".env.example", self._env_example(plan))
        put("docker-compose.yml", self._compose(plan))
        put("db/schema.sql", self._schema(plan))
        put("docs/ARCHITECTURE.md", self._architecture(plan))
        put("docs/SECURITY.md", self._security(plan))
        put("backend/app/main.py", self._backend_main(plan))
        put("backend/app/db.py", self._backend_db())
        put("backend/app/auth.py", self._backend_auth())
        put("backend/requirements.txt", "fastapi>=0.110\nuvicorn[standard]>=0.27\npsycopg[binary]>=3.1\nargon2-cffi>=23.1\npydantic-settings>=2.2\n")
        put("frontend/package.json", self._package_json(plan))
        put("frontend/src/main.tsx", self._frontend_main(plan))
        put("frontend/src/App.tsx", self._frontend_app(plan))
        put("frontend/index.html", self._index_html(plan))
        put(".github/workflows/ci.yml", self._ci())

        problems = self.validate(destination, written)
        return written, problems

    def validate(self, destination: Path, written: Sequence[Path]) -> list[str]:
        """Check the scaffold is actually there and internally consistent."""
        problems: list[str] = []
        for path in written:
            if not path.is_file():
                problems.append(f"missing after write: {path}")
            elif path.stat().st_size == 0:
                problems.append(f"empty file: {path}")
        for required in ("README.md", "db/schema.sql", "backend/app/main.py", "frontend/src/App.tsx"):
            if not (destination / required).is_file():
                problems.append(f"expected {required} in the scaffold")
        env_example = destination / ".env.example"
        if env_example.is_file():
            from jarvis.util import redaction

            body = env_example.read_text(encoding="utf-8")
            for finding in redaction.scan(body):
                problems.append(f".env.example contains something secret-shaped: {finding.label}")
            for line in body.splitlines():
                if line.strip() and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    # Anything that looks like a credential must be left blank.
                    if value.strip() and any(
                        hint in key.upper()
                        for hint in ("SECRET", "KEY", "TOKEN", "PASSWORD")
                    ):
                        problems.append(f".env.example has a value for {key.strip()}")
        return problems

    # ------------------------------------------------------------------ files
    def _readme(self, plan: BuildPlan) -> str:
        return f"""# {plan.name}

{plan.label} scaffolded by Jarvis.

## Pages
{chr(10).join(f"- {p}" for p in plan.pages)}

## Features
{chr(10).join(f"- {f}" for f in plan.features)}

## Stack
{chr(10).join(f"- **{k}**: {v}" for k, v in plan.stack.items())}

## Data model
{chr(10).join(f"- `{t}`" for t in plan.tables)}

Full DDL is in [`db/schema.sql`](db/schema.sql).

## Running it
```bash
docker compose up -d          # Postgres + Redis
cp .env.example .env          # then fill in real values - never commit .env
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

## Before you ship
{chr(10).join(f"- [ ] {item}" for item in plan.approvals_needed)}
- [ ] Every item in [docs/SECURITY.md](docs/SECURITY.md)

## Payments
{
  "Payments are intentionally NOT wired up. Choose a provider, put the keys in your "
  "environment, and build against its **test mode** first."
  if plan.needs_payments else "This site does not take payments."
}
"""

    def _gitignore(self) -> str:
        return """node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc
.env
.env.*
!.env.example
*.log
.DS_Store
coverage/
.pytest_cache/
"""

    def _env_example(self, plan: BuildPlan) -> str:
        db = plan.name.replace("-", "_")
        lines = [
            "# Copy to .env and fill in. NEVER commit .env.",
            "# Credentials stay in separate variables so nothing secret-shaped ends up",
            "# inside a URL that gets pasted into logs, issues or screenshots.",
            "DATABASE_HOST=localhost",
            "DATABASE_PORT=5432",
            f"DATABASE_NAME={db}",
            "DATABASE_USER=",
            "DATABASE_PASSWORD=",
            "REDIS_URL=redis://localhost:6379/0",
            "SESSION_SECRET=",
            "CORS_ORIGINS=http://localhost:5173",
        ]
        if plan.needs_payments:
            lines += [
                "",
                "# Payments - use TEST keys while developing.",
                "# PAYMENT_PROVIDER=stripe",
                "# PAYMENT_SECRET_KEY=",
                "# PAYMENT_WEBHOOK_SECRET=",
            ]
        return "\n".join(lines)

    def _compose(self, plan: BuildPlan) -> str:
        return f"""services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${{POSTGRES_USER:-app}}
      POSTGRES_PASSWORD: ${{POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in your environment}}
      POSTGRES_DB: {plan.name.replace('-', '_')}
    ports: ["5432:5432"]
    volumes:
      - dbdata:/var/lib/postgresql/data
      - ./db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 5s
      retries: 10
  cache:
    image: redis:7-alpine
    ports: ["6379:6379"]
volumes:
  dbdata:
"""

    def _schema(self, plan: BuildPlan) -> str:
        cols: dict[str, str] = {
            "users": "id BIGSERIAL PRIMARY KEY,\n  email CITEXT NOT NULL UNIQUE,\n  password_hash TEXT NOT NULL,\n  created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "products": "id BIGSERIAL PRIMARY KEY,\n  title TEXT NOT NULL,\n  description TEXT,\n  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),\n  currency CHAR(3) NOT NULL DEFAULT 'USD',\n  active BOOLEAN NOT NULL DEFAULT TRUE",
            "orders": "id BIGSERIAL PRIMARY KEY,\n  user_id BIGINT NOT NULL REFERENCES users(id),\n  total_cents INTEGER NOT NULL,\n  currency CHAR(3) NOT NULL DEFAULT 'USD',\n  status TEXT NOT NULL DEFAULT 'pending',\n  created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "audit_log": "id BIGSERIAL PRIMARY KEY,\n  actor TEXT NOT NULL,\n  action TEXT NOT NULL,\n  detail JSONB NOT NULL DEFAULT '{{}}'::jsonb,\n  created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        }
        parts = ["CREATE EXTENSION IF NOT EXISTS citext;", ""]
        for table in plan.tables:
            body = cols.get(table, "id BIGSERIAL PRIMARY KEY,\n  created_at TIMESTAMPTZ NOT NULL DEFAULT now()")
            parts.append(f"CREATE TABLE IF NOT EXISTS {table} (\n  {body}\n);\n")
        parts.append("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);")
        return "\n".join(parts)

    def _architecture(self, plan: BuildPlan) -> str:
        return f"""# Architecture - {plan.name}

## Layers
- **frontend/** - React + TypeScript. Talks to the backend only over `/api`.
- **backend/app/** - FastAPI. Routes are thin; logic lives in services.
- **db/** - Postgres. Migrations are the only way the schema changes.

## Request path
Browser -> reverse proxy -> FastAPI -> service layer -> Postgres/Redis

## Decisions
| Decision | Why |
|---|---|
| Postgres over a document store | Relational integrity matters for {plan.category} data. |
| FastAPI | Typed request/response, automatic OpenAPI, async I/O. |
| Server-side sessions | Simpler to reason about than JWT for a first-party app. |
| Containerised | Same runtime locally, in CI and in production. |

## Not decided yet (needs your input)
- Payment provider {"- required for this category" if plan.needs_payments else "- not needed"}
- Email provider
- Hosting target and managed database

## Non-negotiables
{chr(10).join(f"- {s}" for s in plan.security[:5])}
"""

    def _security(self, plan: BuildPlan) -> str:
        return f"""# Security checklist - {plan.name}

{chr(10).join(f"- [ ] {item}" for item in plan.security)}

## Threat notes specific to this project
- Session fixation: rotate the session id on login.
- Enumeration: return the same message for unknown email and wrong password.
- Price tampering: totals are computed server-side, never trusted from the client.
- IDOR: every query filters by the authenticated user's id.
- Secrets: environment variables only; `.env` is git-ignored and `.env.example`
  holds placeholders only.
"""

    def _backend_main(self, plan: BuildPlan) -> str:
        routes = ", ".join(f'"{p.lower().replace(" ", "-")}"' for p in plan.pages[:8])
        return f'''"""{plan.label} API - scaffolded by Jarvis.

Nothing here talks to a payment provider. Wire that up only after the provider is
chosen and its keys are in the environment (never in this file).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings

PAGES = [{routes}]


class Settings(BaseSettings):
    database_url: str = ""
    session_secret: str = ""
    cors_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


settings = Settings()
app = FastAPI(title="{plan.name}", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {{"status": "ok"}}


@app.get("/api/pages")
def pages() -> dict[str, list[str]]:
    return {{"pages": PAGES}}
'''

    def _backend_db(self) -> str:
        return '''"""Database access. Connection string comes from the environment only."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def connect() -> Iterator[Any]:
    import psycopg

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set - refusing to guess a connection string")
    with psycopg.connect(url) as conn:
        yield conn
'''

    def _backend_auth(self) -> str:
        return '''"""Authentication helpers.

Passwords are hashed with argon2. Never store, log or return a plaintext password,
and never reuse this file's session secret - generate your own.
"""

from __future__ import annotations

import hashlib
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    """argon2 when available, otherwise refuse rather than fall back to something weak."""
    try:
        from argon2 import PasswordHasher
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("argon2-cffi is required; refusing to hash with a weak algorithm") from exc
    return PasswordHasher().hash(password)


def verify_password(password: str, hashed: str) -> bool:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError

    try:
        return PasswordHasher().verify(hashed, password)
    except VerifyMismatchError:
        return False


def constant_time_equals(a: str, b: str) -> bool:
    return hashlib.sha256(a.encode()).digest() == hashlib.sha256(b.encode()).digest()
'''

    def _package_json(self, plan: BuildPlan) -> str:
        return f'''{{
  "name": "{plan.name}-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {{
    "dev": "vite --host 0.0.0.0",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host 0.0.0.0"
  }},
  "dependencies": {{
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }},
  "devDependencies": {{
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.3",
    "vite": "^5.3.4"
  }}
}}
'''

    def _frontend_main(self, plan: BuildPlan) -> str:
        return '''import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const container = document.getElementById("root");
if (!container) throw new Error("missing #root element");
createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
'''

    def _frontend_app(self, plan: BuildPlan) -> str:
        return f'''import {{ useEffect, useState }} from "react";

/**
 * {plan.label} - scaffolded by Jarvis.
 * Pages are driven from the backend so the route list has one source of truth.
 */
export default function App() {{
  const [pages, setPages] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {{
    fetch("/api/pages")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data) => setPages(data.pages ?? []))
      .catch((e) => setError(e.message));
  }}, []);

  return (
    <main style={{{{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "4rem auto", padding: "0 1rem" }}}}>
      <h1>{plan.label}</h1>
      {{error ? <p role="alert">Could not load pages: {{error}}</p> : (
        <ul>{{pages.map((p) => <li key={{p}}>{{p}}</li>)}}</ul>
      )}}
    </main>
  );
}}
'''

    def _index_html(self, plan: BuildPlan) -> str:
        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{plan.label}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""

    def _ci(self) -> str:
        return """name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r backend/requirements.txt pytest ruff
      - run: ruff check backend
      - run: pytest -q
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: npm ci --prefix frontend || npm install --prefix frontend
      - run: npm run build --prefix frontend
"""


def supported_categories() -> Iterable[str]:
    return sorted(CATEGORIES)
