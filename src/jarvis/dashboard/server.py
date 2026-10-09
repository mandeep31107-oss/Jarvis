"""The dashboard (spec section 29).

Fourteen sections, served from the standard library, **read-only by default**.

The read-only default is the point. A dashboard that can act is a second entry
point into the system, and a second entry point is a second place to get the
permission model wrong. So the UI reports; acting stays in the REPL and the CLI,
which already route every action through ``Supervisor.propose()``. When
``allow_actions`` is turned on, actions still go through the same runtime and
the same policy engine - there is no dashboard-specific path.

Nothing here prints a secret: configuration comes from
``Settings.redacted_view()`` and audit records are redacted on the way in.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from jarvis.runtime import Runtime
from jarvis.version import PHASE, PHASE_NAME, __version__


class Dashboard:
    """Assembles the fourteen sections from a live runtime."""

    #: The section order the UI renders, and the contract callers can rely on.
    SECTIONS: tuple[str, ...] = (
        "system",
        "agents",
        "tasks",
        "risk",
        "audit",
        "memory",
        "knowledge",
        "compliance",
        "platform_terms",
        "models",
        "host",
        "revenue",
        "security",
        "activity",
    )

    def __init__(self, runtime: Runtime, *, audit_limit: int = 25) -> None:
        self.runtime = runtime
        self.audit_limit = audit_limit

    # -------------------------------------------------------------- sections
    def snapshot(self) -> dict[str, Any]:
        """All fourteen sections, in one JSON-safe dict."""
        status = self.runtime.status()
        data: dict[str, Any] = {}
        for name in self.SECTIONS:
            builder: Callable[[], Any] = getattr(self, f"_section_{name}")
            data[name] = builder()
        data["_meta"] = {
            "generated_from": "runtime.status",
            "phase": PHASE,
            "phase_name": PHASE_NAME,
            "version": __version__,
            "source_status_keys": sorted(status),
        }
        return data

    def _section_system(self) -> dict[str, Any]:
        status = self.runtime.status()
        return {
            "version": status["version"],
            "phase": status["phase"],
            "phase_name": status["phase_name"],
            "run_state": status["run_state"],
            "settings": status["settings"],
            # Always-active state belongs here, not hidden in the raw status: the
            # first question about a background loop is whether it is running.
            "loop": status.get("loop", {"enabled": False}),
        }

    def _section_agents(self) -> dict[str, Any]:
        agents = self.runtime.status()["agents"]
        return {
            "agents": agents,
            "ready": sorted(n for n, a in agents.items() if a["available"]),
            "unavailable": {
                n: a.get("reason", "unknown")
                for n, a in agents.items()
                if not a["available"]
            },
            "ready_count": sum(1 for a in agents.values() if a["available"]),
            "total": len(agents),
        }

    def _section_tasks(self) -> dict[str, Any]:
        return {
            "summary": self.runtime.status()["tasks"],
            "queue": self.runtime.tasks.plan_view(),
            "run_state": self.runtime.run_state,
        }

    def _section_risk(self) -> dict[str, Any]:
        from jarvis.core.risk import risk_matrix_summary

        return {
            "policy": self.runtime.status()["policy"],
            "matrix": risk_matrix_summary(self.runtime.risk),
        }

    def _section_audit(self) -> dict[str, Any]:
        summary = self.runtime.status()["audit"]
        return {
            "summary": summary,
            "recent": self.runtime.audit.entries(limit=self.audit_limit),
        }

    def _section_memory(self) -> dict[str, Any]:
        return self.runtime.status()["memory"]

    def _section_knowledge(self) -> dict[str, Any]:
        stats = self.runtime.knowledge.stats()
        return {
            "stats": stats,
            "needs_revalidation": len(self.runtime.knowledge.needs_revalidation()),
        }

    def _section_compliance(self) -> dict[str, Any]:
        return self.runtime.status()["compliance"]

    def _section_platform_terms(self) -> dict[str, Any]:
        return self.runtime.terms.stats()

    def _section_models(self) -> dict[str, Any]:
        return self.runtime.status()["models"]

    def _section_host(self) -> dict[str, Any]:
        host = self.runtime.host
        describe = self.runtime.status()["host"]
        describe = dict(describe)
        describe["consented_devices"] = sorted(host.consented_devices)
        return describe

    def _section_revenue(self) -> dict[str, Any]:
        """What the revenue agent can report without inventing anything.

        Jarvis does not track live opportunities it has not been given, so this
        section says what it knows rather than showing an empty pipeline as
        though it were a healthy one.
        """
        memory = self.runtime.memory
        decisions = 0
        if memory is not None and memory.enabled:
            try:
                decisions = memory.counts().get("decision", 0)
            except Exception:  # noqa: BLE001 - a count failure is not fatal to the UI
                decisions = 0
        return {
            "decisions_recorded": decisions,
            "note": (
                "Opportunities are analysed on request. Jarvis does not track a "
                "pipeline it was never given, and will not show an empty one as "
                "though it were healthy."
            ),
        }

    def _section_security(self) -> dict[str, Any]:
        from jarvis.core.policy import FORBIDDEN

        return {
            "forbidden_actions": sorted(entry[0] for entry in FORBIDDEN),
            "forbidden_count": len(FORBIDDEN),
            "secrets_in_code": "secrets are read from the environment only",
            "audit_deletable": False,
            "note": (
                "Hard denials cannot be approved by any approver or any actor. "
                "The audit log has no delete method."
            ),
        }

    def _section_activity(self) -> dict[str, Any]:
        """Recent events as plain dicts.

        ``EventBus.recent`` returns dataclass instances; serialising those gives
        a repr string, which is not something a browser can render.
        """
        events = [
            {"topic": e.topic, "at": e.at, "payload": e.payload}
            for e in self.runtime.events.recent(limit=40)
        ]
        return {
            "events": events,
            "recent_activity": self.runtime.status()["recent_activity"],
        }


class _Handler(BaseHTTPRequestHandler):
    dashboard: Dashboard
    allow_actions: bool

    server_version = f"JarvisDashboard/{__version__}"

    # ------------------------------------------------------------ responses
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The dashboard is same-origin only. It exposes internal state, so it
        # must not be embeddable or readable from another site.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(
            status,
            json.dumps(payload, indent=2, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    # --------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            try:
                self._json(200, self.dashboard.snapshot())
            except Exception as exc:  # noqa: BLE001 - report, do not crash the server
                self._json(500, {"error": f"could not build the snapshot: {exc}"})
        elif path == "/api/health":
            self._json(200, {"ok": True, "run_state": self.dashboard.runtime.run_state})
        else:
            self._json(404, {"error": f"no such route: {path}"})

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        """Answer HEAD without a body. Health probes use it."""
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
        elif path in ("/api/status", "/api/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        """Read-only unless the operator explicitly allowed actions.

        Even then, the request goes through the runtime, which means the risk
        engine and the policy engine still decide. The dashboard never approves
        anything on the user's behalf.
        """
        path = self.path.split("?", 1)[0]
        if path != "/api/request":
            self._json(404, {"error": f"no such route: {path}"})
            return
        if not self.allow_actions:
            self._json(
                403,
                {
                    "error": "the dashboard is read-only",
                    "detail": (
                        "Actions are taken from the REPL or the CLI, where each one is "
                        "risk-assessed and, above LOW, asks for approval."
                    ),
                },
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._json(400, {"error": f"invalid JSON: {exc}"})
            return
        text = str(payload.get("text", "")).strip()
        if not text:
            self._json(400, {"error": "no text supplied"})
            return
        execution = self.dashboard.runtime.handle(text)
        self._json(
            200 if execution.ok else 422,
            {
                "ok": execution.ok,
                "error": execution.error,
                "summary": execution.result.summary if execution.result else "",
                "verified": execution.verified,
            },
        )

    def log_message(self, *_args: Any) -> None:
        pass  # keep the console clean; the audit log is the record


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis dashboard</title>
<style>
  :root { --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e6e8ec;
          --dim:#98a1b3; --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --acc:#60a5fa; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { padding:18px 24px; border-bottom:1px solid var(--line);
           display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; letter-spacing:.3px; }
  header .v { color:var(--dim); font-size:12px; }
  .state { margin-left:auto; padding:3px 10px; border-radius:999px; font-size:12px;
           border:1px solid var(--line); }
  .state.running { color:var(--ok); border-color:#1d3a2a; }
  .state.paused { color:var(--warn); border-color:#3d3220; }
  .state.emergency_stopped { color:var(--bad); border-color:#4a2020; background:#2a1414; }
  main { padding:20px 24px 60px; display:grid; gap:14px;
         grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); }
  section { background:var(--card); border:1px solid var(--line); border-radius:10px;
            padding:14px 16px; overflow:hidden; }
  section h2 { font-size:11px; text-transform:uppercase; letter-spacing:.9px;
               color:var(--dim); margin:0 0 10px; }
  .kv { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; font-size:13px; }
  .kv dt { color:var(--dim); }
  .kv dd { margin:0; word-break:break-word; }
  .big { font-size:22px; font-weight:600; }
  .pill { display:inline-block; padding:1px 7px; border-radius:999px; font-size:11px;
          border:1px solid var(--line); margin:2px 3px 2px 0; color:var(--dim); }
  .pill.ok { color:var(--ok); border-color:#1d3a2a; }
  .pill.bad { color:var(--bad); border-color:#4a2020; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th,td { text-align:left; padding:3px 6px; border-bottom:1px solid var(--line);
          vertical-align:top; }
  th { color:var(--dim); font-weight:500; }
  .scroll { max-height:230px; overflow:auto; }
  .dim { color:var(--dim); }
  .note { font-size:12px; color:var(--dim); margin-top:8px; }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
  .bar { height:6px; background:#222734; border-radius:3px; overflow:hidden; margin-top:6px; }
  .bar > i { display:block; height:100%; background:var(--acc); }
</style>
</head>
<body>
<header>
  <h1>Jarvis</h1>
  <span class="v" id="version">loading…</span>
  <span class="state" id="state">—</span>
</header>
<main id="grid"></main>
<script>
const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const kv = o => '<dl class="kv">' + Object.entries(o)
  .map(([k,v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + '</dl>';

function card(title, html) {
  return `<section><h2>${esc(title)}</h2>${html}</section>`;
}

const R = {
  system(d) {
    return card('1 · System', kv({
      version: d.version, phase: `${d.phase} — ${d.phase_name}`,
      run_state: d.run_state, autonomy: d.settings.autonomy,
      jurisdiction: d.settings.jurisdiction,
      fetch_enabled: d.settings.fetch_enabled ? 'on' : 'off',
      computer_use: d.settings.computer_use ? 'on' : 'off',
      always_active: (d.loop && d.loop.enabled === false) ? 'not configured'
        : (d.loop.running ? `running (${d.loop.iterations} ticks, ${d.loop.tasks_processed} tasks)`
                          : 'stopped'),
    }));
  },
  agents(d) {
    const rows = Object.entries(d.agents).map(([n,a]) =>
      `<tr><td>${esc(n)}</td><td>${a.available
        ? '<span class="pill ok">ready</span>'
        : `<span class="pill bad">${esc(a.reason||'unavailable')}</span>`}</td></tr>`).join('');
    return card(`2 · Agents (${d.ready_count}/${d.total})`,
      `<div class="bar"><i style="width:${(100*d.ready_count/Math.max(1,d.total)).toFixed(0)}%"></i></div>
       <div class="scroll"><table>${rows}</table></div>`);
  },
  tasks(d) {
    const q = d.queue || [];
    const rows = q.slice(0,20).map(t =>
      `<tr><td>${esc(t.state||'')}</td><td>${esc(t.title||t.id||'')}</td>
       <td class="dim">${esc(t.score ?? '')}</td></tr>`).join('');
    return card('3 · Tasks', kv(d.summary) +
      (rows ? `<div class="scroll"><table><tr><th>state</th><th>task</th><th>score</th></tr>${rows}</table></div>`
            : '<div class="note">queue empty</div>'));
  },
  risk(d) {
    const m = d.matrix || {};
    const rows = Object.entries(m).map(([lvl, info]) =>
      `<tr><td>${esc(lvl)}</td><td>${esc(typeof info === 'object'
        ? Object.keys(info).length + ' rule(s)' : info)}</td></tr>`).join('');
    return card('4 · Risk & permissions',
      kv({autonomy: d.policy.autonomy, auto_ceiling: d.policy.auto_ceiling,
          approver: d.policy.approver}) +
      `<div class="scroll"><table>${rows}</table></div>`);
  },
  audit(d) {
    const recs = d.recent || [];
    const rows = recs.slice(0,25).map(r =>
      `<tr><td class="dim">${esc((r.at||'').slice(11,19))}</td>
       <td>${esc(r.action||'')}</td><td>${esc(r.result||'')}</td>
       <td class="dim">${esc(r.risk||'')}</td></tr>`).join('');
    return card(`5 · Audit log (${d.summary.total ?? 0})`,
      kv({by_risk: JSON.stringify(d.summary.by_risk||{}),
          with_errors: d.summary.with_errors ?? 0, deletable: 'no — by design'}) +
      `<div class="scroll"><table>${rows}</table></div>`);
  },
  memory(d) {
    const counts = Object.fromEntries(Object.entries(d)
      .filter(([k]) => !['enabled','total'].includes(k)));
    return card(`6 · Memory (${d.total ?? 0})`, kv(counts) +
      (d.enabled === false ? '<div class="note">memory disabled</div>' : ''));
  },
  knowledge(d) {
    return card('7 · Knowledge base', kv({...d.stats, needs_revalidation: d.needs_revalidation}));
  },
  compliance(d) {
    return card('8 · Compliance', kv(d) +
      '<div class="note">Information and risk analysis, not a substitute for qualified legal or tax advice.</div>');
  },
  platform_terms(d) {
    return card('9 · Platform terms', kv(d));
  },
  models(d) {
    const rows = Object.entries(d.models||{}).map(([m,p]) =>
      `<tr><td>${esc(m)}</td><td class="dim">${esc(p)}</td></tr>`).join('');
    return card(`10 · Models (${(d.available||[]).length})`,
      kv({last_choice: d.last_choice ?? 'none',
          missing_providers: (d.missing_providers||[]).join(', ') || 'none'}) +
      `<div class="scroll"><table>${rows}</table></div>`);
  },
  host(d) {
    return card('11 · Host & privacy',
      kv({platform: d.platform, system: `${d.system} ${d.release}`,
          machine: d.machine, privacy: d.privacy_mode || d.privacy,
          computer_use: d.computer_use_enabled ? 'on' : 'off',
          consented: (d.consented_devices||[]).join(', ') || 'none'}));
  },
  revenue(d) {
    return card('12 · Revenue',
      `<div class="big">${esc(d.decisions_recorded)}</div>
       <div class="dim">decision(s) recorded</div>
       <div class="note">${esc(d.note)}</div>`);
  },
  security(d) {
    const pills = (d.forbidden_actions||[]).map(a => `<span class="pill bad">${esc(a)}</span>`).join('');
    return card(`13 · Security (${d.forbidden_count} hard denials)`,
      `<div>${pills}</div><div class="note">${esc(d.note)}</div>`);
  },
  activity(d) {
    const rows = (d.events||[]).slice(-20).reverse().map(e =>
      `<tr><td class="dim">${esc((e.at||'').slice(11,19))}</td><td>${esc(e.topic||'')}</td></tr>`).join('');
    return card('14 · Activity',
      rows ? `<div class="scroll"><table>${rows}</table></div>` : '<div class="note">no events yet</div>');
  },
};

async function refresh() {
  try {
    const r = await fetch('/api/status', {headers:{'Accept':'application/json'}});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    document.getElementById('version').textContent =
      `v${d.system.version} · phase ${d.system.phase}`;
    const st = document.getElementById('state');
    st.textContent = d.system.run_state;
    st.className = 'state ' + d.system.run_state;
    document.getElementById('grid').innerHTML =
      Object.keys(R).map(k => R[k](d[k] || {})).join('');
  } catch (e) {
    document.getElementById('version').textContent = 'offline: ' + e.message;
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def make_server(
    runtime: Runtime,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    allow_actions: bool = False,
    audit_limit: int = 25,
) -> ThreadingHTTPServer:
    """Build (but do not start) the dashboard server."""
    dashboard = Dashboard(runtime, audit_limit=audit_limit)

    handler = type(
        "BoundHandler",
        (_Handler,),
        {"dashboard": dashboard, "allow_actions": allow_actions},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(
    runtime: Runtime,
    *,
    host: str = "127.0.0.1",
    port: int = 8642,
    allow_actions: bool = False,
) -> ThreadingHTTPServer:
    """Start the dashboard on a daemon thread and return the server."""
    server = make_server(runtime, host=host, port=port, allow_actions=allow_actions)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


__all__ = ["Dashboard", "PAGE", "make_server", "serve"]
