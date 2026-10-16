# Security model

## Authentication & sessions

* Passwords are hashed with **Argon2id** (`argon2-cffi`); minimum length 10. In production,
  `ALLOW_REGISTRATION=false` is required; the first-user bootstrap remains open only while the
  user table is empty.
* Login creates a server-side `AuthSession` (hashed token, TTL `SESSION_TTL_HOURS`, IP/user agent
  recorded) and sets:
  * `ba_session` — **HttpOnly**, `SameSite=Lax`, `Secure` in staging/production (`COOKIE_SECURE`),
  * `ba_csrf` — readable double-submit token; every state-changing request must send it as
    `X-CSRF-Token` (checked in `core/deps.py` together with an **Origin** allow-list).
* `POST /api/auth/logout` revokes the session; `switch-organization` re-issues cookies for the
  selected tenant; `PATCH /api/auth/me` only touches `full_name` and the user's `preferences`.
* Phone pairing is initiated from an authenticated device. A random two-minute code is stored as a
  hash, carried in a QR URL fragment (not sent in HTTP/referrer headers), and never returned by
  status endpoints. Scanning moves the request to `claimed`; the signed-in device must explicitly
  approve before the phone can exchange the code once for a separate normal HttpOnly session.
  Pairing requests are rate-limited, limited to three active requests per user, audited without the
  code, and their resulting sessions can be revoked from the active-session list. Do not approve
  QR scans you did not initiate; use HTTPS in production.
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

## Browser task policy & approvals

* Each task can set an exact-host allowlist in `config.allowed_domains`. A listed hostname also
  permits its subdomains. The policy is enforced by the browser request interceptor for page
  navigation and subresources; an empty list leaves the global URL-safety policy in force. This
  is defense in depth, not a replacement for worker egress firewalling. The production browser
  policy resolves hostnames at request time and blocks private/link-local/metadata addresses unless
  explicitly allowlisted; configure a network-level egress policy to limit DNS rebinding and
  browser/redirect behavior outside the app's control.
* Custom LLM endpoints are resolved with bounded A/AAAA lookups; private and metadata resolutions
  are rejected unless the exact hostname is allowlisted. The HTTP transport performs its own DNS
  lookup, so an egress proxy/firewall remains necessary to prevent resolution changes between
  validation and connect.
* Generic agent tasks default to `approval_mode=confirm_writes`. Browser clicks, typing, selects,
  uploads/downloads, tab closure, and project-file create/save/rename/move actions require a real
  organization-member decision before the tool handler runs. `confirm_all` also gates browser
  tools other than the explicit read-only tools; `never` disables this gate. Approval/rejection
  is one-shot and scoped to the currently displayed action; rejection cancels the task. Stop
  wakes an outstanding approval wait.
* Pending action previews are persisted on the task, but free-form text and selected values are
  replaced with a length-only marker to avoid exposing passwords or user-entered content in the
  UI/activity history. The review page displays the target and tool, and requires a deliberate
  approve/reject click. Agent action arguments use the same privacy-safe representation.
* Adapter-native workflows run through an approval-aware `BrowserDriver` proxy as well as the
  generic `ToolRegistry`. `confirm_writes` pauses before adapter clicks, text entry, key presses,
  selections, uploads/downloads and tab closure; `confirm_all` also pauses before navigation,
  scrolling, hovering, tab changes and page-script evaluation. Reads continue without approval.
  The preview hides typed text, selected values and file paths. `never` is an explicit opt-out;
  scheduled workflows reject it. Browser page scripts or provider-side effects cannot be
  classified semantically by the app, so keep the task visible and review each target carefully.

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
  serialized into API responses. Browser URLs are sanitized before persistence, activity/event
  publication, API display, and agent prompting: userinfo, query strings, fragments, and known
  signed-path tokens are removed. Session start URLs that need query/fragment data for worker
  navigation keep the full value encrypted in session metadata. The audit in
  `docs/PROJECT_AUDIT.md` records the earlier pre-fix data-flow finding; it remains a useful
  regression checklist, not a statement of current behavior.
* `.env` files are git-ignored; `docker-compose.yml` reads secrets from the environment only.

## Input validation, XSS, uploads

* Pydantic v2 schemas validate every body/query (lengths, ranges, enums, UUIDs, regex keys).
* React escapes all rendered text; no `dangerouslySetInnerHTML`; the only inline script is the
  pre-paint theme snippet in `app/layout.tsx` (static, no user data).
* Security headers middleware: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`
  (`SAMEORIGIN` only for the mock platform and file previews), `Content-Security-Policy:
  default-src 'none'` for API/download responses, `Referrer-Policy`, and `Permissions-Policy`.
  Next.js config applies frontend response headers, with HSTS only in production. Compose binds the
  frontend host port to loopback by default; use a configured TLS ingress in production.
* Uploads are limited by `MAX_UPLOAD_SIZE_MB`, filtered by `ALLOWED_UPLOAD_EXTENSIONS`, staged
  outside tenant storage, and moved into the project folder only after MIME and organization-quota
  checks. Generated files are checked for non-empty content, media type, per-file size, sha256, and
  organization storage capacity before registration. `MAX_STORAGE_PER_ORG_MB` is the server ceiling;
  organization settings can tighten it. Crossing 80% emits an activity event and in-app warning.
* Project ZIP exports are built on disk and streamed; media-src fallback downloads stream in bounded
  chunks, re-check redirects through the URL policy, and send only cookies scoped to each target.
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
characters, `ENCRYPTION_KEY` is missing, `DATABASE_URL` points to SQLite, `PUBLIC_BASE_URL` or
`ALLOWED_ORIGINS` are not HTTPS, cookies are not secure, `TRUSTED_HOSTS` is wildcard/empty,
`ALLOW_REGISTRATION` is enabled, or a mock-platform URL is configured. The first registration
remains possible only until the first user is created. These checks do not configure or verify the
TLS ingress itself.

## Reporting

Treat the repository like any production code base: rotate `SECRET_KEY`/`ENCRYPTION_KEY` on
compromise (re-encrypting stored secrets requires re-entering LLM keys and re-logging browser
sessions), keep Chromium/Playwright current (`PLAYWRIGHT_VERSION` build arg) and review the audit
log for unexpected take-overs.
