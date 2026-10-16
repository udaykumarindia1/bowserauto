# Roadmap status: phases 0–3

**Updated:** 2026-10-16 · **Branch:** `arena/c436eb2e-bowserauto`

This status records application work completed for Phases 0–3. It is not a production-readiness
certification: deployment-specific network, storage, PostgreSQL, backup/restore and real-browser
checks still require an operator-controlled environment.

## Phase 0 — P1 security and dependency blockers

**Status: application controls implemented; host/deployment controls remain.**

- Browser requests use a shared URL policy for navigation, redirects and subresources. Production
  resolves destinations and blocks private, link-local and metadata addresses unless explicitly
  allowlisted; task policies may further restrict destinations to exact hostnames and subdomains.
  Connection-time DNS pinning and a browser-worker egress firewall/proxy remain deployment controls.
- LLM endpoint validation uses bounded DNS resolution and rejects private/metadata resolutions
  unless the exact host is explicitly allowlisted. The HTTP client resolves independently, so an
  egress proxy/firewall is still needed to close the DNS-rebinding gap.
- URL userinfo, queries, fragments and signed path tokens are removed from persistence, API/event
  display, agent prompts and logs. Start URLs that need sensitive query state are kept in encrypted
  session metadata; signed-download path tokens are redacted from access logs.
- Production startup requires HTTPS public URL/origins, secure cookies, explicit trusted hosts,
  PostgreSQL, an encryption key, disabled open registration and no mock-router URL. First-owner
  bootstrap remains available while the user table is empty. Compose binds the frontend to loopback;
  TLS ingress and its matching host/origin values remain operator-configured.
- API/Next response headers are configured. Frontend dependencies passed the current high-severity
  npm audit. Python requirements are ranges rather than a fully pinned lockfile.
- **Remaining security control:** active Chromium profile directories are plaintext on the data
  volume. Encrypted GPG backups protect exported copies, not the live profile. Use encrypted
  host/volume storage and strict access controls; application-level encryption of an in-use
  Chromium profile is not implemented.

## Phase 1 — restart-safe orchestration, concurrency, availability, backups/quotas, alerts

**Status: core workflows implemented; infrastructure validation remains.**

- Tasks persist in PostgreSQL/SQLite; worker claims are conditional, heartbeats and stale recovery
  are implemented, and production browser automation stays outside the API process.
- Worker task claims, schedule dispatch and organization storage reservations use row locks on
  PostgreSQL. Conditional updates prevent duplicate claims on SQLite. PostgreSQL multi-worker
  contention has not been exercised here.
- Schedules persist in `task_schedules`; workers dispatch idempotently, do not overlap recurring
  runs, skip missed intervals instead of backfilling and defer when queue capacity is full.
- Organization storage caps are configurable (default 50 GiB), enforced on selected uploads and
  generated outputs, with warning/activity events at 80%. Project ZIP exports and media fallback
  downloads stream from disk/in bounded chunks.
- `scripts/backup.sh` creates GPG-encrypted PostgreSQL and persistent-data backups, pauses app
  writers during the snapshot and applies retention. It is operator-run; Docker/GPG execution,
  restore drills, offsite delivery and key custody remain unverified/operator responsibilities.
- Health endpoints, worker liveness, activity logs, in-app notifications and quota warnings are
  available. External alert delivery depends on configured SMTP/Telegram/webhook channels.

## Phase 2 — policy, approvals, and scheduled allowlisted workflows

**Status: implemented for generic-agent tools and adapter-native browser calls.**

- Task policies support `confirm_writes` (default), `confirm_all` or `never`, plus exact hostname
  allowlists. Free-form text, selected values and file paths are hidden from approval previews.
- Generic ToolRegistry writes wait for an authenticated, one-shot approve/reject decision. Decisions
  are compare-and-set; pending approval blocks configuration changes; rejecting an action stops the
  task.
- Platform adapter calls use an approval-aware BrowserDriver proxy. `confirm_writes` pauses before
  clicks, typing, key presses, selection, upload/download and tab closure; `confirm_all` also gates
  navigation, scrolling, hovering, tab changes and page-script evaluation. Read-only inspection is
  not blocked. `never` remains an explicit task-level opt-out; scheduled tasks cannot use it.
- The schedule API/HUD supports create/list/pause/resume/delete, future UTC run times, recurring
  intervals, optional browser sessions and domain/action policies. Schedules dispatch only to the
  generic agent and require an allowlist and approval mode.
- Adapter prompts and selector/target semantics cannot be classified perfectly by software; keep
  tasks visible and review the action preview before approving external-site operations.

## Phase 3 — assistant skills, undo, memory, voice, and mobile controls

**Status: application feature scope implemented.**

- The authenticated Skills page and `/assistant/skills` API show live browser/filesystem/task tools,
  schemas and approval classifications. Skills are version-controlled built-in handlers; arbitrary
  plugin/code loading is intentionally not enabled.
- Memory can be listed, added, individually deleted and cleared from the API/HUD; secret-like values
  are rejected. Voice input/output and wake-word controls use browser Web Speech APIs and are
  browser-dependent.
- `task.undo` compensates only the latest explicit `browser.open` navigation while the page remains
  at the expected destination. It refuses if state changed; it cannot undo clicks, submissions,
  purchases or other external effects. With `confirm_all`, undo itself requires approval.
- The responsive HUD works on mobile browsers. Settings → Devices creates a short-lived QR pairing
  request; the phone scans into the same-origin `/pair` page, the signed-in device explicitly
  approves, and the phone receives a separate HttpOnly app session. Codes are hashed at rest,
  kept in the URL fragment, expire after two minutes and can be exchanged once. Active sessions are
  revocable from Settings → Devices. Production use requires HTTPS and a phone that can reach the
  configured app origin.

## Verification and remaining deployment work

- Backend: Ruff check/format plus the full automated suite pass (131 tests); tests use SQLite, mocked LLM
  transports and fake browser interactions. No real AI generation is called.
- Frontend: `npm ci`, lint, TypeScript typecheck, production build and `npm audit --audit-level=high`
  pass with zero reported npm vulnerabilities.
- Backup script passed mocked Docker/GPG success and failure-cleanup smoke checks; this does not test
  a real database snapshot, encryption key, restore, or service restart.
- Docker Compose, Chromium against real sites, PostgreSQL multi-worker races, encrypted-backup
  restore, TLS ingress, browser-worker firewalling, live-profile volume encryption, phone scanning
  across real networks and real notification delivery were not exercised in this sandbox.

**Do not claim “production ready” until those deployment-specific checks are run and the remaining
active-profile encryption/egress risks are accepted or resolved.**
