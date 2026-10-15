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

## Backend suite (78 tests)

```bash
cd backend && source ../.venv/bin/activate
timeout 900 python -m pytest -q            # whole suite (~4–6 min: real waits are short but real)
python -m pytest -q tests/test_queue.py    # a single file
ruff check app tests && ruff format --check app tests
```

| File | Covers |
| --- | --- |
| `test_units.py` (14) | heuristic script parser (markers, free text, limits, target duration), adapter **state detection follows page state not timers**, timeouts instead of hangs, bounded retry backoff, scene filename patterns, safe filenames/path traversal, unique paths, MP4 verification (rejects HTML/truncated files), Argon2 + Fernet, signed payload expiry/tampering |
| `test_auth.py` (10) | public status, register/login/logout, weak passwords, CSRF enforcement, error envelope + request id, **tenant isolation**, member roles, device sessions + revoke, change password, audit log |
| `test_api.py` (9) | project CRUD + analysis, scene editing workflow (reorder/duplicate/delete/regenerate), assets/characters/export, generated files listing/deletion, templates/platforms/notifications/dashboard, settings limits + members, tenant isolation, CSRF/cookie protection, viewer read-only |
| `test_queue.py` (8) | video task completes with verified files, retry with backoff then success, failure after retries → `needs_attention` → skip, pause/resume/stop, priority + reorder, **tasks survive worker restart**, STOP EVERYTHING, task limits |
| `test_takeover.py` (6) | login wall → takeover → release, **verification challenge is never bypassed**, session lifecycle + limits, viewer cannot control browsers, WebSocket requires authentication, worker evicts the longest-idle unused browser when its slots are full (and reports the wait otherwise) |
| `test_assistant.py` (31) | AVINASH command grammar, confirmations, memory (secret refusal), briefing, capabilities, role gating, navigation hints |

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
