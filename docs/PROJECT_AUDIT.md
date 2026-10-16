# BowserAuto project audit

**Audit date:** 2026-10-16

**Baseline reviewed source revision:** `ad36c83` (branch `arena/c436eb2e-bowserauto`)

**Current release assessment:** **Not certified for production or public, untrusted workloads.** Application remediations and Phase 0–3 status are recorded in [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md). Remaining items include plaintext live Chromium profiles, external egress enforcement/DNS pinning, PostgreSQL multi-worker verification, and deployment/restore drills.

This is an evidence-based repository review, not an independent penetration test or a compliance certification. Findings distinguish confirmed code/configuration gaps from risks that were not reproduced. No confirmed breach or successful exploit was found. The finding descriptions below record conditions at the baseline revision; the remediation table below supersedes their original status where branch changes have landed.

## Executive summary

The original audit identified P1/P2 security and reliability gaps. Subsequent work on the current branch adds request-time browser URL checks, DNS-aware LLM endpoint validation, URL/token redaction, fail-closed production settings, generic and adapter-browser action approval, scheduled workflows, disk-backed exports/downloads, tenant storage quotas, encrypted backup tooling, phone pairing, a live skills catalog, and CI gates. The full current implementation status and caveats are listed below and in [`ROADMAP_STATUS.md`](ROADMAP_STATUS.md).

The primary residual production risks are plaintext live Chromium profile directories, DNS rebinding/egress controls outside the application, and deployment verification not performed in this sandbox. No live exploit or real third-party generation was attempted.

## Current remediation status (2026-10-16)

The detailed finding narratives that follow describe the baseline commit above. These current statuses
supersede their baseline status labels:

| Finding | Current status |
| --- | --- |
| A-01 browser URL/egress policy | **Mitigated in application:** DNS-aware request interception, metadata/private checks, and task allowlists. Egress firewall and DNS pinning remain deployment controls. |
| A-02 LLM endpoint DNS checks | **Mitigated in validation:** bounded A/AAAA resolution rejects private/metadata addresses unless the exact host is allowed. DNS rebinding at connect time still requires egress control. |
| A-03 browser URL/token leakage | **Mitigated in code/tests:** sanitized before persistence, events, API display, prompts, and logs; secret-bearing start URL state is encrypted. |
| A-04 production ingress/settings | **Partially mitigated:** secure cookies, HTTPS/origin/host/mock/registration checks; frontend binds to loopback by default. TLS ingress must still be configured and tested. |
| A-05 mock platform in production | **Mitigated:** production refuses `MOCK_PLATFORM_URL` and does not mount the mock router. |
| A-06 plaintext live profiles | **Open:** active Chromium profiles remain plaintext on the data volume. GPG backups do not encrypt the live profile. |
| A-07 forwarded client IP | **Mitigated:** forwarded addresses are trusted only through configured proxy CIDRs. |
| A-08 signed-token access logs | **Mitigated:** signed route tokens are redacted from request-path logs; regression test added. |
| A-09 resource exhaustion | **Partially mitigated:** disk-backed ZIP streaming, bounded media streaming, staged uploads, per-file and organization quotas, plus a configurable app-level request-body cap that counts chunked uploads. Trusted-ingress caps, retention/cleanup policy, and restore/deployment load tests remain. |
| A-10 cross-worker capacity | **Mitigated in PostgreSQL implementation:** organization row lock plus conditional claim. PostgreSQL multi-worker contention was not tested here. |
| A-11 action policy | **Mitigated in application:** generic ToolRegistry and adapter-native BrowserDriver writes use the same approval controller; `confirm_all` also gates browser state changes. The explicit `never` mode and external-page semantics remain documented limitations. |
| A-12 dependency advisories | **Mitigated at current resolution:** refreshed frontend dependencies and dependency-audit CI gate. Re-run audits against deployment locks before release. |
| A-13 CI coverage | **Mitigated:** GitHub Actions runs backend lint/tests and frontend lint/typecheck/build/audit. A dedicated frontend unit suite remains absent. |
| A-14 frontend headers | **Mitigated in Next/API configuration:** response security headers are configured. HTTPS/HSTS behavior still requires a live ingress check. |

### Current branch verification

- Backend: 137 collected tests pass, with Ruff check and format checks clean. Automated browser tests use the fake driver.
- Frontend: lint, TypeScript, production build, and `npm audit --audit-level=high` pass; the current npm audit reports zero vulnerabilities.
- CI workflow exists for backend tests/lint and frontend lint/typecheck/build/audit. A dedicated frontend unit/integration suite remains absent.
- Body-size limits are now covered for declared and chunked request bodies. This does not replace a trusted-ingress limit or a real deployment load test.

## Severity and status

- **High / P1:** address before exposing the application to the public internet or untrusted tenants.
- **Medium / P2:** fix before broad production use or significant scale; add regression tests.
- **Confirmed:** source/configuration demonstrates the path or missing control. It does not imply that a live exploit was performed.
- **Potential / not reproduced:** the race or impact follows from the implementation but was not exercised under production concurrency.

## Findings

### A-01 — Browser navigation has no destination or egress policy

**Severity:** High · **Priority:** P1 · **Status:** Confirmed gap

Session start/navigation input accepts arbitrary HTTP(S) URLs. The generic browser tool and human-takeover navigation reject dangerous schemes such as `javascript:`, `data:`, and `file:`, but they do not reject loopback, private, link-local, or other internal destinations. `PlaywrightDriver.goto()` then navigates directly, with no destination policy. Browser-worker network egress is not restricted by the application.

**Evidence:** [`NavigateRequest`](../backend/app/schemas/__init__.py#L382-L393), [`InputController`](../backend/app/browser/input.py#L97-L104), [`browser.open`](../backend/app/agent/tools/__init__.py#L125-L138), and [`PlaywrightDriver.goto`](../backend/app/browser/playwright_driver.py#L250-L260).

**Impact:** In a shared/container deployment, a permitted user or compromised agent flow may cause a browser worker to reach services on loopback, private Docker networks, link-local addresses, or cloud metadata endpoints. Browser navigation also follows page-controlled links and redirects; scheme checks alone do not establish a safe network boundary.

**Recommended action:** Centralize destination policy for initial URLs, agent navigation, takeover navigation, redirects, and browser subrequests. Deny loopback/private/link-local/metadata destinations by default, resolve and validate DNS at connection time, and enforce the boundary with a browser egress proxy/firewall as defense in depth. Add tests for IPv4/IPv6 literals, alternate numeric IP spellings, DNS changes, redirects, and subresources.

### A-02 — LLM endpoint validation does not resolve hostnames

**Severity:** High · **Priority:** P1 · **Status:** Confirmed residual gap

The provider URL validator rejects known metadata hostnames and literal private/metadata IPs, and production requires exact-host allowlisting for literal private/plain-HTTP destinations. However, it does not resolve a public-looking hostname before the client connects. An FQDN that resolves to a private or metadata address can therefore pass the hostname-based checks.

**Evidence:** [`validate_llm_base_url`](../backend/app/agent/llm/factory.py#L35-L103) checks the parsed hostname/literal address and `LLM_ALLOWED_HOSTS`; there is no DNS resolution in this validation path.

**Impact:** A tenant administrator able to configure a custom provider endpoint may be able to direct server-side LLM traffic toward internal services through a hostname that resolves to an internal address. The code itself recommends an outbound network policy, but the policy is not enforced by this validator.

**Recommended action:** Resolve and validate A/AAAA records at connection time, prevent DNS rebinding between validation and connect (or use a controlled egress proxy), re-check redirects, and deny private/link-local/metadata ranges at the container/host boundary. Preserve the explicit allowlist for intended internal model hosts.

### A-03 — Sensitive values in browser URLs can reach the UI, storage, events, and LLM

**Severity:** High · **Priority:** P1 · **Status:** Confirmed data-flow gap; token exposure is conditional on URL contents

Raw browser URLs are used in session status, stored in `BrowserSession.current_url`, published in navigation events, returned in session/screenshot API data, and included in the agent's page prompt. The agent also records tool arguments/results, which can contain URLs. The existing `redact()` helper redacts sensitive *keys* and a few value patterns; it does not parse URL query strings or fragments, so a key such as `access_token` embedded in a `url` string is not redacted. OAuth codes, signed query parameters, or bearer-like fragments may consequently be sent to a configured external LLM and exposed to authorized UI/event consumers or persisted records.

**Evidence:** [`SessionHost.publish_status`](../backend/app/workers/session_host.py#L268-L305), [`BrowserSessionOut`](../backend/app/schemas/__init__.py#L358-L379), [`PageSnapshot.as_prompt`](../backend/app/browser/driver.py#L87-L108) and its use in [`BrowserAgent`](../backend/app/agent/agent.py#L146-L154), screenshot persistence/events in [`TaskExecutor`](../backend/app/workers/executor.py#L1343-L1380), and [`redact`](../backend/app/core/logging.py#L21-L45).

**Impact:** URLs sometimes function as credentials. This path does not mean every URL contains a secret, and no token-bearing live URL was used in testing; it means the system currently has no reliable boundary that prevents one from leaving the worker.

**Recommended action:** Keep the full navigation URL only in worker memory where required. Before persistence, event publication, API serialization, logging, or LLM submission, strip userinfo, query, and fragment by default (or apply a carefully tested, explicit parameter allowlist). Sanitize link `href`s and action results too. Add canary tests using `?access_token=DO_NOT_LEAK#id_token=DO_NOT_LEAK` and assert the values are absent from DB rows, API responses, event payloads, logs, agent-run history, and mocked provider requests.

### A-04 — Compose production defaults permit insecure HTTP/session deployment

**Severity:** High · **Priority:** P1 · **Status:** Confirmed unsafe defaults; live deployment not tested

Compose defaults `APP_ENV` to production but explicitly sets `COOKIE_SECURE=false`, leaves `TRUSTED_HOSTS=*`, keeps self-registration enabled, and publishes the frontend on `${WEB_PORT:-3000}`. The nginx profile is optional and the shipped nginx server listens on port 80; its 443 block is commented and incomplete. The published frontend port remains available even when the proxy profile is enabled. Production validation currently requires a non-default `SECRET_KEY` of at least 32 characters, an encryption key, and PostgreSQL, but does not enforce secure cookies, HTTPS, trusted hosts, disabled registration, or a mock-platform prohibition.

**Evidence:** [`docker-compose.yml`](../docker-compose.yml#L17-L32) and [frontend port publication](../docker-compose.yml#L153-L170), [`Settings` production validation](../backend/app/core/config.py#L192-L206), and [`deploy/nginx.conf`](../deploy/nginx.conf#L20-L22) with its commented example at [lines 64–72](../deploy/nginx.conf#L64-L72).

**Impact:** If deployed with these defaults on an untrusted network, authentication cookies may be sent over plaintext HTTP and can be stolen in transit. Direct access to the frontend can bypass headers supplied only by the optional proxy. Open registration and wildcard hosts increase the exposed surface and abuse potential.

**Recommended action:** Require HTTPS at a tested TLS ingress, set `COOKIE_SECURE=true`, use an explicit trusted-host/origin allowlist, disable registration after bootstrap, and publish only the TLS ingress (keep the frontend port internal). Make insecure settings fail closed in production validation. Add an automated Compose/configuration test for the production profile.

### A-05 — The unauthenticated mock platform is mounted by the production Compose stack

**Severity:** High · **Priority:** P1 · **Status:** Confirmed configuration path

The mock router has generation, job-status, and download handlers without BowserAuto authentication. The API includes that router whenever the environment is not production **or** `mock_platform_url` is set. The production Compose anchor always sets `MOCK_PLATFORM_URL`, so the condition remains true in the supplied Compose stack when `APP_ENV=production`. The frontend proxy also routes `/mock-platform` to the backend.

**Evidence:** [`docker-compose.yml`](../docker-compose.yml#L25-L32), [`create_app`](../backend/app/main.py#L203-L207), and unauthenticated [`mock_platform` handlers](../backend/app/mock_platform/router.py#L87-L128) and [download route](../backend/app/mock_platform/router.py#L131-L141).

**Impact:** A public deployment exposes a test-only surface that allows unauthenticated fake-job creation/status/download and simulated login behavior. It does not directly expose real platform credentials, but it expands the attack and resource-abuse surface and contradicts production isolation expectations.

**Recommended action:** Do not set `MOCK_PLATFORM_URL` in production and gate mounting on an explicit development/test flag that production validation forbids. If the mock is required for a controlled staging workflow, isolate it behind authentication/network policy and add a test asserting it is absent from production OpenAPI and routes.

### A-06 — Live browser profiles are plaintext and share the application data volume

**Severity:** High · **Priority:** P1 · **Status:** Confirmed

Playwright launches a persistent Chromium context with a per-session profile directory. The code sets directory mode `0700`, but the profile files themselves are not application-encrypted. `ENCRYPTION_KEY` encrypts organization LLM keys and serialized storage-state snapshots; it does not encrypt the live profile. Compose mounts the same `app-data` volume into the API, general worker, and browser worker containers, and the containers run as the same unprivileged image user. The API does not intentionally read the profiles in its normal code path, but the shared mount means a compromised API process is not isolated from those files at the filesystem level.

**Evidence:** [`PlaywrightBrowser.launch`](../backend/app/browser/playwright_driver.py#L55-L110), profile creation/permissions in [`session_service`](../backend/app/services/session_service.py#L95-L115), encrypted snapshot save in [`SessionHost.stop`](../backend/app/workers/session_host.py#L177-L200), shared volume mounts in [`docker-compose.yml`](../docker-compose.yml#L80-L113) and [browser worker](../docker-compose.yml#L124-L139), and the non-root user in [`backend/Dockerfile`](../backend/Dockerfile#L29-L34).

**Impact:** A host/volume/backup compromise, or a process compromise in a container with the shared mount, can expose active website cookies and logged-in sessions. Filesystem mode `0700` helps against other host users but not the same UID in the other containers. The previous documentation overstated the protection; the README, security, architecture, and operations documents have been corrected.

**Recommended action:** Use encrypted host/volume storage and encrypted backups immediately. For defense in depth, separate profile storage from the API volume, grant profile access only to the browser worker that needs it, minimize retention, and document recovery behavior. Consider an encrypted profile-at-rest design with explicit key lifecycle and tests rather than implying that the snapshot key protects the live profile.

### A-07 — Rate limiting trusts an untrusted `X-Forwarded-For` value

**Severity:** Medium · **Priority:** P2 · **Status:** Confirmed

`client_ip()` returns the first value in `X-Forwarded-For` whenever the header is present. The global API rate limiter keys requests on this value. The supplied nginx configuration appends the remote address to the incoming header, preserving any client-supplied first value; the frontend proxy also forwards proxy metadata.

**Evidence:** [`client_ip`](../backend/app/core/deps.py#L54-L58), its use in [`RequestContextMiddleware`](../backend/app/main.py#L62-L80), and [`deploy/nginx.conf`](../deploy/nginx.conf#L34-L42).

**Impact:** A client can spoof the IP used for rate limiting and audit attribution, potentially evading per-IP limits or polluting logs. This does not bypass authentication by itself.

**Recommended action:** Trust forwarded headers only when the immediate peer is a configured trusted proxy. Parse the chain from the trusted end (or overwrite inbound forwarded headers at the edge) and test spoofed, repeated, and malformed values through both direct and proxied paths.

### A-08 — Signed download tokens are included in application access logs

**Severity:** Medium · **Priority:** P2 · **Status:** Confirmed

Signed file URLs put the bearer token in the route path (`/api/files/signed/{token}`). The request middleware logs `request.url.path` for API calls, which includes that token. The endpoint intentionally permits possession of the signed URL without a user session; generated preview URLs can be issued with a one-hour lifetime.

**Evidence:** [`RequestContextMiddleware` access log](../backend/app/main.py#L99-L114), [`download_signed`](../backend/app/api/files.py#L43-L58), and [`signed_url`](../backend/app/services/file_service.py#L88-L103); preview issuance is visible in [`api/files.py`](../backend/app/api/files.py#L31-L39).

**Impact:** Anyone who can read application or proxy access logs may be able to use an unexpired URL to download a file. The token is time-limited, but logs often outlive the URL.

**Recommended action:** Log route templates rather than raw paths, redact the signed-token segment before structured and proxy logging, and add a test that verifies a canary signed token is absent from captured logs. Review upstream access-log configuration as well.

### A-09 — File and archive size controls are not end-to-end resource limits

**Severity:** Medium · **Priority:** P2 · **Status:** Confirmed resource-risk paths

The media-fetch fallback calls `response.body()` and holds the entire download in memory before writing it. Generated-file size is checked later in the executor, after the download. Project ZIP export allows a project directory up to 4 GiB, builds the archive in `BytesIO`, then returns the complete bytes object. Uploads have a per-file cap, but it is applied by application code after multipart parsing; the 64 MiB nginx ingress cap applies only when that optional proxy is used, while Compose also publishes the frontend directly. The storage summary reports usage but no organization-wide storage quota or retention limit was found.

**Evidence:** [`PlaywrightDriver.fetch_to_file`](../backend/app/browser/playwright_driver.py#L688-L699), the post-download check in [`TaskExecutor._store_output`](../backend/app/workers/executor.py#L605-L628), [`build_project_zip`](../backend/app/services/file_service.py#L147-L197), upload checks in [`project_service`](../backend/app/services/project_service.py#L260-L312), and [`deploy/nginx.conf`](../deploy/nginx.conf#L18-L24).

**Impact:** Large or concurrent downloads/uploads/exports can consume worker memory, temporary disk, or persistent volume space and cause service degradation or crashes. A per-file cap reduces but does not eliminate this risk.

**Recommended action:** Stream downloads to disk while counting bytes and aborting at the configured limit; cap request bodies at the first trusted ingress; make ZIP export asynchronous and disk-backed/streamed; add organization storage quotas, retention/cleanup policy, per-worker concurrency backpressure, and tests at each boundary.

### A-10 — Organization concurrency limits may be oversubscribed across workers

**Severity:** Medium · **Priority:** P2 · **Status:** Potential; not reproduced

Each worker counts active tasks for an organization, checks the configured limit, and then conditionally claims one queued task. The conditional update prevents two workers claiming the *same* task, but does not serialize different task claims or atomically reserve an organization-wide concurrency slot. Two workers can plausibly both observe available capacity and claim different queued tasks.

**Evidence:** [`Worker._claim_task`](../backend/app/workers/main.py#L157-L223), particularly the active-task count/check at lines 191–208 followed by the per-task update at lines 209–223.

**Impact:** Under concurrent claims, an organization may exceed its configured task limit, consuming more browser/LLM resources than allowed. This is a race analysis, not a reproduced test result.

**Recommended action:** Reserve capacity atomically (for example, a PostgreSQL organization lock/slot row or a correctly leased Redis semaphore), and add a multi-worker concurrency test that starts more tasks than the org limit and asserts the invariant under contention.

### A-11 — Agent safety rules are primarily prompt instructions, not a universal action policy

**Severity:** Medium · **Priority:** P2 · **Status:** Architectural risk; not demonstrated as an exploit

The generic agent sends page text, URL, interactive element descriptions, and optionally a screenshot to the configured model. Its system prompt contains important safety rules, and platform adapters/human intervention checks cover some login and verification states. However, after basic response-shape checking, the agent dispatches any registered tool; the registry invokes the handler and there is no universal policy/approval gate for navigation, clicks, form submission, or other consequential actions. A hostile page can supply prompt-injection content alongside normal page state.

**Evidence:** [`SYSTEM_PROMPT`](../backend/app/agent/agent.py#L25-L36), page context sent to the provider in [`BrowserAgent._decide`](../backend/app/agent/agent.py#L135-L175), and direct registered-tool dispatch in [`ToolRegistry.execute`](../backend/app/agent/tools/__init__.py#L81-L100). The existing verification-challenge test is useful coverage but does not establish safe handling of arbitrary prompt injection.

**Impact:** A model can be induced to take an unintended action or disclose task/page context to the selected provider. The test suite does not show a CAPTCHA/MFA bypass; the concern is that prompt instructions alone cannot reliably enforce policy for every action.

**Recommended action:** Add a deterministic policy layer between model output and tool execution. Use per-organization domain/tool allowlists, require real user confirmation for irreversible/high-impact actions, limit which page-derived values can be sent to the LLM, and record approvals. Keep the existing manual human takeover for authentication/security challenges. Test prompt injection using a local mock page and a mocked provider—never a real site or real generation call.

### A-12 — Frontend dependency audit reports unresolved vulnerabilities

**Severity:** High (scanner rating) · **Priority:** P1 · **Status:** Confirmed audit output; exploitability not manually assessed

`npm audit` reported **12 findings** in the full frontend dependency tree (**8 high, 4 moderate**). `npm audit --omit=dev` still reported **10** (**6 high, 4 moderate**, no critical), including transitive `braces`, `chokidar`, `micromatch`, and PostCSS findings, plus the current Next/Tailwind dependency tree. The resolved tree included Next `15.5.28`, PostCSS `8.4.31` nested under Next, and Tailwind `3.4.19`. No forced major upgrade was applied because npm's suggested Next remediation is a major-version jump and needs compatibility review.

**Evidence:** `npm audit --audit-level=high` and `npm audit --omit=dev --audit-level=high` run against `frontend/package-lock.json` on 2026-10-16. The latter produced 6 high and 4 moderate findings; npm's automated fix suggestions include a major Next upgrade for some paths.

**Impact:** Vulnerable build/runtime dependencies may expose the frontend or its build pipeline to denial of service, XSS, information disclosure, or other advisory-specific impacts, depending on reachability and usage. Reachability and exploitability were not separately tested in this audit.

**Recommended action:** Triage each advisory against production dependency paths, apply supported patch/minor upgrades or carefully tested overrides where possible, review the Next major upgrade separately, regenerate the lockfile, and rerun frontend typecheck/lint/build plus `npm audit` in CI. Do not blindly apply `npm audit fix --force`.

### A-13 — No automated CI gate or frontend test suite was found

**Severity:** Medium · **Priority:** P2 · **Status:** Confirmed repository inventory

The repository scan found no CI workflow/configuration and no frontend test/spec files. The backend has 97 collected pytest cases across 72 test functions; `docs/TESTING.md` previously listed a stale count and has been corrected. The UI snapshot script and recorded browser smoke flows are useful checks but are not a frontend unit/integration test suite or an automatic PR gate.

**Evidence:** Repository workflow/test inventory; [`docs/TESTING.md`](TESTING.md) records the current backend suite and available manual/smoke commands.

**Impact:** Regressions in security-sensitive changes, frontend API flows, migrations, and dependency upgrades can merge without repeatable automated gates.

**Recommended action:** Add CI for Ruff/format, the complete backend suite, frontend typecheck/lint/build, dependency audits, migrations/OpenAPI checks, and a small set of frontend tests for login/CSRF, permissions, provider-key redaction, and URL display. Add integration tests for the P1 controls above.

### A-14 — Frontend security headers depend on an optional proxy

**Severity:** Medium · **Priority:** P2 · **Status:** Confirmed configuration gap

`frontend/next.config.ts` does not configure response headers. Backend middleware applies headers to API responses, not Next-rendered pages. The optional nginx proxy adds several headers, but not a Content Security Policy or HSTS, and the frontend port is also published directly so traffic can bypass nginx. The current static theme script means any CSP must be implemented with a matching hash/nonce rather than disabling the control wholesale.

**Evidence:** [`frontend/next.config.ts`](../frontend/next.config.ts#L9-L16), frontend port mapping in [`docker-compose.yml`](../docker-compose.yml#L153-L170), [`SecurityHeadersMiddleware`](../backend/app/main.py#L34-L50), and the optional proxy headers in [`deploy/nginx.conf`](../deploy/nginx.conf#L28-L43).

**Impact:** Browser defenses against script injection, framing, and insecure transport are inconsistent between API and UI routes and depend on deployment path.

**Recommended action:** Configure and test frontend response headers at the Next/proxy layer, including an application-compatible CSP, frame policy, Referrer-Policy, and HSTS only after HTTPS is enforced. Make the secure ingress the only published path.

## Controls that are working

- API routes reviewed for projects, tasks, sessions, assistant, activity, platform settings, templates, dashboard, and notifications use authenticated dependencies and organization scoping; mutating/admin routes generally have role checks.
- Realtime SSE/WebSocket endpoints authenticate, check session ownership, and gate browser control on write permission/subscription.
- Passwords use Argon2; app sessions are cookie-based with CSRF handling; organization LLM keys and serialized browser-state snapshots use Fernet encryption.
- Production settings reject the development secret, a missing encryption key, and SQLite; these checks do not cover all production-hardening settings noted above.
- Uploads have allowed-extension, file-size, filename/path, and MIME checks; agent filesystem tools are project-folder constrained.
- Automated tests use the fake browser/heuristic mode and do not perform real AI generation or contact real third-party platforms. The verification challenge test asserts that the adapter does not bypass the challenge.

These controls reduce risk but do not offset the unresolved release blockers.

## Original audit recommendations (baseline)

The options below were proposed against source revision `ad36c83`. Many application-layer items have
since been implemented; consult the current remediation table above rather than treating this list
as an outstanding-work checklist. The remaining release work is primarily deployment verification,
active-profile storage protection, and a dedicated frontend test suite.

### Option 1 — Security-first release baseline (recommended; prerequisite for public deployment)

1. Close A-01/A-02 with a single tested egress and destination policy for browser and LLM traffic.
2. Close A-03 with URL redaction at persistence, event, API, log, screenshot metadata, and LLM boundaries.
3. Close A-04/A-05 by enforcing HTTPS/secure cookies, restricting trusted hosts, disabling bootstrap registration, publishing only TLS ingress, and removing the mock router from production.
4. Close A-06 with encrypted storage/backups and least-privilege volume separation.
5. Triage and patch A-12 dependencies; add CI coverage for all release gates.
6. Add deterministic high-impact action confirmations as part of A-11.

**Acceptance gate:** tests prove private/link-local/metadata destinations are blocked on every entry path; canary URL query/fragment secrets are stripped before downstream persistence, events, logs, API responses, and LLM prompts; production startup rejects insecure settings and mock routes; and the deployment smoke test runs behind HTTPS with secure cookies.

### Option 2 — Reliability and scale

- Stream media downloads and exports; enforce request-body and per-organization storage quotas; add retention and cleanup controls.
- Make organization-wide worker capacity claims atomic and prove them with concurrent multi-worker tests.
- Use trusted-proxy IP handling, route-template logging, signed-token redaction, and operational alerts for rejected/oversized transfers.
- Add migration, backup/restore, and worker restart tests to CI; measure storage, memory, queue depth, provider latency, and failure rates.

### Option 3 — Agent/product capabilities after the safety baseline

- Build a typed, permissioned plugin/skill manifest (Mark-LV-inspired) with explicit scopes, validation, versioning, and per-organization enablement—no unrestricted dynamic code loading.
- Add a real approval and undo workflow: classify actions, require confirmation for consequential operations, show the exact pending action, and persist the approval/result in the audit trail.
- Improve session continuity and memory controls with user-visible inspect/export/delete, clear retention, and tenant/user ownership boundaries.
- Extend the JARVIS HUD/voice workflows only when each control is wired to a real API and covered by an end-to-end test; do not ship placeholder buttons or decorative controls.

## Baseline verification and limitations (source revision `ad36c83`)

The following measurements describe the original audit baseline, not the current branch. Current
remediation status and branch checks are summarized near the top of this report and in
[`ROADMAP_STATUS.md`](ROADMAP_STATUS.md).

### Baseline results observed

- Backend: `python -m pytest -q` — **97 passed** in 52.51 seconds; Ruff check and format checks passed.
- Frontend: `npm run typecheck`, `npm run lint`, and `npm run build` passed.
- OpenAPI inventory: **101 operations across 77 paths**.
- Python dependency audit: `pip-audit -r requirements.txt` reported no known vulnerabilities for the versions resolved at audit time. `backend/requirements.txt` is not a frozen lockfile, so this is not a guarantee about every deployment resolution.
- Frontend dependency audit: 12 full-tree findings and 10 with dev dependencies omitted as detailed in A-12.
- Repository inventory: 198 tracked files; no frontend test/spec files or CI workflow/configuration found.

### Limitations

- Docker was unavailable in the audit environment. Compose was inspected but not executed as a live stack; the production exposure findings follow from the checked-in conditional/mount configuration.
- `browser_available()` reported Chromium unavailable during this audit. The fake-browser test suite ran; no real-browser or real-platform penetration test was performed in this pass. Earlier recorded mock workflow/smoke runs are not a substitute for rerunning them in a production-like deployment.
- No real AI provider calls or real generation were made. Provider tests use mocked transports.
- A token-bearing browser URL was not created, and the multi-worker concurrency race was not reproduced. Their data-flow/interleaving paths were established from source.
- NPM advisory reachability, exploitability, and exploit chains were not manually validated; scanner results should be triaged, not treated as proof of exploitation.
- This review is not an external security assessment, compliance review, or formal threat-model sign-off.

## Release recommendation

**Do not describe the current project as production-ready.** It is suitable for continued development and controlled testing in an isolated environment. For internet-facing or multi-tenant use, close all P1 items, implement the recommended P2 controls, and rerun backend/frontend tests plus a Docker/HTTPS/real-Chromium smoke test in a deployment-like environment. Revisit this report after those changes; do not treat a passing UI build as a security sign-off.
