# BowserAuto — AI Browser Automation Workspace

A production-oriented, multi-tenant web application in which an AI agent drives **real Chromium
browsers** through a worker fleet — turning scripts into AI-generated video scenes on third-party
platforms (Google Flow, Runway, Kling, Luma, Hailuo, Pika, PixVerse, Vidu, ChatGPT, Gemini, Arena,
any website via the generic agent) while you watch the live browser, take over whenever a login,
CAPTCHA or verification appears, and hand control back to the agent.

The UI is a JARVIS-style HUD with an assistant called **AVINASH** (reactor core, console, system monitor, voice/wake-word,
customisable colours and assistant name) that ports the *functionality* of
[FatihMakes/Mark-LV](https://github.com/FatihMakes/Mark-LV) to the web (see
[docs/ASSISTANT.md](docs/ASSISTANT.md) for the feature mapping).

```
 browser ──► frontend (Next.js + single-origin proxy :3000)
                │  /api, /api/ws, /mock-platform
                ▼
             backend (FastAPI) ──► PostgreSQL (tasks, scenes, sessions, files, audit…)
                │   ▲                Redis (event bus + worker wake-ups)
   task queue   ▼   │ events / CDP frames
          browser-worker(s) ──► Playwright ──► Chromium (persistent browser profiles; see storage-encryption caveat in the audit)
                                                  └──► Google Flow / Runway / … / Mock Video Generator
```

---

## Feature overview

| Area | What is implemented |
| --- | --- |
| AI task agent | Self-describing browser/filesystem/task tools and a searchable Skills catalog, provider-neutral planner, encrypted org keys, health test, and deterministic heuristic fallback; `task.undo` only compensates an unchanged explicit navigation (not external side effects) |
| Browser automation | Playwright/Chromium driven by **workers** (never inside the API), persistent per-session profiles (mode 0700 on the shared data volume; live files are not application-encrypted, while Fernet covers stored state snapshots), CDP screencast streamed over WebSocket (not repeated screenshots), URL/title/action indicators, tabs, uploads |
| Human takeover | **TAKE OVER BROWSER** pauses the agent and gives you click/type/scroll/paste/upload control in the live viewer; **RETURN CONTROL TO AI** re-inspects page state and resumes. CAPTCHA/MFA/logins are never bypassed |
| Mobile pairing | Settings → Devices generates a two-minute QR pairing request; the signed-in device approves before the phone receives its own revocable HttpOnly app session |
| Agent policy & approvals | Task-level browser-domain allowlists; generic-agent and adapter-native browser writes pause for authenticated approve/reject decisions by default; `confirm_all` also gates browser state changes; sensitive input values are hidden in approval previews |
| Scheduled workflows | Persistent one-time/recurring generic browser tasks with exact host allowlists, approval modes, pause/resume/delete, idempotent worker dispatch, and no overlapping runs |
| Task queue | DB-persisted queue with priority (1–10), reorder, pause/resume/stop/retry/skip, STOP EVERYTHING, atomic claims, heartbeats and crash recovery (queue survives restarts) |
| Script → scenes | LLM or heuristic script analysis into editable scenes (reorder, duplicate, delete, edit prompt/visual/camera/dialogue, per-scene platform, reference images, regenerate prompt) |
| Scene generation | open platform → verify login → upload references → prompt → configure → generate → **smart state detection** (`QUEUED/GENERATING/PROCESSING/COMPLETED/DOWNLOAD_READY/DOWNLOADING/SUCCESS/FAILED/RETRYING/BLOCKED/WAITING_FOR_HUMAN`) with configurable timeouts, no blind sleeps → download → verify → rename `scene_001.mp4` → metadata |
| Retry & recovery | Configurable retries with backoff, non-retryable error detection, then pause with **Retry / Edit / Skip / Take over**; "no silent task death" |
| Platform adapters | Plugin registry under `backend/app/platforms/` (13 adapters + `mock`), generic AI agent fallback, per-org enable/disable, verified-selector flag |
| Files | `/projects/{id}/{script,references,scenes,generated,final,logs}`, signed expiring download/preview URLs, disk-backed streamed ZIP export, per-file and per-organization quotas |
| Realtime | WebSocket + SSE event stream (task/scene/agent/browser/session/download/human/file/notification events), screenshot history, agent decision panel |
| Multi-tenancy & security | Organisations, roles (owner/admin/member/viewer), Argon2 passwords, HttpOnly cookie sessions + CSRF, rate limiting, audit log, Fernet-encrypted secrets, tenant-scoped queries, signed URLs, WS auth, production start-up guards |
| Notifications | In-app + native browser notifications; email / Telegram / generic webhook (WhatsApp gateways, Slack) through env vars |
| Observability | Structured logs (`timestamp, task_id, session_id, action, result, duration, error`), request IDs, activity + audit tables, health endpoint, worker liveness files |
| Cost & limits | Org-level limits (task duration, retries, scenes, concurrent tasks, sessions, file size, storage quota, timeouts) with server ceilings, 80% storage alerts, and pre-run estimates |
| Templates | Built-in (AI Video Generation, Image Generation, Research, Data Entry, Website Testing, Content Creation, Browser Workflow) + custom |
| Dev mode | Built-in **Mock Video Generator** (`/mock-platform`) with configurable speed, failure rate, login and human-check simulation for end-to-end runs without AI credits |
| Tests | 131 backend tests (including pairing/approval flows and mocked OpenAI-compatible, Gemini, Ollama and Anthropic transports) on a fake browser backend; UI snapshot script; API/UI smoke scripts |

---

## Quick start (local development)

Requirements: Python 3.11+, Node 22+. No Docker/Postgres/Redis needed (SQLite + in-memory bus +
embedded worker).

```bash
./scripts/dev_setup.sh          # venv, deps, backend/.env, migrations, npm install, Chromium
./scripts/dev_api.sh            # API + embedded worker on :8000  (http://localhost:8000/api/docs)
cd frontend && npm run dev:proxy   # UI on http://localhost:3000 (proxies /api to :8000)
```

1. Open http://localhost:3000, create the first account (it becomes the organisation owner).
2. **Browser Sessions → New session** (platform *Mock Video Generator* or any real platform), log
   in by hand inside the live viewer, click **Mark logged in**.
3. **Projects → New project**, paste a script, *Analyse into scenes*, review the scenes, press
   **Generate**. Watch the live browser, pause/take over/return control at any time.
4. Or ask AVINASH in the console: `run: open news.ycombinator.com and read the top 5 titles`.

Smoke scripts (API must be running):

```bash
.venv/bin/python scripts/smoke_e2e.py --speed fast         # project → scenes → generation → files → export
.venv/bin/python scripts/smoke_controls.py                 # pause / resume / take over / release / stop
.venv/bin/python scripts/ui_snapshots.py --out /tmp/ui     # logs into the UI with Chromium, screenshots every page, fails on console errors
```

If the sandbox/VM is reset, re-run `./scripts/dev_setup.sh` — it repairs the venv, Chromium
download, `.env` and migrations idempotently.

---

## Production deployment (Docker Compose)

```bash
cp .env.example .env
#   SECRET_KEY:      python -c "import secrets; print(secrets.token_urlsafe(48))"
#   ENCRYPTION_KEY:  cd backend && python -m app.core.security gen-key
#   POSTGRES_PASSWORD, PUBLIC_BASE_URL, ALLOWED_ORIGINS, TRUSTED_HOSTS, COOKIE_SECURE=true behind HTTPS
#   ALLOW_REGISTRATION=false (first-owner bootstrap works until the first user exists)
docker compose up -d --build
docker compose --profile proxy up -d      # optional nginx front door (deploy/nginx.conf)
```

Services: `frontend`, `backend`, `worker`, `browser-worker` (scale with `BROWSER_WORKERS=n`),
`postgres`, `redis`, optional `nginx` — all with health checks. Production refuses to start with
the dev secret, without `ENCRYPTION_KEY`, or on SQLite. See
[docs/OPERATIONS.md](docs/OPERATIONS.md) for environment separation (dev/staging/prod), scaling,
backups and upgrades.

---

## Repository layout

```
backend/
  app/
    api/            FastAPI routers (auth, tasks, schedules, projects, sessions, files, platforms,
                    templates, activity, audit, notifications, settings, dashboard, assistant, realtime)
    agent/          BrowserAgent, tool registry (browser.* / filesystem.* / task.*), LLM clients,
                    heuristic planner
    browser/        Playwright driver (CDP screencast, input injection, downloads), fake driver,
                    launcher (bundled Chromium support)
    core/           settings, logging, security (Argon2, Fernet, signed URLs), database, errors,
                    rate limiting, dependencies, system metrics
    models/         SQLAlchemy multi-tenant, identity and workflow models
    platforms/      PlatformAdapter base + 13 adapters + mock; registry
    queue/          control tokens (pause/resume/stop/takeover/…)
    realtime/       event bus (in-memory / Redis), event catalogue, WebSocket + SSE
    schemas/        Pydantic request/response models
    services/       business logic (tasks, schedules, projects, sessions, files, dashboard,
                    assistant, notifications, org settings, templates)
    workers/        Worker loop, TaskExecutor, SessionHost, schedule dispatcher, health check
    mock_platform/  the Mock Video Generator web app
  alembic/          migrations
  tests/            pytest suite (fake browser backend, no real AI calls)
frontend/
  src/app/          Next.js App Router pages (dashboard, new-task, projects, sessions, tasks,
                    schedules, files, platforms, skills, templates, activity, settings, pair, auth)
  src/components/   hud/ (AVINASH core, console, sys monitor, customise, memory), browser/
                    (live viewer), tasks/, scenes/, layout/, ui/
  src/lib/          typed API client, realtime client, stores, theme, voice, utils
  server.mjs        single-origin proxy (dev + prod)
deploy/nginx.conf   reverse proxy example
docker-compose.yml  full stack
scripts/            dev_setup, dev_api, fetch_chromium, backup (GPG encrypted), smoke_e2e, smoke_controls, ui_snapshots
docs/               ARCHITECTURE, SECURITY, PLATFORM_ADAPTERS, OPERATIONS, API, TESTING, ASSISTANT,
                    PRODUCTION_READINESS
```

---

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data model, task lifecycle, realtime, storage
* [docs/PLATFORM_ADAPTERS.md](docs/PLATFORM_ADAPTERS.md) — how adapters work and how to add one
* [docs/SECURITY.md](docs/SECURITY.md) — auth, tenancy, secrets, CSRF/XSS, rate limits, what is never done
* [docs/API.md](docs/API.md) — REST/WebSocket surface (full OpenAPI at `/api/docs`)
* [docs/LLM_PROVIDERS.md](docs/LLM_PROVIDERS.md) — OpenAI-compatible, Gemini, Ollama, Anthropic and production provider configuration
* [docs/OPERATIONS.md](docs/OPERATIONS.md) — environments, deployment, scaling, backups, troubleshooting
* [docs/TESTING.md](docs/TESTING.md) — test strategy and commands
* [docs/ASSISTANT.md](docs/ASSISTANT.md) — AVINASH assistant: HUD, voice, memory, customisation; Mark-LV feature mapping
* [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) — implementation phases 1–15 and prior verification notes
* [docs/PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md) — project/repository audit findings, remediation status, and limitations
* [docs/ROADMAP_STATUS.md](docs/ROADMAP_STATUS.md) — current Phase 0–3 implementation and remaining gaps

## Non-negotiables baked into the design

* The agent never bypasses CAPTCHA, MFA, logins, payments or anti-bot systems — it pauses and asks you.
* Passwords for third-party platforms are never requested or stored. Raw browser cookie stores are not sent to the frontend; browser URLs are sanitized before persistence, events, API display, and agent prompts, while secret-bearing start URLs needed by workers are encrypted at rest.
* No fixed `sleep(30)` waits — state is detected from the page with configurable timeouts.
* One scene at a time unless parallel generation is explicitly enabled for a project.
* Browser automation runs in workers, never inside the API process.
* Every failure surfaces as a task state with recovery actions; nothing dies silently.
