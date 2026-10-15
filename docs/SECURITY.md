# Security model

## Authentication & sessions

* Passwords are hashed with **Argon2id** (`argon2-cffi`); minimum length 10; registration can be
  closed with `ALLOW_REGISTRATION=false` once the owner account exists.
* Login creates a server-side `AuthSession` (hashed token, TTL `SESSION_TTL_HOURS`, IP/user agent
  recorded) and sets:
  * `ba_session` — **HttpOnly**, `SameSite=Lax`, `Secure` in staging/production (`COOKIE_SECURE`),
  * `ba_csrf` — readable double-submit token; every state-changing request must send it as
    `X-CSRF-Token` (checked in `core/deps.py` together with an **Origin** allow-list).
* `POST /api/auth/logout` revokes the session; `switch-organization` re-issues cookies for the
  selected tenant; `PATCH /api/auth/me` only touches `full_name` and the user's `preferences`.
* WebSocket (`/api/ws`) and SSE (`/api/events`) authenticate with the same cookie and Origin
  check; unauthenticated sockets are closed with code `4401`.
* Auth endpoints are rate-limited separately (`AUTH_RATE_LIMIT_PER_MINUTE`), all `/api` routes by
  `RATE_LIMIT_PER_MINUTE` per client IP (429 + `Retry-After`).

## Authorisation & tenancy

* Roles per organisation: `owner` > `admin` > `member` > `viewer`.
  * viewer — read-only (the UI hides every mutating control, the API enforces it with
    `Writer`/`Admin` dependencies),
  * member — create/run/control tasks, edit projects & scenes, manage browser sessions,
  * admin — organisation settings, LLM configuration, limits, members, platforms, audit log,
  * owner — admin + cannot be removed/demoted.
* Every tenant table carries `organization_id`; services always filter on the authenticated
  organisation, and cross-tenant IDs resolve to 404 (`tests/test_auth.py::test_tenant_isolation`).
* Signed download/preview/screenshot URLs embed the organisation id, resource id and expiry in an
  HMAC (`SIGNED_URL_TTL_SECONDS`, default 15 min).

## Secrets & browser state

* `ENCRYPTION_KEY` (Fernet) encrypts: organisation LLM API keys (`secrets_encrypted`) and browser
  storage-state snapshots (cookies/local storage used to re-attach sessions after a worker
  restart). It does **not** encrypt the live Chromium profile directory; protect the data volume
  and backups with host/volume encryption and restrictive access. Production refuses to start
  without the key.
* The API returns LLM keys only masked (`has_api_key`, `api_key_masked`), never the plaintext.
* **No third-party platform password is ever requested or stored.** Users log into platforms by
  hand in the live browser. The live Chromium profile persists on the shared worker/API data
  volume and is not application-encrypted (see above). Raw cookie-store data is not intentionally
  serialized into API responses; however, current URLs are exposed to authorized UI clients and
  the agent, and can themselves carry sensitive query/fragment values. This URL-redaction gap is
  documented as an open finding in `docs/PROJECT_AUDIT.md`; do not treat URL fields as secret-free.
* `.env` files are git-ignored; `docker-compose.yml` reads secrets from the environment only.

## Input validation, XSS, uploads

* Pydantic v2 schemas validate every body/query (lengths, ranges, enums, UUIDs, regex keys).
* React escapes all rendered text; no `dangerouslySetInnerHTML`; the only inline script is the
  pre-paint theme snippet in `app/layout.tsx` (static, no user data).
* Security headers middleware: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`
  (`SAMEORIGIN` only for the mock platform and file previews), `Content-Security-Policy:
  default-src 'none'` for API/download responses, `Referrer-Policy`, `Permissions-Policy`.
  The optional nginx proxy adds frontend response headers; direct access to the published frontend
  port does not pass through nginx and must be protected separately.
* Uploads are limited by `MAX_UPLOAD_SIZE_MB`, filtered by `ALLOWED_UPLOAD_EXTENSIONS`, stored
  under the project folder with sanitised names; downloads from platforms are verified
  (non-empty, MIME/size caps `MAX_GENERATED_FILE_SIZE_MB`, sha256) before being registered.
* Agent `filesystem.*` tools are confined to the project folder (path traversal rejected).

## Audit & observability

* `AuditLog` records security-relevant actions (login/logout/registration, member changes,
  settings/LLM changes, takeover/release, stop-all, deletions) with user id, IP and resource.
* `ActivityLog` records every task/scene/agent/browser event with timestamps and correlation ids.
* Structured JSON logs (`LOG_JSON=true`) carry `request_id`, `task_id`, `session_id`, `action`,
  `result`, `duration_ms`, `error`.

## What the agent will never do

* Solve or bypass CAPTCHA / "verify you are human" walls, MFA, e-mail/SMS codes, payment or
  consent dialogs — it raises `HumanRequired` → task `needs_attention` / `waiting_for_human` and
  notifies you to take over.
* Use stored credentials to log in (`detect_login_required` → `LoginRequired` → you log in).
* Run browsers inside the API process in production (`EMBEDDED_WORKER=false`).

## Production start-up guards (`core/config.py`)

`APP_ENV=production` refuses to boot when `SECRET_KEY` is the dev default or shorter than 32
characters, when `ENCRYPTION_KEY` is missing, or when `DATABASE_URL` points to SQLite.

## Reporting

Treat the repository like any production code base: rotate `SECRET_KEY`/`ENCRYPTION_KEY` on
compromise (re-encrypting stored secrets requires re-entering LLM keys and re-logging browser
sessions), keep Chromium/Playwright current (`PLAYWRIGHT_VERSION` build arg) and review the audit
log for unexpected take-overs.
