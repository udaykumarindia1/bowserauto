# Architecture

## 1. Components

| Component | Technology | Responsibility |
| --- | --- | --- |
| **frontend** | Next.js 15 (App Router), React 19, TypeScript, Tailwind, shadcn-style primitives, TanStack Query, Zustand | AVINASH HUD (JARVIS-style), three-panel task workspace, live browser viewer, scene editor, all management pages. Talks only to the same origin (`/api`). |
| **frontend/server.mjs** | Node `http` + `http-proxy` | Single-origin front door: `/api`, `/api/ws`, `/mock-platform` → backend; everything else → Next. Keeps cookies first-party (no CORS), proxies WebSocket upgrades. In Docker it runs Next's standalone server. |
| **backend (API)** | FastAPI, SQLAlchemy 2 (async), Pydantic v2, Alembic | REST + WebSocket/SSE API, auth, tenancy, validation, queue control, signed URLs, mock platform. **Never launches browsers** in production (`EMBEDDED_WORKER=false`). |
| **worker** | same code base, `python -m app.workers.main` | Claims queued tasks, applies control tokens, housekeeping/crash recovery. `WORKER_CAPABILITIES=general`. |
| **browser-worker** | worker + Playwright + Chromium | Hosts persistent browser sessions (`SessionHost`), runs `TaskExecutor`, streams CDP frames, relays human input. `WORKER_CAPABILITIES=browser,general`; scale horizontally. |
| **PostgreSQL** | 16 | Source of truth for every entity, the task queue and audit trail. SQLite is used only for local development/tests. |
| **Redis** | 7 | Event bus (`org:{id}:events`, `session:{id}:frames|input|status`, `worker:wakeup`). Without Redis the API falls back to an in-memory bus and an embedded worker (single process dev mode). |

### Module layout (backend/app)

```
core/        config (pydantic-settings, env separation), logging (structured), security
             (Argon2, Fernet, HMAC-signed URLs, CSRF), database (async engine), errors,
             ratelimit, deps (auth/role guards), sysinfo
models/      21 SQLAlchemy tables — every tenant table carries organization_id
schemas/     Pydantic in/out models (metadata_ ↔ metadata aliasing)
api/         routers mounted under /api
services/    business logic (task_service, project_service, session_service, file_service,
             dashboard_service, assistant_service, notification_service, org_settings, templates)
queue/       ControlToken (pause/resume/stop/takeover/release/retry/skip) written by the API,
             applied by workers
realtime/    EventBus (InMemory/Redis), EVENT_TYPES catalogue, emit() (persists ActivityLog +
             publishes), WebSocket hub, SSE
browser/     BrowserDriver protocol, PlaywrightDriver (CDP screencast, input, downloads, tabs),
             FakeDriver (tests), launcher (bundled Chromium resolution)
agent/       BrowserAgent loop, tools (browser.*, filesystem.*, task.*), LLM clients,
             HeuristicPlanner
platforms/   PlatformAdapter base, chat_base, registry, 13 adapters + mock
workers/     Worker (claims, heartbeats, recovery, controls), TaskExecutor (scene workflow,
             retries, attention), SessionHost (one Chromium context per BrowserSession),
             healthcheck
notifications/ provider plug-ins (email, telegram, webhook) + in-app/browser via events
mock_platform/ FastAPI sub-app simulating a video generator (speed, fail_rate, login, human check)
```

## 2. Data model (multi-tenant)

```
User ──< OrganizationMember >── Organization ──< Project ──< Scene ──< GeneratedFile
  │                                   │             ├──< Character        ▲
  └──< AuthSession                    │             ├──< Asset            │
                                      ├──< Task ──< TaskStep              │
                                      │      ├──< AgentRun ──< AgentAction│
                                      │      ├──< Screenshot              │
                                      │      └──────────────── (produces) ┘
                                      ├──< BrowserSession (persistent profile dir + encrypted storage-state snapshot)
                                      ├──< Platform (per-org enable/overrides)
                                      ├──< ActivityLog, AuditLog, Notification, MemoryFact, Template
```

* Every tenant table has `organization_id`; all service queries filter by it (see
  `tests/test_auth.py::test_tenant_isolation`).
* JSON columns use attribute name `metadata_` (serialised as `metadata`).
* Enumerations: `TaskStatus` (draft, queued, running, paused, waiting_for_human,
  needs_attention, completed, failed, cancelled), `SceneStatus` (draft, queued, generating,
  processing, downloading, completed, failed, skipped, waiting_for_human), `GenerationState`
  (QUEUED, GENERATING, PROCESSING, COMPLETED, DOWNLOAD_READY, DOWNLOADING, SUCCESS, FAILED,
  RETRYING, BLOCKED, WAITING_FOR_HUMAN, UNKNOWN), `BrowserSessionStatus` (closed, launching,
  ready, in_use, error, expired), roles owner/admin/member/viewer.

## 3. Task lifecycle

```
POST /api/tasks ─► draft ─(start)─► queued ─► running ─► completed
                                      ▲          │  │          failed / cancelled
   reorder / priority                 │          │  └─► paused ─(resume)─► running
                                      │          └─► waiting_for_human (take-over)
   crash recovery (stale heartbeat) ──┘          └─► needs_attention ─ Retry / Edit / Skip / Take over
```

1. **Claim** — workers run `UPDATE tasks SET worker_id=… WHERE status='queued' AND worker_id IS NULL`
   ordered by priority, queued_at (session affinity: a task bound to a session hosted by another
   live worker is left for that worker).
2. **Execute** — `TaskExecutor.run()`:
   * resolves platform adapter (`agent_mode`: auto → adapter if available else generic agent),
   * opens/attaches the `BrowserSession` through `SessionHost` (persistent live profile; not application-encrypted—see the Browser layer caveat),
   * for video tasks iterates scenes sequentially (`parallel_generation` must be explicitly enabled),
     each scene: `open_generation_tool → ensure_ready (login/human checks) → upload_references →
     enter_prompt → configure → start_generation → wait_for_completion (state detection loop with
     generation_timeout) → download_result (verify size/mime/sha256) → rename
     (`scene_{index:03d}.mp4`) → GeneratedFile + metadata`,
   * for generic tasks runs `BrowserAgent` with the tool registry until `task.complete`/`task.fail`
     /`task.needs_human`.
3. **Controls** — the API writes a `ControlToken` to `tasks.control` and wakes workers; the worker
   applies it between steps and inside wait loops (pause/resume/stop/takeover/release/retry/skip).
4. **Recovery** — exceptions are classified (`retryable`), retried with backoff up to
   `max_retries`, then the task enters `needs_attention` with `attention{kind, reason, scene_id,
   options}`; nothing is swallowed. Workers heartbeat; a worker that disappears has its tasks
   re-queued and sessions marked closed.
5. **Human takeover** — `takeover` pauses the agent, flags `human_control`, switches the session to
   interactive; the live viewer forwards mouse/keyboard/scroll/paste/upload events over
   `session:{id}:input`. `release` re-inspects URL/title/login state and resumes the current step.

## 4. Browser layer

* One Chromium **persistent context per BrowserSession** (`data/profiles/<id>`), launched by the
  worker that hosts it. The live Chromium profile contains cookies/local storage and is protected
  by POSIX directory permissions (mode `0700`); it is **not application-encrypted** by
  `ENCRYPTION_KEY`. Only the serialized storage-state snapshot is Fernet-encrypted. Operators
  must use encrypted host/volume storage and encrypted backups for at-rest protection. The API
  code does not intentionally read live profile files; note that Compose mounts the shared
  `app-data` volume into the API and worker containers, so this is not filesystem isolation from
  a compromised API process.
* Frames come from the **CDP `Page.screencast`** (JPEG, `STREAM_MAX_FPS`, `STREAM_QUALITY`,
  `STREAM_MAX_WIDTH`), published on `session:{id}:frames` and fanned out to WebSocket subscribers;
  a `/preview` endpoint serves the latest frame for clients without WebSockets.
* The driver API (`BrowserDriver`) is backend-agnostic: `PlaywrightDriver` for real browsers,
  `FakeDriver` for tests (`BROWSER_BACKEND=fake`).

## 5. Realtime

* `emit()` persists an `ActivityLog` row *and* publishes an event (`type`, `message`, `level`,
  `task_id`, `session_id`, `project_id`, `scene_id`, `payload`) on the org channel.
* Clients: `GET /api/ws` (WebSocket: `hello`, `ping/pong`, `subscribe_session`, `input`, org
  events) and `GET /api/events` (SSE). The frontend coalesces invalidations and shows
  notifications (toast + native browser notification).
* Event catalogue: see `backend/app/realtime/events.py` (`task.*`, `scene.*`, `agent.*`,
  `browser.*`, `session.*`, `download.*`, `human.*`, `file.*`, `project.updated`, `worker.status`,
  `notification`).

## 6. Storage

```
DATA_DIR/
  projects/<project_id>/{script,references,scenes,generated,final,logs}/
  profiles/<session_id>/           persistent Chromium profile (not application-encrypted)
  screenshots/<task_id>/
  workers/<host>-<pid>.alive       liveness files for container health checks
```

Downloads are streamed only through HMAC-signed, expiring URLs (`SIGNED_URL_TTL_SECONDS`), and
`GET /api/projects/{id}/export` zips the whole project folder.

## 7. Frontend

* `src/lib/api.ts` — typed client (all routes), `ApiError` with `{code, message, details}`.
* `src/lib/realtime.ts` — reconnecting WebSocket singleton (frames, status, actions, org events).
* `src/hooks/use-jarvis.ts` + `src/lib/jarvis-store.ts` — the assistant controller (commands,
  confirmations, voice, briefing, memory, HUD state).
* Pages are thin; shared building blocks live in `components/tasks` (controls, rows, attention
  banner, panels), `components/scenes` (editor), `components/browser/live-viewer.tsx`,
  `components/hud/*`.
* Query keys follow `["tasks"]`, `["task:<id>"]`, `["project:<id>"]`, `["sessions"]`,
  `["session:<id>"]`, `["files"]`, `["activity"]`, `["dashboard"]` … so realtime invalidation is
  one-to-one with API resources.
