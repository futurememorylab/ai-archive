# Repo Audit & Improvement Plan — catdv-annotator

**Date:** 2026-06-11
**Scope:** full repository at commit `5c555b6` (Cloud Run deployment merge).
**Method:** four-phase audit (discovery → evidence-based findings → strategy → task plan).
Findings were gathered by parallel deep-dives per dimension and the Critical/High
claims were re-verified against source before inclusion. No code was modified.
**Review depth note:** services, repositories, routes, deploy, and tests received deep
review; Jinja templates and the 20 static JS modules received structural (not
line-by-line) review.

---

## Executive Summary

**Overall health: B+ (very good, with two structural debts).** This is an unusually
disciplined codebase for a single-operator tool: 1,408 tests (99.6 % passing), 74 ADRs,
import-linter layer contracts, a complexity-erosion ratchet, an N+1 query-count guard,
and a clean offline-first cache architecture. The internals are sound — parameterized
SQL throughout, secrets in `SecretStr`/Secret Manager, safe subprocess and path
handling. What keeps it from an A is **drift between the codebase and its environment**:
(1) the new Cloud Run deployment quietly outgrew the documented "single operator on a
laptop behind VPN" threat model — the app has no in-app auth, CSRF, or audit logging,
so one IAM binding is the entire security boundary; (2) there is no dependency
lockfile, so every deploy build resolves dependencies fresh; (3) CI runs tests only on
push to `main`, after merge. Top opportunities: a PR-level test workflow, a committed
lockfile, and a thread-hop around the one verified event-loop-blocking call (the
synchronous GCS upload). All three are small.

**Top 3 risks**

1. Cloud Run exposure vs. zero in-app auth/CSRF/audit (threat-model drift).
2. No lockfile → non-reproducible builds; a bad upstream release breaks the next deploy.
3. Synchronous multi-GB GCS upload called from `async def` — freezes the event loop
   (every request, the health probe, the connection monitor) for the upload duration.

**Top 3 opportunities**

1. PR-triggered CI (tests + ruff + basedpyright + erosion gate) — feedback before merge.
2. Quick wins batch: lockfile, `executemany` in job creation, `.env.example` placeholder,
   README refresh — all under 2 h each.
3. Refactor the three CC>20 route/composition hotspots before the erosion ratchet locks
   them in as the new normal.

---

## Repo Map

**Purpose.** Local-first web app for the Pragafilm CatDV video archive: runs AI
annotation jobs against Gemini (Vertex AI), caches proxies/thumbnails locally, queues
writes for offline-safe writeback to CatDV. Single operator. Recently grew a Cloud Run
deployment (WireGuard tunnel to the CatDV LAN, Litestream-replicated SQLite).

**Stack.** Python 3.12/3.13, FastAPI + uvicorn, aiosqlite (WAL), Jinja2 + HTMX +
Alpine.js (vendored, no Node toolchain — ADR 0001), google-genai + google-cloud-storage,
sse-starlette. Tests: pytest(-asyncio) with composable in-process fakes.

**Architecture sketch.** `routes/` (HTTP + Jinja partials) → `services/` (orchestration:
write queue, sync engine, connection monitor, caches, annotator) → `repositories/`
(raw SQL, one module per table) + `archive/` (ports/adapters: `ArchiveProvider` for
CatDV/FS, `AIInputStore` for GCS). Two composition roots: `CoreCtx` (always present)
and `LiveCtx` (online services), built in `backend/app/context.py`, enforced by
`.importlinter` + guard tests (ADR 0047).

| Area | One-liner |
|---|---|
| `backend/app/services/` (35 modules) | Orchestration: annotator, write_queue, sync_engine, 3 cache layers, connection/VPN monitors |
| `backend/app/repositories/` (24 modules) | Raw-SQL repos; `_batch.py` chunked `WHERE IN` helper |
| `backend/app/routes/` (17 modules + `pages/`) | API + page routers; HTMX partial rendering |
| `backend/app/archive/` | Provider ports/adapters (CatDV REST, filesystem, GCS AI store) |
| `backend/app/templates/`, `static/` | 80+ Jinja templates, 20 JS modules (~3.3k LOC), shared `_ui.html` macro library |
| `tests/` (~29.4k LOC) | unit / integration / contract + fakes; 9 "guardrail" ratchet tests |
| `docs/` | 74 ADRs (0001–0076; 0026/0027 never created), specs, plans, ARCHITECTURE/CONTEXT triage docs |
| `deploy/`, `.github/workflows/deploy.yml` | Cloud Run: single instance, Litestream, onetun WireGuard, Secret Manager |
| `tools/` + `.erosion-baseline.json` | Complexity-concentration ratchet (baseline erosion 0.4229, max_cc cap 30) |

**Surprises found during discovery**

- The repo is named `ai-archive` but the project is `catdv-annotator` everywhere else.
- `README.md:6` still says "**Backend only at this point.** UI is Plan B" — there are 80+
  templates and a full Studio UI.
- Backend is ~18.7k LOC; tests are ~29.4k LOC (1.6 : 1 test-to-code ratio).
- ADR numbering skips 0026–0027 with no sentinel file (the 0011 gap got one; this one
  didn't — git history shows the files never existed).

---

## Audit Report

Severity legend: **C** Critical · **H** High · **M** Medium · **L** Low.
"Fact" = verified in source; "Judgment" = assessment.

### Security

| # | Sev | Finding |
|---|---|---|
| S1 | **C** | **Threat-model drift: Cloud Run exposure with no in-app auth, CSRF, or audit logging.** Fact: the FastAPI app registers no auth middleware (`backend/app/main.py:97-184`); ~50 state-mutating POST/DELETE endpoints (studio, cache eviction, sync, prompts) are protected solely by the Cloud Run IAM gate (`--no-allow-unauthenticated`, `.github/workflows/deploy.yml:60`). README's stated threat model (`README.md:94-113`) is "single operator, own laptop, behind VPN" and predates the deployment. Judgment: one misconfigured IAM binding, leaked credential, or future "let's just open it up" change removes the entire security boundary in one step. Consequence: destructive operations (evict cache, delete sets, write back to CatDV) become internet-invokable with no second factor and no audit trail of who did what. |
| S2 | **H** | **`GEMINI_API_KEY` shipped to the browser — accepted risk (ADR 0043) whose context changed.** Fact: `/api/live/session-config` returns the raw key (`backend/app/routes/live.py:46-85`, `services/live_sessions.py:91-112`); a boot warning exists (`startup.py:62-79`). Judgment: acceptable on a VPN'd laptop; on Cloud Run the key crosses the public internet and sits in an internet-reachable response. At minimum, document "don't set `GEMINI_API_KEY` on Cloud Run"; the real fix is the WSS proxy alternative already named in ADR 0043. |
| S3 | **M** | **Internal network details committed.** Fact: `.env.example:8` hardcodes the real internal CatDV server `http://192.168.1.41:8080` (also echoed in `CLAUDE.md`). Consequence: repo readers learn LAN topology. Trivial fix: placeholder host. |
| S4 | **L** | **One f-string SQL construction** in `backend/app/services/clip_list_filters.py:73-77`. Fact: the interpolated fragment is one of two hardcoded literals — *not injectable today*; it's a refactoring landmine and linter-bait, nothing more. |
| S5 | **L** | Upload endpoint trusts the browser's MIME header (`routes/studio.py:140-182`): allowlist + size cap exist, no magic-bytes check. Files are never executed; operator uploads their own media. Fine for this maturity. |

Healthy: SQL is parameterized everywhere (incl. `_batch.py`), Jinja autoescape on,
`ffprobe` subprocess uses list-args with timeout, path construction for media/uploads is
int-keyed (no traversal), secrets use `SecretStr` + Cloud Run Secret Manager, `.env` is
git- and docker-ignored.

### Performance & async hygiene

| # | Sev | Finding |
|---|---|---|
| P1 | **H** | **Event-loop-blocking GCS upload.** Fact (re-verified): `GcsService.upload_if_absent` (`backend/app/services/gcs.py:38-53`) is synchronous — full-file chunked MD5 (`gcs.py:17-23`) plus a resumable upload with `timeout=1800` — and is called directly from `async def ensure_uploaded` at `backend/app/archive/ai_stores/gcs/adapter.py:59` with **no** `asyncio.to_thread`. Every other GCS call site in the codebase does the thread hop (`media_locator.py:91`, `thumbnail_store.py:36,43`, `media_cache.py:111`); this one slipped through (the sync-fs guard test only catches *filesystem* calls). Consequence: during a multi-hundred-MB proxy upload the entire app — every page, `/api/health`, the connection monitor that decides online/offline — is frozen. On Cloud Run this can cascade into the instance being marked unhealthy. |
| P2 | **M** | **Per-clip INSERT loop in job creation.** Fact: `backend/app/repositories/jobs.py:38-42` loops one `INSERT` per clip. Correction to the raw finding: it is one transaction with a single commit (`jobs.py:43`), so the cost is N await-round-trips, not N commits — real but modest. `executemany` is a 5-line fix. |
| P3 | **M** | **Studio run polling: fixed 1 Hz, no backoff, no jitter, plus an unconditional 1 Hz UI ticker** (`backend/app/static/studioStore.js:432-446`, `:140-153`). Fact. Judgment: tolerable single-user; wasteful on Cloud Run (CPU always allocated, but each poll renders a run-status query). SSE infrastructure (`services/events.py`, sse-starlette) already exists and is unused here. |
| P4 | **M** | **O(n²) pending-review query.** Fact: `repositories/review_items.py:183-214` re-scans the `pending` CTE per output row via a correlated `MAX()` subquery; `pending_clip_ids_for_jobs` (`:216-237`) returns unbounded lists. Judgment: invisible at hundreds of items, painful at tens of thousands. A window function rewrite is mechanical. |
| P5 | **L** | LRU eviction does a full `proxy_cache` scan + fetchall every tick with no `last_used_at` index (`services/lru_eviction.py:143-155`); `studio_runs` rows are never pruned (intentional history, but reads have no `LIMIT`); EventBus drops oldest events silently on overflow (`services/events.py:30-36`). All acceptable at current scale; named here so they're deliberate, not accidental. |

Healthy: WAL + busy_timeout + FK enforcement on the SQLite connection (`db.py`);
`chunked_in_clause` prevents parameter blowups; sync-fs-in-async guard test with audited
pragmas; Vertex calls and large file writes correctly threaded
(`annotator.py:460`, `routes/studio.py:191`, `catdv_client.py:283`).

### Architecture & code quality

| # | Sev | Finding |
|---|---|---|
| A1 | **M** | **Complexity is concentrating in three functions, and the ratchet is at its cap.** Fact: `studio_page` CC=30 (`routes/pages/studio.py:38-131`), `clips_list` CC=26 (`routes/pages/clips.py:175-295`), `_build_archive_subsystem` CC=21, 235 lines (`context.py:457-691`). The pre-commit erosion gate runs with `--max-cc 30` (`.pre-commit-config.yaml:38-42`) against baseline `{erosion: 0.4229, max_cc: 26}` — so `studio_page` sits *exactly at* the hard cap. Consequence: the next edit to that function fails the gate or pressures someone to raise the cap; refactoring now is cheaper than negotiating with the ratchet later. |
| A2 | **M** | **ADR 0042 violated in three places.** Fact: `routes/pages/studio.py:194-205, 318-330, 440-490` catch bare `Exception` around `archive.get_clip()` without consulting `is_provider_not_found()` — the exact pattern the project's own error-handling discipline (ADR 0042, `archive/errors.py`) forbids, and which `routes/pages/clips.py:498` gets right. Consequence: genuinely-deleted clips are indistinguishable from transient blips; orphaned cache entries accumulate. |
| A3 | **M** | **`LiveCtx` delegation boilerplate**: 28 consecutive single-line `@property` forwarders (`context.py:241-350`). Fact. Judgment: the drift-guard test (`test_context_delegation.py`) makes this safe, but every new `CoreCtx` field costs two edits; a `__getattr__` delegator or codegen comment block would halve the friction. Low urgency — guarded debt. |
| A4 | **M** | **Build-order coupling via untyped holder.** Fact: `build_context` threads a `monitor_holder: dict` between subsystem builders; the `_is_online` closure (`context.py:576-584`) silently returns `True` until the monitor is assigned at `:729`, and teardown ordering (VPN after CatDV logout, `context.py:352-373`, ADR 0075) is enforced only by comments. Judgment: works today by discipline; a named holder class plus an assertion would make the contract survivable across refactors. (Raw finding called this Critical; downgraded — the ordering is documented and currently correct.) |
| A5 | **L** | Batch status/cost aggregation maps are hand-built in near-identical loops in `routes/pages/clips.py:257-288` and the studio/review pages — a shared service helper would prevent the three views drifting apart. GCS failure logs drop the bucket/clip context (`thumbnail_store.py:37,44`) instead of routing through `services/errors.py::humanise`. |

Healthy: all five import-linter contracts pass; no unused-import/dead-symbol findings
(pylint clean); one Jinja env, one HTMX↔Alpine lifecycle helper, `Alpine.store` discipline
— each backed by a guard test that actually enforces it.

### Testing & CI

| # | Sev | Finding |
|---|---|---|
| T1 | **H** | **Tests run in CI only after merge.** Fact: `.github/workflows/deploy.yml:3-6` triggers on `push: [main]` + manual dispatch only; no PR workflow exists. The test job also omits `ruff`, `basedpyright`, `interrogate`, and the erosion gate (those run only in local pre-commit, which is opt-in). Consequence: a contributor without pre-commit installed lands broken code on `main`, and the first signal is a failed *deploy*. |
| T2 | **M** | **Frontend is functionally untested.** Fact: ~3.3k LOC across 20 `static/*.js` modules (player transport, studio store, drag-resize, pickers) have zero unit/E2E tests; coverage is static greps + `assert "…" in r.text` template checks. Judgment: the highest-regression-risk surface (Alpine reactivity, HTMX swaps) is guarded only by manual clicking — partially mitigated by the specs' Manual Acceptance Flows convention. |
| T3 | **M** | **Core services lack isolated unit tests.** Fact: `annotator.py` (prompt rendering, timecode clamping, telemetry capture), `catdv_client.py` (session/reauth state machine), and `gemini.py` happy path are exercised only through integration pipelines. Failures localize slowly. |
| T4 | **M** | **Timing-dependent tests.** Fact: 19 fixed `sleep(0.02–0.3s)` waits across `test_connection_monitor_halt_and_retry.py`, `test_media_prefetcher.py`, `test_vpn_supervisor.py`. Consequence: intermittent failures on slow CI runners; will be felt the day T1 is fixed and CI runs on every PR. |
| T5 | **L** | One environment-dependent failure: `test_context_manual_boot.py::test_manual_mode_does_not_login_at_boot` fails without GCP ADC credentials (`DefaultCredentialsError`) — the test reaches a real `google.auth` lookup instead of a fake. Also: no coverage measurement is configured at all (no pytest-cov), so "what's untested" is folklore. |

Healthy — genuinely excellent: 1,408 tests, 99.6 % pass in 2m43s; fake-first design
(in-process FastAPI CatDV fake with real session semantics, no MagicMock sprawl);
the nine guardrail ratchets all exist and enforce what they claim; the N+1 guard
asserts identical query counts at 10/100/1000 clips.

### Dependencies, DevEx, operations, docs

| # | Sev | Finding |
|---|---|---|
| D1 | **H** | **No dependency lockfile.** Fact: only `>=` floors in `pyproject.toml:6-21`; no `uv.lock`/`requirements.txt` anywhere; CI (`deploy.yml:23`) and the `Dockerfile` both `pip install -e .` fresh. Consequence: every deploy image is built against whatever PyPI serves that day — an upstream breaking release (google-genai is at `>=0.3` and moves fast) breaks deploys with no code change, and rollback rebuilds don't reproduce the old image. |
| D2 | **M** | **Single-instance invariant is convention, not policy.** Fact: `--min-instances=1 --max-instances=1` lives in the workflow (`deploy.yml:52-60`) with an ADR-0066 comment; nothing stops a future edit scaling to 2 — at which point two SQLite writers + two Litestream replicators + two sync-engine drainers + two CatDV seats corrupt things in four distinct ways. A boot-time guard or a CI assertion on the flag would make the invariant self-defending. |
| D3 | **M** | **Stale README.** Fact: `README.md:6` ("Backend only… UI is Plan B") and the Status section (`:141-146`, dated-May plans) contradict the shipped product. First-contact documentation misleads new contributors within ten lines. |
| D4 | **L** | Vendored JS untracked: `static/vendor/htmx.min.js` (≈1.9.10 by string inspection) and `alpine.min.js` (version unidentifiable) have no recorded version/source, so security advisories can't be checked against them. A `VERSIONS.txt` fixes it. |
| D5 | **L** | Minor ops nits: Dockerfile pins `python:3.13-slim` while `requires-python = ">=3.12"` (version skew between local 3.12 venvs and prod 3.13 — judgment: low, not the Critical the raw finding claimed); no `HEALTHCHECK` in the Dockerfile (Cloud Run probes independently; matters only for local Docker); Litestream restore semantics (`-if-db-not-exists`) undocumented in DEPLOY.md; ADR index jumps 0025→0028 with no sentinel (cf. the 0011 precedent); pre-commit hooks use `language: system` + `.venv/bin/…` paths that fail confusingly before first `./run.sh`. |

Healthy: documentation is a standout strength — 74 ADRs cross-referenced from code
comments, a symptom→file triage table in ARCHITECTURE.md, a domain glossary, dated
specs with manual acceptance flows. Offline-first degradation (forced and automatic) is
real and tested. `python-json-logger` was suspected unused and verified used
(`logging_setup.py:8` imports `pythonjsonlogger`).

---

## Improvement Strategy

**Theme 1 — Close the gap between deployment reality and threat model.**
(S1, S2, D2.) Target state: the Cloud Run service is safe *even if* the IAM gate is
misconfigured once: a lightweight in-app check (signed session or static bearer token
for the single operator), CSRF protection on state-mutating endpoints, audit logging of
POST/DELETE with identity, `GEMINI_API_KEY` documented as local-only (or WSS proxied),
and the single-instance invariant asserted at boot. Principle: *defense in depth
proportional to exposure — one binding should never be the whole story.*

**Theme 2 — Make the existing quality gates fire before merge, reproducibly.**
(T1, D1, T4, T5.) Target state: a PR workflow runs pytest + ruff + basedpyright +
lint-imports + erosion gate from a committed lockfile; the GCP-credential-dependent
test is hermetic; sleeps replaced by event-driven waits. Principle: *the project
already built the gates — they just need to be in the road.*

**Theme 3 — Async and complexity hygiene at the hot boundaries.**
(P1, P2, A1, A2.) Target state: zero synchronous network/hash work on the event loop;
the three CC>20 functions decomposed below the ratchet cap; the three ADR-0042
violations narrowed. Principle: *fix the spots that contradict the project's own
written discipline — the rules are right, enforcement just missed these.*

**Theme 4 — Give the frontend a safety net proportional to its size.**
(T2, T3.) Target state: a handful of Playwright smoke flows mirroring the specs'
manual-acceptance flows, plus isolated unit tests for annotator prompt-rendering and
the CatDV client session state machine. Principle: *test the seams that static guards
can't see.*

**Deliberately NOT recommending** (effort vs. payoff at this maturity):
multi-user auth/RBAC, migrating off SQLite, an npm/bundler frontend build (ADR 0001
stands), magic-bytes upload validation, EventBus durability, studio-runs pruning, and
the LiveCtx delegation rework (guarded debt, revisit only if it causes a real bug).
Polling→SSE for studio runs is optional polish, not strategy.

**"Done" signals**
- CI fails PRs on test/lint/type/contract/erosion violations; deploys build from a lockfile.
- Zero Critical findings: in-app auth + CSRF live on Cloud Run, or the service is
  formally re-scoped to local-only and the deploy path is documented as experimental.
- `rg "upload_if_absent" backend/ | grep -v to_thread` only matches the definition.
- Erosion baseline max_cc ratcheted *down* (e.g. cap 26) after hotspot refactors.
- A coverage report exists in CI (target: informational first, ≥70 % line on
  `services/` after one quarter).

---

## Task Plan

### Quick wins (do immediately — all S effort, low risk)

| ID | Task | Files | Acceptance |
|---|---|---|---|
| QW1 | Wrap `upload_if_absent` call in `asyncio.to_thread` | `archive/ai_stores/gcs/adapter.py:59` | Upload of a large file no longer stalls `/api/health`; existing tests green |
| QW2 | `executemany` in `JobsRepo.create_job` | `repositories/jobs.py:38-42` | Query-count test added: job creation statement count is O(1) in clip count |
| QW3 | Placeholder host in `.env.example` (drop `192.168.1.41`) | `.env.example:8` | No real internal IPs in tracked files |
| QW4 | README refresh: delete "Backend only" line, update Status section | `README.md:6,141-146` | README describes the shipped UI accurately |
| QW5 | `docs/adr/0026_SKIPPED.txt` + `0027_SKIPPED.txt` + index note | `docs/adr/`, `docs/decisions.md` | Numbering gap is self-explaining (matches 0011 precedent) |
| QW6 | `static/vendor/VERSIONS.txt` (htmx, alpine, fonts: version + source URL) | `backend/app/static/vendor/` | Advisory checks possible without decompiling minified JS |

### Milestone 0 — Safety net (before touching behavior)

| ID | Task | Effort | Risk | Deps |
|---|---|---|---|---|
| M0.1 | **PR CI workflow**: `test.yml` on `pull_request` running pytest, ruff, basedpyright, lint-imports, interrogate, erosion gate; keep deploy.yml gating on it | M | Low | — |
| M0.2 | **Commit a lockfile** (`uv lock` recommended); CI + Dockerfile install from it; document refresh procedure | M | Low — but first lock may surface latent upgrades; pin and test | M0.1 |
| M0.3 | Make `test_context_manual_boot` hermetic (fake the `google.auth` default-credentials path) | S | Low | — |
| M0.4 | Replace the 19 fixed test sleeps with event-driven waits (`asyncio.Event`/polling-with-deadline helper); add `pytest-timeout` defaults | M | Low | M0.1 (so flakes are visible) |
| M0.5 | Add pytest-cov, publish coverage as informational CI artifact | S | None | M0.1 |

### Milestone 1 — Critical fixes (security & correctness)

| ID | Task | Effort | Risk | Deps |
|---|---|---|---|---|
| M1.1 | **Decide & document the Cloud Run threat model** (open question Q1), then: minimal in-app auth (single-operator bearer/session), CSRF middleware on POST/DELETE, audit log (identity + endpoint + timestamp) for mutations | L | Medium — touches every form/fetch; do behind a setting defaulting off locally | Q1 answered |
| M1.2 | Boot-time single-instance assertion (refuse to start if another writer holds the Litestream/DB lease) + CI check that deploy flags still say `max-instances=1` | M | Low | — |
| M1.3 | Narrow the three bare `except Exception` blocks in `routes/pages/studio.py` to use `is_provider_not_found()` per ADR 0042; add regression tests | S | Low | — |
| M1.4 | `GEMINI_API_KEY` on Cloud Run: either hard-block (refuse to mint when `media_cache=="ai_store"`) or implement the ADR-0043 WSS proxy alternative; update ADR | M (block) / XL (proxy) | Medium | Q2 answered |

### Milestone 2 — High-leverage improvements

| ID | Task | Effort | Risk | Deps |
|---|---|---|---|---|
| M2.1 | Refactor `studio_page` (CC 30): extract version/compare selection into a tested pure helper | M | Medium — behavior-preserving; lean on route tests | M0.1 |
| M2.2 | Refactor `clips_list` (CC 26): extract batch status/cost aggregation into a shared service used by clips/studio/review (also resolves A5) | M | Medium | M0.1 |
| M2.3 | Refactor `_build_archive_subsystem`: collapse the three identical login-failure handlers; introduce a typed monitor-holder with an assertion replacing the silent `True` | M | Medium — startup path; integration tests cover boot modes | M0.1 |
| M2.4 | Ratchet the erosion baseline down (max_cc 30 → ≤26) once M2.1–M2.3 land | S | None | M2.1–2.3 |
| M2.5 | Unit tests for `annotator` prompt-rendering/clamping/telemetry and the `catdv_client` session state machine | M | None | — |
| M2.6 | Playwright smoke suite: 4–6 flows lifted from the specs' Manual Acceptance Flows (clip list → detail → player; annotate dropdown; studio run; draft review accept) | L | Low | M0.1 |

### Milestone 3 — Quality & polish

| ID | Task | Effort | Risk |
|---|---|---|---|
| M3.1 | Window-function rewrite of `review_items` pending query + `LIMIT`/pagination on unbounded reads | M | Low |
| M3.2 | Studio polling: add backoff + jitter, or move to the existing SSE bus | M | Low |
| M3.3 | Index `proxy_cache(last_used_at)`; route GCS error logs through `humanise()` with bucket/clip context | S | Low |
| M3.4 | Docs pass: DEPLOY.md Litestream restore semantics, pre-commit-before-first-run onboarding note, parameterize Dockerfile Python version | S | None |
| M3.5 | Align `requires-python` with the deployed 3.13 (or add a 3.12+3.13 CI matrix) | S | Low |

### Top-3 implementation sketches

**QW1 — thread-hop the GCS upload.** In `archive/ai_stores/gcs/adapter.py:59` change to
`gcs_uri = await asyncio.to_thread(self._gcs.upload_if_absent, clip_id=clip_id, local_path=local_path, mime=mime)`.
Gotchas: keyword args pass through `to_thread` fine; confirm no caller relies on the
call being atomic with the surrounding repo writes (it isn't today — same interleaving,
minus the freeze); add a unit test asserting the adapter awaits a threaded call (patch
`asyncio.to_thread`). Consider also threading `self._gcs.delete` if it's on an async path.

**M0.1/M0.2 — PR CI + lockfile.** New `.github/workflows/test.yml` on
`pull_request` + `push: [main]`; job mirrors deploy.yml's test job plus
`ruff check`, `basedpyright`, `interrogate -c pyproject.toml`, and
`python tools/erosion_gate.py --path backend --baseline .erosion-baseline.json --max-cc 30`.
For the lockfile: `uv lock` → commit `uv.lock` → CI uses `uv sync --frozen`;
Dockerfile gets a `COPY uv.lock pyproject.toml` layer + `uv sync --frozen --no-dev`
before the source copy (better layer caching too). Gotcha: first lock will pick current
latest versions — run the full suite against the locked set before merging; pre-commit's
`language: system` hooks are unaffected.

**M1.1 — minimal auth + CSRF for Cloud Run.** Add `APP_AUTH_TOKEN: SecretStr | None`
to settings; middleware (after the timing middleware) that, when set, requires either a
signed session cookie (set via a tiny `/login` form) or `Authorization: Bearer` —
exempting `/api/health` and `/static/*`. CSRF: double-submit cookie checked on
unsafe methods; HTMX sends it via one `hx-headers` attribute on `<body>` in
`layout.html`, and the handful of raw `fetch()` calls (`studioStore.js`,
`liveSession.js`, `cacheActions.js`) get a shared header helper in `format.js` or a
fetch wrapper. Audit log: in the same middleware, `logger.info` method/path/identity
for non-GET. Gotchas: keep it default-off so local dev and the 1,408 tests don't need
tokens; add integration tests for 401/403 paths; SSE endpoint needs the cookie path,
not the header path.

---

## Open Questions (need a human decision)

1. **Q1 — Who may reach the Cloud Run service, ever?** If it's permanently
   "operator-only via IAM," M1.1 can shrink to CSRF + audit log + a documented IAM
   review cadence. If teammates or a second device are coming, do M1.1 in full.
2. **Q2 — Is Gemini Live needed on the Cloud Run deployment**, or is it a
   local-laptop feature? Determines M1.4's cheap path (hard-block) vs. the XL proxy.
3. **Q3 — Expected archive scale** (clips in `proxy_cache` / pending review items)?
   Decides whether M3.1/M3.3 are polish or should be promoted.
4. **Q4 — Repo naming**: is `ai-archive` (GitHub) vs `catdv-annotator` (everything
   else) intentional? Cheap to align, confusing to leave.
5. **Q5 — Appetite for a Playwright dependency** given ADR 0001's no-Node stance?
   M2.6 needs Node for tooling only (not shipped frontend) — worth an ADR either way.
6. **Q6 — Deprecation check**: `services/prompt_compare.py`, `output_compare.py`,
   `word_diff.py` overlap in purpose (diffing); confirm all three are live before any
   consolidation is considered.
