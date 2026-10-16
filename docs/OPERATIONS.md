# Operations

## Environments

Configuration is 100 % environment variables (pydantic-settings; `.env` files are a convenience
for development only). `APP_ENV` selects the profile:

| | development | test | staging | production |
| --- | --- | --- | --- | --- |
| Database | SQLite (`backend/data`) | temp SQLite | PostgreSQL | PostgreSQL (enforced) |
| Bus / workers | in-memory bus, embedded worker in the API | in-memory, fake browser | Redis + separate workers | Redis + separate workers (`EMBEDDED_WORKER=false`) |
| Cookies | not Secure | – | Secure | Secure |
| Secrets | dev defaults allowed | – | real | `SECRET_KEY` ≥ 32 chars + `ENCRYPTION_KEY` enforced |
| Mock platform | enabled | enabled | enabled | disabled; the mock router is not mounted and production refuses a configured `MOCK_PLATFORM_URL` |
| Logs | text | text | JSON | JSON (`LOG_JSON=true`) |

Key variables (full list with comments: `backend/.env.example`, compose: `.env.example`):
`APP_ENV, SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL, REDIS_URL, PUBLIC_BASE_URL, ALLOWED_ORIGINS,
TRUSTED_HOSTS, COOKIE_SECURE, ALLOW_REGISTRATION, DATA_DIR, WORKER_CAPABILITIES,
MAX_CONCURRENT_TASKS_PER_WORKER, MAX_OPEN_SESSIONS_PER_WORKER, SESSION_INTERACTIVE_IDLE_SECONDS, BROWSER_HEADLESS, CHROMIUM_EXECUTABLE_PATH, STREAM_MAX_FPS,
STREAM_QUALITY, MOCK_PLATFORM_URL, LLM_PROVIDER/LLM_API_KEY/LLM_MODEL/LLM_BASE_URL,
LLM_TIMEOUT_SECONDS/LLM_ALLOWED_HOSTS/AGENT_USE_VISION, SMTP_*, TELEGRAM_*, NOTIFICATION_WEBHOOK_URL,
MAX_TASK_DURATION_SECONDS, MAX_RETRIES_LIMIT,
MAX_SCENES_PER_TASK, MAX_BROWSER_SESSIONS_PER_ORG, MAX_UPLOAD_SIZE_MB, MAX_REQUEST_BODY_MB,
MAX_GENERATED_FILE_SIZE_MB, MAX_STORAGE_PER_ORG_MB, MAX_SCHEDULED_WORKFLOWS_PER_ORG,
RATE_LIMIT_PER_MINUTE,
SIGNED_URL_TTL_SECONDS`.

## Docker Compose

```bash
cp .env.example .env && $EDITOR .env
docker compose build
docker compose up -d
docker compose ps                      # all services should be "healthy"
docker compose logs -f browser-worker  # Chromium + task logs
```

* `backend` runs `alembic upgrade head` on start (`AUTO_MIGRATE=true`); workers wait for the API
  health check, so the schema always exists before they claim tasks.
* Scale browser capacity: `BROWSER_WORKERS=3 docker compose up -d --scale browser-worker=3`
  (each hosts its own sessions; tasks bound to a session stay on the worker hosting it).
* `shm_size: 1gb` on browser workers is required for Chromium stability.
* Persistent data lives in the named volumes `postgres-data`, `redis-data`, `app-data`
  (project files, live Chromium profiles, encrypted storage-state snapshots, screenshots). The
  live profiles are not application-encrypted: use encrypted host/volume storage and encrypted
  backups, and protect the backups with the same care as active browser sessions. Back up related
  volumes consistently.
* **TLS is not configured by the current nginx profile.** `deploy/nginx.conf` listens on HTTP
  port 80 and has only a commented, incomplete 443 example. Terminate TLS at a separately configured
  reverse proxy/load balancer, or add and test a certificate-backed 443 server before exposure.
  The frontend host port binds to `127.0.0.1` by default so a host-level TLS proxy can connect
  without exposing it directly. Do not publish it on a public interface over plain HTTP. Set
  `PUBLIC_BASE_URL=https://…`, matching `ALLOWED_ORIGINS=https://…`, an explicit
  `TRUSTED_HOSTS`, `COOKIE_SECURE=true`, and `ALLOW_REGISTRATION=false`. The first-owner registration
  remains available only while the user table is empty.

### Health & liveness

| Endpoint / check | Meaning |
| --- | --- |
| `GET /api/health` | `{status: ok\|degraded, database, bus, environment, embedded_worker}` |
| `python -m app.workers.healthcheck` | worker touched `DATA_DIR/workers/<host>-<pid>.alive` within 3× heartbeat |
| `GET /login` (frontend) | Next + proxy up |
| `GET /healthz` (nginx) | proxy up |

Workers heartbeat running tasks every `WORKER_HEARTBEAT_SECONDS` (5 s); tasks whose worker went
silent for `WORKER_STALE_AFTER_SECONDS` (45 s) are re-queued automatically and the affected
sessions are marked closed so they relaunch on demand — a restart never loses the queue.

### Storage quotas and alerts

`MAX_STORAGE_PER_ORG_MB` is the server-wide storage ceiling (default 51,200 MiB); an organization
may set a lower `max_storage_mb` limit in Settings. Reference uploads and generated outputs check
usage before entering tenant storage. The dashboard reports use/limit, and crossing 80% creates an
in-app notification plus a `storage.quota_warning` activity event. External email/webhook delivery
requires configuring notification channels. Temporary `.incoming` downloads are excluded from the
persistent quota until verified and committed.

`MAX_UPLOAD_SIZE_MB` limits an individual uploaded file (default 50 MiB). The ASGI request-body
limit `MAX_REQUEST_BODY_MB` (default 64 MiB) covers the full request, including multipart framing;
it must be greater than the per-file limit. Declared lengths are rejected before parsing, and
streamed/chunked request bodies are counted as they arrive. Keep an independent size cap at the
trusted ingress as defense in depth.

## Upgrades

```bash
git pull
docker compose build
docker compose up -d           # backend migrates, workers restart; running tasks are re-queued
```

Migrations are plain Alembic (`backend/alembic`). Create new ones with
`cd backend && alembic revision --autogenerate -m "…"` after changing `app/models`.

## Backups

The repository includes an operator-run, GPG-encrypted snapshot script. Configure a GPG public key
on the backup host, then run during a maintenance window (the script stops frontend/API/workers
briefly so database and files are consistent):

```bash
export BACKUP_GPG_RECIPIENT='backup-key@example.org'
export BACKUP_DIR=/secure/offsite-staging/bowserauto
export BACKUP_RETENTION_DAYS=14
./scripts/backup.sh
```

The script captures a PostgreSQL dump and `app-data` volume archive, requires encryption, restarts
application services on exit, and prunes old backup files. It does not automate offsite transfer,
GPG key management, or restore. Copy snapshots to a separate access-controlled location and
perform a restore drill before relying on them. Keep the GPG private key and the application's
`ENCRYPTION_KEY` outside the backup. GPG protects backup copies; it does **not** encrypt active
Chromium profile files on the live data volume. Volume encryption and strict access controls remain
required. Redis is transient; PostgreSQL is the source of truth for queued tasks.

## Running without Docker (systemd style)

1. `python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt && .venv/bin/playwright install chromium --with-deps`
2. API: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'`
   (cwd `backend`, env `EMBEDDED_WORKER=false REDIS_URL=…`).
3. Workers: `python -m app.workers.main` with `WORKER_CAPABILITIES=browser,general` (one or more).
4. Frontend: `cd frontend && npm ci && npm run build && NODE_ENV=production BACKEND_URL=http://127.0.0.1:8000 node server.mjs`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Task stays `queued` | a worker with the `browser` capability is running and healthy; `docker compose logs worker browser-worker`; `GET /api/health` shows the bus |
| Session shows "Waiting for a free browser slot" | the browser worker already keeps `MAX_OPEN_SESSIONS_PER_WORKER` browsers open for humans/tasks; close or release another session, raise the cap (≈ 500 MB RAM per browser) or scale `browser-worker` |
| Live viewer "Waiting for first frame" | the session is `ready`/`in_use` on a live worker; WebSocket upgrade allowed by your proxy (`Upgrade`/`Connection` headers) |
| `needs_attention: Login required` | open the task, **Take over**, log into the platform, **Return control**; then **Mark logged in** on the session |
| Downloads fail with size errors | raise `MAX_GENERATED_FILE_SIZE_MB` (org limit in Settings has to fit under the server ceiling) |
| Production start-up refuses | set `SECRET_KEY` (≥32 chars), `ENCRYPTION_KEY`, PostgreSQL `DATABASE_URL` |
| Chromium crashes in Docker | ensure `shm_size`, `init: true`, enough RAM (≈ 500 MB per headless browser) |
| Sandbox/VM reset wiped the dev environment | `./scripts/dev_setup.sh` repairs venv, Chromium, `.env`, migrations, `node_modules` |
