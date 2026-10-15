# Production readiness

This document tracks the implementation phases and the final audit. "Verified" means exercised
in this repository by automated tests or the smoke scripts (real Chromium against the built-in
mock platform) — never against real AI platforms.

## Phases

| # | Phase | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Architecture & scaffolding (FastAPI, SQLAlchemy async, Alembic, Next.js, modular layout) | done | `docs/ARCHITECTURE.md`, repo layout |
| 2 | Auth, organisations, roles, audit | done | `tests/test_auth.py` |
| 3 | Projects, script → scene engine, assets, characters, templates | done | `tests/test_api.py`, `test_units.py` (parser) |
| 4 | Persistent task queue with controls, priorities, reorder, restart survival | done | `tests/test_queue.py` |
| 5 | Worker architecture (claiming, heartbeats, stale recovery, capabilities) | done | `test_tasks_survive_worker_restart`, `app/workers` |
| 6 | Browser layer (Playwright driver, profiles, encrypted storage state, fake driver) | done | `app/browser`, `test_takeover.py` |
| 7 | Live viewer (CDP screencast over WebSocket, input relay, tabs, uploads) | done | `realtime/`, `components/browser/live-viewer.tsx`, UI flow smoke |
| 8 | Human takeover / return control with re-inspection | done | `test_login_wall_takeover_and_release`, `scripts/smoke_controls.py` |
| 9 | Platform adapter plugin system + mock video generator | done | `docs/PLATFORM_ADAPTERS.md`, 13 adapters + mock |
| 10 | Per-scene generation workflow with smart state detection, retries, recovery, verification, naming | done | `test_video_task_completes_and_files_are_verified`, `test_retry_with_backoff_then_success`, `test_failure_after_retries_needs_attention_then_skip` |
| 11 | Generic AI browser agent with tool calling (browser.*/filesystem.*/task.*) and heuristic fallback | done | `app/agent`, `test_verification_challenge_is_never_bypassed` |
| 12 | Files, signed URLs, export, dashboard, activity stream, notifications | done | `test_generated_files_listing_and_deletion`, `test_templates_platforms_notifications_dashboard` |
| 13 | Frontend: all pages, three-panel workspace, AVINASH HUD, voice, customisation | done | `scripts/ui_snapshots.py` (11 routes, zero console errors), `/tmp/ui_flow` E2E |
| 14 | Security hardening, limits, observability, env separation, Docker Compose | done | `docs/SECURITY.md`, `docs/OPERATIONS.md`, `docker-compose.yml` |
| 15 | Audit (§44) + bug fixing | done | below |

## §44 audit checklist

| Check | Result |
| --- | --- |
| All API endpoints work | 100 operations in `/api/openapi.json`; covered by 78 backend tests + smoke scripts |
| Live browser viewer streams at usable framerate | CDP screencast frames (JPEG, throttled to `STREAM_MAX_FPS` with the latest frame always flushed, `STREAM_QUALITY`) over the authenticated WebSocket; verified in UI flow |
| Human takeover works | pause → `waiting_for_human` + `human_control` → user input relayed → release re-inspects state → `running` (`test_login_wall_takeover_and_release`, UI flow) |
| Queue persistence and resume | DB-backed; running tasks re-queued after worker death (`test_tasks_survive_worker_restart`) |
| Scene generation loop and smart state detection | adapters poll the real page state with configurable timeouts; no fixed sleeps (`test_detect_state_follows_real_page_state_not_timers`, `test_wait_for_completion_times_out_instead_of_hanging`) |
| Download verification and naming | MP4 magic/size/sha256 verification, `scene_001.mp4` naming, versioned collisions (`test_mp4_verification_rejects_html_and_truncated_files`, smoke E2E 4/4 files) |
| Retry logic | bounded exponential backoff, then `needs_attention` with Retry/Edit/Skip/Take over (`test_retry_policy_backoff_is_bounded`, queue tests) |
| STOP EVERYTHING | `POST /api/tasks/stop-all` + assistant confirm gate (`test_stop_everything`) |
| Auth/authz, tenant isolation | cookie + CSRF, roles, org scoping on every query (`test_tenant_isolation` ×2, `test_viewer_role_is_read_only`, `test_viewer_role_cannot_control_browser`) |
| Secrets never exposed | browser storage state Fernet-encrypted at rest, never serialised in API responses; LLM keys stored encrypted and returned only masked (`api_key_masked`/`has_api_key`); memory refuses secrets |
| WebSocket auth | unauthenticated `/api/ws` closed with 4401 (`test_websocket_requires_authentication`) |
| No silent task death | every exception path sets `failed`/`needs_attention` with `error` + activity event; stale tasks re-queued |
| Observability | structured logs with task/session ids and durations; `X-Request-ID`; activity + audit tables |
| Cost/limits | server ceilings + per-org limits (duration, retries, scenes, concurrency, sessions, file size, rate limit) enforced (`test_task_limits_enforced`, `test_settings_limits_and_members`) |
| Lint/type/test gates | `ruff check`, `ruff format --check`, `pytest` (78 passed), `tsc --noEmit`, `eslint src`, `next build` |
| Docker Compose | frontend, backend, worker, browser-worker, postgres, redis (+ optional nginx), health checks on all services. Written and syntax-validated; **not executed in the authoring sandbox (no Docker daemon)** — run `docker compose up` once on the target host before go-live |

## Bugs found and fixed during the audit

* Screenshot payload spread the signed URL over the wrong key (`url` vs `image`) → history panel
  showed broken images; fixed with `image{url, expires_in}`.
* Session launch under CPU contention exceeded the 10 s readiness wait used by tests/smoke
  scripts → raised to 20 s; sessions report `launching` while Chromium starts.
* `dev_setup.sh` tested the wrong attribute for Chromium availability → uses `browser_available()`.
* Pydantic `EmailStr` rejects reserved TLDs (`.local`) — smoke scripts use `@example.com`.
* `Notification` type import shadowed the DOM global in `use-realtime.ts` → browser notifications
  never fired; fixed with `window.Notification`.
* Topbar layout regression and a Projects-dialog locator ambiguity found by the UI snapshot
  script.
* Workers had no liveness signal for container orchestrators → liveness file + healthcheck
  module.
* A browser worker at its open-browser cap silently ignored further session-open requests (the
  session stayed `closed` with no explanation). It now evicts the longest-idle browser nobody is
  using (profiles persist; human-controlled browsers are only taken after
  `SESSION_INTERACTIVE_IDLE_SECONDS`) and otherwise writes "Waiting for a free browser slot" on the
  session so the UI shows why (`test_worker_evicts_idle_browser_when_slots_are_full`). The cap is
  configurable via `MAX_OPEN_SESSIONS_PER_WORKER`.
* Next standalone output did not include `http-proxy` (only imported by `server.mjs`) → installed
  explicitly in the frontend runner image.

## Known limitations / honest caveats

* Real platform adapters (Google Flow, Runway, Kling, …) encode the known DOM of each product
  with multiple fallback selectors and a generic-agent fallback; platforms change their UIs, so
  expect to maintain the selector tables. Only the mock adapter is exercised by CI.
* Voice features depend on the browser's Web Speech API (Chrome/Edge).
* E-mail/Telegram/webhook notifications are implemented but disabled until their env vars are set.
* The Docker images were written to best practice but could not be built in the authoring
  environment; the first `docker compose build` on a real host is the final verification step.
