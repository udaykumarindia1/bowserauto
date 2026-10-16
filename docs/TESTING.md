# Testing

## Principles

* **No real AI generation, no real third-party platforms** in automated tests. Tests run with
  `APP_ENV=test`, `BROWSER_BACKEND=fake` (an in-memory browser that renders the built-in mock
  platform's state machine), `LLM_PROVIDER=none` (heuristic planner) and a temporary SQLite
  database (`tests/conftest.py`).
* Tests exercise the real HTTP API through `httpx.ASGITransport` with the application lifespan
  (embedded worker included), so queue, workers, controls and events run end-to-end in-process.
* Suites are serial on purpose: the embedded worker launches (fake) browsers; running pytest in
  parallel with other heavy processes makes session start-up timeouts flaky.

## Backend suite (137 collected pytest cases across 16 modules; verified 2026-10-16)

```bash
cd backend && source ../.venv/bin/activate
timeout 900 python -m pytest -q            # whole suite; runtime varies by host
python -m pytest -q tests/test_queue.py    # a single file
ruff check app tests && ruff format --check app tests
```

| File | Cases | Covers |
| --- | ---: | --- |
| `test_units.py` | 14 | Script parsing, page-state detection (not timers), timeout/retry behavior, safe filenames, MP4 verification, Argon2/Fernet, signed payloads |
| `test_auth.py` | 10 | Registration/login/logout, CSRF, tenant isolation, roles, device sessions, password change, audit log |
| `test_api.py` | 10 | Projects/scenes/assets/export, templates/platforms/notifications/dashboard, limits and members, tenant and read-only checks |
| `test_queue.py` | 8 | Task lifecycle/retries/controls, priority/reorder, worker restart recovery, stop-all and limits |
| `test_takeover.py` | 6 | Manual takeover/release, verification challenge non-bypass, session limits, viewer permissions, WebSocket auth, worker slot recovery |
| `test_assistant.py` | 31 | AVINASH commands, approvals, memory/secret refusal, briefing, capabilities, role gating, navigation hints |
| `test_agent.py` | 6 | Mocked vision/context handling, agent decisions and tool validation; no live provider calls |
| `test_llm.py` | 14 | Provider isolation, endpoint validation, mocked transports, redirects and authorization headers |
| `test_action_approval.py` | 9 | Authenticated one-shot decisions, compare-and-set, rejection/cancellation, pending-preview redaction and policy changes |
| `test_adapter_approval.py` | 5 | Adapter-native driver approval gates and rejection preventing browser operations |
| `test_device_pairings.py` | 4 | Pairing expiry, approval, one-time exchange and session revocation |
| `test_media_streaming.py` | 2 | Bounded media/download and export streaming behavior |
| `test_schedules.py` | 3 | Schedule policy validation and due-run dispatch behavior |
| `test_security_hardening.py` | 9 | Production configuration, request-size configuration/integration, URL redaction/policy, signed-token log redaction, trusted proxies and mock-router isolation |
| `test_storage_quota.py` | 2 | Organization storage cap configuration and upload enforcement |
| `test_request_body_limit.py` | 4 | Declared and chunked request-size limits, exact-boundary acceptance and ambiguous Content-Length rejection |

All automated browser interactions use the fake driver. The suite uses mocked LLM transports and does not call real AI providers or third-party platforms.

Useful filter for the noisy aiosqlite cancellation traces:

```bash
python -m pytest -q > /tmp/pytest.txt 2>&1; grep -n "^E  \|FAILED\|passed\|failed" /tmp/pytest.txt | grep -v "sqlalchemy\|aiosqlite\|CancelledError$"
```

## Frontend checks

```bash
cd frontend
npm run typecheck      # tsc --noEmit (strict)
npm run lint           # ESLint flat config: next/core-web-vitals + next/typescript + hooks rules
npm run build          # production build (standalone output)
```

## Browser-level smoke tests (real Chromium, mock platform)

With the API (`:8000`) and the proxy (`:3000`) running:

```bash
.venv/bin/python scripts/smoke_e2e.py --speed fast [--fail-rate 0.5]   # API flow: project → scenes → task → files → zip
.venv/bin/python scripts/smoke_controls.py                            # pause/resume/takeover/release/stop against a live task
.venv/bin/python scripts/ui_snapshots.py --out /tmp/ui                # logs in through the UI, screenshots every page, exits 1 on console errors
```

The last script is the closest thing to a UI regression test without a JS test runner: it
registers/logs in through the real forms, visits every route and fails on any browser console
error or page exception.

## Writing new tests

* Use the `client` fixture (authenticated owner with `client.csrf` set) and the helpers in
  `conftest.py` (`wait_task`, `wait_session`, `register_user`).
* Prefer polling helpers with timeouts over sleeps; control actions are asynchronous.
* For adapter work, extend the mock platform (`app/mock_platform`) rather than hitting real sites.
