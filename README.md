# Jarvis — AI Home Designer

An honest, complete AI house-design web app: describe a plot and a household, get
five real design concepts, edit the plan in 2D, walk through the actual generated
house in first-person 3D, design interiors/exteriors live, see a parametric budget,
and export plans, reports, schedules and a GLB model.

> Built on top of the original Jarvis idea — an assistant that talks to you as
> naturally as a friend and gives human-like solutions. That spirit lives in the
> design assistant: plain language in, real numbers out, and it says "I don't
> know — ask a professional" instead of making things up.

## What it does

- **Progressive setup wizard** — plot (ft/m, shape, road side, setbacks), room
  programme (8 Indian presets + 12 toggles), household & style, optional photos.
  Image uploads are always optional; nothing requires them.
- **Honest planning engine** (deterministic, no LLM geometry): 5 concepts
  (compact, family comfort, courtyard, frontage/shop, terrace) with metrics,
  assumptions, trade-offs, setbacks and Vastu guidance kept strictly separate
  from code checks.
- **2D editor** — drag/resize/rename rooms on a real grid, drag doors and windows
  along walls, move furniture, undo/redo, reset-to-saved, live areas, and an AI
  command box with a preview → approve/reject flow that refuses impossible edits
  with an explanation instead of silently failing.
- **Structured 3D builder** — walls with real openings (never a solid box),
  stairs, railings, skirting, window frames/sills, counters, furniture, boundary
  wall with a gate, garden, driveway — all generated from the project document.
  Eight view modes: exterior orbit, top, dollhouse, single room, three
  elevations, and first-person walkthrough.
- **Immersive walkthrough** (the big one):
  - Real first-person controller at adjustable eye height (~1.65 m), pointer-lock
    mouse look, WASD/arrows, sprint, two styles (Realistic Walk / Free Explore),
    collision against the *actual* wall geometry (window-height walls block you,
    door gaps don't), wall sliding, gravity with continuous stair ramps, camera
    bob toggle, comfort settings, instant exit.
  - Complete outside→inside journey: street spawn → boundary gate → front door
    (hinge-correct, interactive with E) → rooms via real openings → stairs →
    upper floor → back out.
  - Centre-ray interactions: doors, windows (sliding/casement), furniture
    inspection with dims/materials, measure mode between clicked points,
    room-inspection panel (area, ceiling, finishes, openings).
  - Lighting: day/evening/night presets, sun angle, warm/neutral/cool
    temperature, exposure, interior ceiling lights at night, shadows.
  - Mini-map from the actual floor plan (position + view direction), room
    directory that flags inaccessible rooms and explains plan fixes instead of
    teleporting, guided tours (street→entrance, living/dining/kitchen, bedrooms,
    stairs, full tour) computed from a walkable nav-graph — a tour is refused
    when no valid route exists.
  - Live design changes while walking (materials from the Interior page),
    before/after comparison overlay, quality presets (High/Balanced/
    Performance/Mobile).
- **Interior & exterior studios** — 11 styles, material/colour panels that
  re-render 3D surfaces immediately, room-level finish overrides, façade/roof/
  boundary/landscape/lighting controls with elevation previews.
- **Budget planner** — 14 categories with low/high ranges, assumptions and rate
  sources shown per line, finish-level switch, manual local rate entry (marked
  verified), concept cost comparison, CSV export. Rates are parametric
  placeholders, never invented "current market prices".
- **Assistant chat** — answers only from the project's real data; when an AI
  provider/key is configured it can go further, otherwise the built-in local
  engine keeps everything working offline.
- **History & sharing** — autosave, versions with restore, activity log,
  opt-in read-only share links.
- **Exports** — plan PNG (all floors, dims, scale bar, disclaimers), plan SVG,
  plans PDF, design report PDF, GLB 3D model, documented JSON, room/material/
  furniture/budget CSVs. All owner-checked; downloads work via `?token=`.

## Architecture

```
backend/   FastAPI + WebSocket (SQLite → PostgreSQL switchable via DATABASE_URL)
  app/services/designer.py    layout engine: programmes, concepts, placement
  app/services/engine.py      walls with openings, stairs, furniture, site
  app/services/validation.py  code checks + honest severity/disclaimers
  app/services/budget.py      14-category parametric estimate + comparisons
  app/services/nl.py          natural-language command parser (preview/commit)
  app/services/ai.py          provider layer: local fallback → OpenAI/Anthropic/
                              Gemini; keys server-side only; vision analysis is
                              pixel-derived facts + disclaimers
  app/export/                 PNG/SVG/PDF/CSV/GLB/JSON exporters
frontend/  React + TypeScript + Vite + Tailwind + zustand
  src/lib/model/scene.ts      plan → render pieces + collision solids
  src/lib/model/collision.ts  character controller: circle-vs-AABB, groundAt()
  src/lib/model/navgraph.ts   walkable graph, tours, inaccessible-room detection
  src/three/FirstPerson.tsx   pointer-lock controller + interaction raycast
  src/three/HouseModel.tsx    geometry from pieces, doors/windows/furniture
  src/three/Lighting.tsx      presets, sun, interior lights, tone mapping
  src/pages/                  11 functional pages (dashboard → settings)
```

The LLM never generates geometry or prices. Every number comes from the
deterministic engine or the parametric tables; every export says so.

## Run it

```bash
# backend (port 8000)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# frontend (port 5173, proxies /api and /ws to the backend)
cd frontend && npm install && npm run dev
```

Optional: `DATABASE_URL=postgresql://…` for PostgreSQL; `AUTH_ENABLED=true` +
`JWT_SECRET=…` for multi-user mode (default is single local user). Provider keys
are configured in-app under Settings (stored server-side, file mode 600).

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q          # 20 engine/API/export tests
cd frontend && npm run test:walk                     # 13 walkthrough acceptance
                                                     # tests (collision, doors,
                                                     # stairs, nav-graph, tours)
```

Frontend checks: `tsc --noEmit` and `npm run build` both pass clean.

## Safety & honesty

- Everything is a **preliminary design aid**, not construction documentation.
  Plans, PDFs, GLBs and the UI repeat this; regulatory requirements vary by
  municipality and are stated as uncertain, not as facts.
- Vastu suggestions are labelled as traditional guidance, separate from code.
- Budget numbers are ranges with visible assumptions; user-entered rates are
  marked "verified", engine defaults are marked "not verified".
- Impossible edits are rejected with reasons; blocked rooms are flagged with
  suggested plan fixes; tours refuse to walk through walls.
