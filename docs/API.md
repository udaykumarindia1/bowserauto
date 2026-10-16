# API reference (summary)

The authoritative, always-current contract is the OpenAPI document served by the backend:
`GET /api/openapi.json` and the interactive docs at `/api/docs`. This page summarises the
surface and the conventions.

## Conventions

* Base path `/api`; JSON bodies; UUID ids; timestamps ISO-8601 UTC.
* Auth: cookie `ba_session` (HttpOnly) + header `X-CSRF-Token` (value of the `ba_csrf` cookie)
  on every non-GET request. Responses from `register`/`login`/`switch-organization`/`me`
  include `csrf_token`.
* Errors: `{"error": {"code": "...", "message": "...", "details": {...}}}` with 400/401/403/404/
  409/413/422/429/500. `DELETE` returns 204.
* Lists: `/api/tasks` and `/api/files` return `{items, total}` (with `limit`/`offset`); other
  lists return plain arrays.
* Roles: *writer* endpoints require member+; *admin* endpoints require admin/owner.

## Auth

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/auth/status` | `{has_users, registration_open}` |
| POST | `/auth/register` | `{email, password, full_name, organization_name?}` → first user becomes owner |
| POST | `/auth/login` / `/auth/logout` | |
| GET / PATCH | `/auth/me` | `PATCH {full_name?, preferences?}` (assistant prefs live under `preferences.jarvis` — legacy key name, assistant default is AVINASH) |
| POST | `/auth/change-password` | |
| GET / DELETE | `/auth/sessions[/{id}]` | List and revoke this user's active login sessions. |
| POST | `/auth/device-pairings` | Authenticated device creates a two-minute pairing request → `{id, code, expires_at}`. The raw code is returned once. |
| POST | `/auth/device-pairings/claim` | Public phone endpoint: `{pairing_id, code, device_name?}` marks a scan as waiting for confirmation. |
| POST | `/auth/device-pairings/poll` | Public phone endpoint: `{pairing_id, code}`; only an explicitly approved request can exchange once for a normal HttpOnly login cookie. |
| GET | `/auth/device-pairings/{id}` | Creator checks status/device metadata; code is never returned. |
| POST | `/auth/device-pairings/{id}/approve\|reject` | Creator explicitly allows or rejects the claimed phone. |
| POST | `/auth/switch-organization/{org_id}` | |

The QR URL keeps the one-time pairing code in the browser fragment (not the HTTP request path or
referrer). Scanning alone does not grant access: the signed-in device must approve the request.
Pairing expires after two minutes, can be exchanged for one app session only, and that session is
independently revocable from `/auth/sessions`.

## Tasks (queue)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/tasks?status=active\|attention\|finished\|<status>&project_id&limit&offset` | |
| POST | `/tasks` | `{kind, project_id?, browser_session_id?, instructions, platform_key?, priority(1–10), start, config{max_retries, generation_timeout_seconds, action_timeout_seconds, parallel_generation, scene_ids?, retry_failed_only, headless?, agent_mode auto\|adapter\|generic, approval_mode confirm_writes\|confirm_all\|never, allowed_domains[], mock_options}}` |
| GET / PATCH / DELETE | `/tasks/{id}` | delete → 409 while active |
| POST | `/tasks/{id}/start\|pause\|resume\|stop\|takeover\|release\|retry\|skip\|approve\|reject` | control actions are asynchronous — poll the task or listen to events; `retry`/`skip` accept `{scene_id?}`; `approve`/`reject` decide a pending generic-agent tool action |
| POST | `/tasks/reorder` | `{task_ids}` for queued tasks |
| POST | `/tasks/stop-all` | STOP EVERYTHING → `{stopped}` |
| GET | `/tasks/{id}/events` | SSE stream scoped to one task |
| GET | `/tasks/{id}/activity`, `/agent-runs`, `/screenshots` | screenshots carry `image{url, expires_in}` (signed) |

Task object: `status`, `control`, `human_control`, `progress{total, completed, failed, skipped,
percent, current_scene_*}`, `current_action`, `attention{kind, reason, scene_id, scene_code,
options, since, pending_action?}`, `priority`, `retry_count`, `worker_id`, timestamps, `error`, `result`;
`GET /tasks/{id}` adds `steps[]`, `scenes[]`, `project_name`, `session_name`. `confirm_writes` and
`confirm_all` apply to generic-agent tools and adapter-native BrowserDriver calls; adapter writes
wait for the same `approve` / `reject` decision before execution. `confirm_all` also gates browser
state changes. `never` opts out. Action previews hide free-form text and selected values.
`allowed_domains` restricts browser destinations to listed hostnames and their subdomains; leaving
the list empty keeps the global URL-safety rules.

## Scheduled workflows

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/schedules` | List the caller's organization's schedules. |
| POST | `/schedules` | Create `{title, kind, instructions, platform_key, browser_session_id?, priority, run_at, repeat_interval_minutes?, config{agent_mode, approval_mode, allowed_domains[]}}`. `run_at` must include a timezone and be in the future. At least one exact allowed hostname is required; `approval_mode=never` is rejected. |
| PATCH | `/schedules/{id}` | Pause/resume, change `run_at`, or change `repeat_interval_minutes`. |
| DELETE | `/schedules/{id}` | Remove the schedule; previously dispatched tasks remain. |

Workers dispatch due schedules idempotently into the normal persistent task queue. Recurring runs
skip overlapping intervals rather than backfilling missed runs. Queue-capacity pressure defers a due
schedule. The API/UI supports one-time, hourly, six-hourly, daily, and weekly cadences; the API
accepts 15–10,080-minute intervals. Schedules always use the generic agent and its domain/action
policy. A schedule cannot disable approval.

## Projects, scenes, assets, characters

`/projects` CRUD · `POST /projects/{id}/analyze {script_text?, clip_duration_seconds?,
target_duration_seconds?}` · `GET /projects/{id}/estimate` · `GET /projects/{id}/export` (zip) ·
`/projects/{id}/scenes` (POST `?after=<scene_id>`, PATCH, DELETE, `/reorder`, `/{id}/duplicate`,
`/{id}/regenerate-prompt`) · `/projects/{id}/assets` (multipart upload, signed `url`) ·
`/projects/{id}/characters`.

## Browser sessions

`/sessions` CRUD · `POST /sessions/{id}/open|close|navigate|takeover|release|logout|mark-login?logged_in=` ·
`POST /sessions/{id}/upload` (file into the live page while in control) ·
`GET /sessions/{id}/screenshots` · `GET /sessions/{id}/preview` (latest frame, signed).

Multipart uploads enforce `MAX_UPLOAD_SIZE_MB` per file (50 MiB by default); all HTTP request bodies
also have a configurable `MAX_REQUEST_BODY_MB` cap (64 MiB by default, including multipart overhead).
Oversized/ambiguous requests return 413/400 respectively. The app counts streamed bodies without
buffering them; enforce an additional cap at the trusted ingress.

## Files, platforms, templates, settings, misc

* `/files` list/get/delete, `GET /files/{id}/url?inline=` → signed download/preview URL.
* `/platforms`, `GET /platforms/{key}`, `POST /platforms/{key}/enabled {enabled}` (admin).
* `/templates` CRUD (built-ins are read-only).
* `GET/PATCH /settings` (`name, settings{notifications,defaults,ui,platforms}, llm_provider,
  llm_model, llm_base_url, llm_api_key, clear_llm_api_key, limits{}`), admin-only
  `POST /settings/llm/test` (small provider health check; does not expose output or credentials),
  `/settings/members` CRUD. See [LLM provider configuration](LLM_PROVIDERS.md).
* `GET /dashboard`, `GET /activity?level&event_type&task_id&project_id&before&limit`,
  `GET /audit` (admin), `/notifications` (+`/read`), `GET /health`.

## Assistant (AVINASH)

* `POST /assistant/command {text, confirmed?, context{task_id?, session_id?, project_id?, page?,
  local_hour?}}` → `{reply, intent, ok, actions[], navigate?, confirm{title, detail, command}?,
  client{action, value}?, data}`. Irreversible intents (STOP EVERYTHING, CLEAR MEMORY, QUEUE
  BROWSER TASK) return `confirm` first; resend the same text with `confirmed: true`.
* `GET /assistant/briefing?local_hour=`, `GET /assistant/capabilities`, `GET /assistant/skills`
  (live built-in browser/filesystem/task tool schemas and approval classifications), `GET /assistant/system`
  (host metrics), `GET/POST/DELETE /assistant/memory[/{id}]` (POST refuses secrets with 422).
  The skill catalog is read-only; only version-controlled handlers are executable (no arbitrary plugin loader).

## Realtime

* **WebSocket `/api/ws`** — server sends `{"type":"hello"}`; client messages: `{"type":"ping"}`,
  `{"type":"subscribe_session","session_id"}` → `subscribed`, then `frame` (`data` base64 JPEG,
  `width`, `height`, `seq`, `ts`), `status` (`url`, `title`, `action`, `interactive`, `task_id`,
  `tabs`, `file_chooser_pending`), `action`; `{"type":"unsubscribe_session"}`;
  `{"type":"input","session_id","event":{type: mouse_move|mouse_down|mouse_up|wheel|key_down|
  key_up|type|paste|navigate|back|forward|reload|switch_tab|close_tab, …}}` (only while the user
  has control). Organisation events arrive as `{type: "<event name>", message, level, task_id,
  session_id, project_id, scene_id, payload, created_at}`.
* **SSE `/api/events`** — the same organisation events for clients without WebSockets.

Event names: `task.created|queued|started|paused|resumed|progress|attention|approval|completed|failed|
cancelled|updated`, `scene.started|updated|completed|failed|skipped`, `agent.action|thought`,
`browser.navigation|screenshot|status`, `session.updated`, `download.started|completed|failed`,
`human.takeover|released|required`, `file.created|deleted`, `schedule.created|updated|deleted|dispatched`,
`storage.quota_warning`, `project.updated`, `worker.status`, `notification`.
