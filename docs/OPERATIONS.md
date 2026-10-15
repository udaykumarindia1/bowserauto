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
| Mock platform | enabled | enabled | enabled | enabled but hidden from Settings (`mock_platform_url` null); disable the `mock` platform per org if desired |
| Logs | text | text | JSON | JSON (`LOG_JSON=true`) |

Key variables (full list with comments: `backend/.env.example`, compose: `.env.example`):
`APP_ENV, SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL, REDIS_URL, PUBLIC_BASE_URL, ALLOWED_ORIGINS,
TRUSTED_HOSTS, COOKIE_SECURE, ALLOW_REGISTRATION, DATA_DIR, WORKER_CAPABILITIES,
MAX_CONCURRENT_TASKS_PER_WORKER, MAX_OPEN_SESSIONS_PER_WORKER, SESSION_INTERACTIVE_IDLE_SECONDS, BROWSER_HEADLESS, CHROMIUM_EXECUTABLE_PATH, STREAM_MAX_FPS,
STREAM_QUALITY, MOCK_PLATFORM_URL, LLM_PROVIDER/LLM_API_KEY/LLM_MODEL/LLM_BASE_URL,
LLM_TIMEOUT_SECONDS/LLM_ALLOWED_HOSTS/AGENT_USE_VISION, SMTP_*, TELEGRAM_*, NOTIFICATION_WEBHOOK_URL,
MAX_TASK_DURATION_SECONDS, MAX_RETRIES_LIMIT,
MAX_SCENES_PER_TASK, MAX_BROWSER_SESSIONS_PER_ORG, MAX_GENERATED_FILE_SIZE_MB,
RATE_LIMIT_PER_MINUTE, SIGNED_URL_TTL_SECONDS`.

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
  Do not expose the published frontend port over plain HTTP to untrusted networks. Set
  `PUBLIC_BASE_URL=https://…`, `ALLOWED_ORIGINS=https://…`, and `COOKIE_SECURE=true`.

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

## Upgrades

```bash
git pull
docker compose build
docker compose up -d           # backend migrates, workers restart; running tasks are re-queued
```

Migrations are plain Alembic (`backend/alembic`). Create new ones with
`cd backend && alembic revision --autogenerate -m "…"` after changing `app/models`.

## Backups

* PostgreSQL: `docker compose exec postgres pg_dump -U bowser bowserauto > backup.sql`.
* Files/profiles: snapshot the `app-data` volume (`docker run --rm -v bowserauto_app-data:/data
  -v $PWD:/backup alpine tar czf /backup/app-data.tgz /data`).
* Keep `ENCRYPTION_KEY` with the backup for encrypted LLM keys and storage-state snapshots. It
  does not encrypt or restore the live Chromium profile; protect the `app-data` backup separately
  with encryption and access controls.

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
