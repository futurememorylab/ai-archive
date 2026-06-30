# Architecture Invariants

The short list. These are the facts that are **true for the life of the
system** — the rules a change must not break without a deliberate,
reviewed decision to change the rule itself. Read this page to understand
the architecture; read `docs/adr/` only when you need the *why* behind a
specific invariant (each one footnotes the ADRs that established it).

This page is the canon. `CLAUDE.md` (agent-facing red flags),
`docs/ARCHITECTURE.md` (layer map), and `docs/CONTEXT.md` (glossary) all
defer to it. If an invariant here and prose elsewhere disagree, **this
page wins** — and the disagreement is a bug to fix.

Each invariant is: the **rule**, *why* it holds, **Enforced by** (the
test / lint / type that fails if you break it — `—` means convention
only), and the ADRs it came from.

How to change an invariant: you don't edit it in a feature PR. You write
an ADR that explicitly supersedes it, update this line, and move the old
text to the ADR. See `docs/decisions.md` → *How decisions are tracked*.

---

## Stack & layering

**1. Python-only, server-rendered.** Jinja2 + HTMX + Alpine.js + Tailwind
standalone CLI. No Node runtime, no SPA, no build step beyond the Tailwind
CLI. The UI is forms plus one video screen; a JS framework is overkill for
a single maintainer.
*Enforced by:* convention + absence of `package.json`/bundler.
*ADRs:* 0001.

**2. Layer boundaries are one-directional and linted.** `routes/` →
`services/` / `repositories/`; `services/` never imports `routes/`;
`repositories/` are leaves (no services); `models/` import nothing
app-internal. Routes may not reach into archive adapter internals
(`archive.providers`/`registry`/`ai_stores`) — only the pure
`archive.errors` / `archive.model`. Routes must not import `httpx` (go
through the archive/client layer). One Jinja environment
(`routes.pages.templates`), imported, never re-instantiated.
*Enforced by:* `.importlinter` (`lint-imports`),
`tests/unit/test_templates_shared.py`.
*ADRs:* ARCHITECTURE.md; 0042 (the no-`httpx`-in-routes rationale).

**3. Two distinct external ports.** `ArchiveProvider` ("where clips and
metadata live": CatDV REST or FS sidecar) is separate from `AIInputStore`
("where Gemini reads media bytes from": GCS or Gemini Files). The app
talks to the Protocols and branches on declarative **capability flags**,
never `isinstance` on an adapter. Switching one port is one adapter swap
that doesn't cascade into the other.
*Enforced by:* convention (Protocol shapes) + capability flags in
`archive/model.py`.
*ADRs:* 0002, 0007.

## Caching & offline

**4. Three independent cache layers, each with one responsibility.**
Proxy cache (`ProxyResolver`), AI-input store (`AIInputStore`), thumbnail
cache (`ThumbnailService`). Each answers "does this clip's media exist
here, and where?" and owns `get_or_fetch` / `status` / an
`is_online_provider` gate. Code that needs media calls the right layer and
lets it decide hit-vs-fetch — it never calls `catdv.download_*` or
`_gcs_service` directly, and never hand-rolls an `if cached_file.exists()`
guard. A new clip-touching cache (e.g. waveforms) is added as a *new layer
in the same shape*, not wedged into an existing one.
*Enforced by:* convention; the canonical pattern is
`services/annotator.py::_process_item`.
*ADRs:* 0006, 0021, 0065, 0069, 0071, 0072.

**5. Offline-first: every cache miss degrades gracefully.** A miss returns
`None` or a clear, layer-named error — never a crash or a silent stale
read. The app stays fully navigable with no network; clips that *are*
cached stay usable. CatDV-offline/GCS-online and the reverse are both
first-class states the code must handle, not just "all online" / "all
offline".
*Enforced by:* convention; `is_online_provider()` gates + `status()`
DB-lookups (no network).
*ADRs:* 0015, 0017, 0023, 0068, 0073.

**6. Media writes never serve stale bytes.** GCS proxy upload is
content-aware (not presence-only), so a reused clip id can't serve an
orphaned blob; uploads are reference-counted and orphan-GC'd on set
removal; large transfers over the cloud tunnel resume by Range and verify
completeness before an upload is considered done.
*Enforced by:* convention; `repositories/ai_store_files.py`.
*ADRs:* 0070, 0081, 0082, 0087.

## Context & wiring

**7. Two contexts, chosen at the route edge — no god-context.** `CoreCtx`
(always present: settings, db, repos, write queue, event bus, DB-first
cache inspector/actions) and `LiveCtx` (composes CoreCtx, adds the wired
CatDV/Gemini/GCS services). Routes declare what they need via
`Depends(get_core_ctx)` / `get_live_ctx` (the latter returns a typed 503
when offline). **No** Optional-service fields on a single context, **no**
`attach_provider` / `attach_ai_store` late-binding, **no**
`assert ctx.foo is not None`. The offline/online contract is a *type*.
*Enforced by:* `tests/unit/test_context_delegation.py` (CoreCtx-fields ⊆
LiveCtx-accessors drift guard).
*ADRs:* 0020, 0021, 0047.

**8. All background work is a lifespan-owned DB-backed claim worker; routes
never spawn execution.** Both the cache queue (`MediaPrefetcher`) and the
annotation/studio runner (`JobRunner`) share one shape: start / poll /
claim-CAS / orphan-resume / stop. Routes only insert `pending` rows (and, for
cancel, call `job_runner.cancel`); execution is owned by the lifespan, not the
request handler. Orphaned `running` rows are **resumed** on worker start, not
cancelled — `run_job` is idempotent, so only unfinished items re-run.
*Enforced by:* `tests/unit/test_jobs_no_route_spawn.py`;
`services/media_prefetcher.py` + `services/job_runner.py`.
*ADRs:* 0086, 0114, 0125.

## Writes & connectivity

**9. Archive writes go through a durable journal.** Mutations are
enqueued as `ChangeOp` rows in `pending_operations`; `SyncEngine` drains
them only while `ConnectionMonitor` says online. Retries honour a uniform
ceiling and per-row backoff; appends are idempotent and accumulate
(multiple appends to one target don't clobber each other); conflicts
re-base on the live clip's freshest ETag rather than re-conflicting
forever; terminal failures flip status + attempts in one atomic SQL.
Notes/bigNotes write to top-level clip properties, not the user-fields map.
*Enforced by:* convention; `repositories/pending_operations.py`,
`services/sync_engine.py`, crash-recovery in `context.build()`.
*ADRs:* 0004, 0090, 0091, 0093, 0094, 0097, 0098.

**9a. Multi-statement DB writes hold `ctx.write_lock`.** Any call passing
`commit=False` to a repo must be inside an `async with ctx.write_lock:`
block — the lock spans first-DML through `commit()` so a concurrent writer
can't prematurely commit the half-finished transaction on the shared
`aiosqlite.Connection`. Single-statement `commit=True` writers don't take
the lock (one execute+commit is indivisible). No network awaits inside the
lock. `SyncEngine._handle_result` exceptions route through `_retry_or_fail`
so rows never strand `in_flight` mid-run.
*Enforced by:* `tests/unit/test_write_lock_guard.py` (AST scan for
`commit=False` outside `write_lock`); `tests/integration/test_sync_engine.py`
(stranding test).
*ADRs:* 0126.

**10. Clip seat discipline (CatDV 2-seat limit → assume 1).** One in-flight
CatDV session; graceful `SIGTERM` shutdown runs `lifespan` →
`ctx.aclose()` → `DELETE /session` to release the seat. `kill -9` leaks the
`JSESSIONID`. uvicorn's graceful-shutdown is bounded so open streams can't
hold the seat. Boot-time login is bounded by a short separate timeout and
keeps the client alive for retry on failure.
*Enforced by:* convention (see `CLAUDE.md` → CatDV session discipline) +
the bounded-timeout code paths.
*ADRs:* 0023, 0024, 0032, 0041.

## Error handling

**11. Absence requires evidence; generic exceptions are transient.**
"This clip is gone" is decided *only* by
`archive/errors.py::is_provider_not_found(exc)` (recognises `NotFoundError`
and `httpx 404`). Anything else is "try later" — never marked terminal.
Bare `except Exception` is allowed only in event-loop watchdogs; catch
`Exception`, not `BaseException` (the latter swallows `CancelledError`).
A filtered list skips an un-hydratable clip rather than 502-ing the page.
*Enforced by:* convention; the `is_provider_not_found` chokepoint.
*ADRs:* 0042, 0088.

**12. User-facing error strings go through `humanise()`.**
`services/errors.py::humanise(exc)` produces a non-empty, actionable
string for any exception. Never surface `str(exc) or exc.__class__.__name__`
to a user (it returns `'HTTPStatusError'` for the most common SDK
failures).
*Enforced by:* convention.
*ADRs:* 0042.

## Performance

**13. No N+1 reads.** Any repository method taking a list of keys uses
`repositories/_batch.py::chunked_in_clause` (`WHERE (a,b) IN (…)`,
chunked under SQLite's param limit) instead of looping. New per-key
hydration methods ship with a query-count test asserting the same
statement count for 10 / 100 / 1000 keys.
*Enforced by:* `tests/_helpers/query_count.py::assert_query_count`,
`tests/integration/test_clips_page_perf.py`.
*ADRs:* 0046.

**14. No sync filesystem I/O inside `async def`.** Wrap blocking fs calls
in `asyncio.to_thread(...)`. The only escape hatch is an inline
`# sync-io-ok` pragma on a justified pre-existing case.
*Enforced by:* `tests/unit/test_no_sync_fs_in_async.py`.
*ADRs:* ARCHITECTURE.md / Tier-3 (CLAUDE.md).

**15. Complexity can hold or fall, never climb.** A pre-commit erosion
gate ratchets the structural-erosion ratio (share of complexity mass in
CC>10 functions) against `.erosion-baseline.json` + a hard CC cap.
Raising the baseline is a conscious, reviewed act; a refactor that pushes
past the cap blocks until then.
*Enforced by:* `tools/erosion_gate.py` (pre-commit) +
`pylint --enable=duplicate-code`.
*ADRs:* 0060.

## Frontend

**16. Reuse the shared UI library; one vocabulary per primitive.** Buttons
(`.btn` / `ui.button`), modals (`ui.modal` + `.modal-*`), menus
(`ui.menu`/`ui.menu_item` + `popover()`), form fields (`ui.field` /
`ui.textarea_field`), design tokens (not raw hex), and the `fmtTimecode` /
`fmtBytes` formatters. Don't parallel-evolve a second renderer for
scenes/markers/fields (that's `_anno_panels.html`), the video transport
(`Alpine.data("player")` + shared overlay), clip cards, or the clip
picker. New primitive vocabulary (`*-menu`, `modal-*`) is forbidden.
*Enforced by:* `tests/unit/test_design_language_guard.py`; see
`docs/design-language.md`.
*ADRs:* 0025, 0056, 0062, 0063.

**17. Cross-component Alpine state uses `Alpine.store`, never
`_x_dataStack`.** Shared studio state lives in `static/studioStore.js`.
There is exactly **one** HTMX↔Alpine lifecycle helper
(`static/htmxAlpine.js`); call `window.htmxAlpine.reinit(el)` for
fetch-injected subtrees. Manual `innerHTML`/`insertAdjacentHTML` into a
live Alpine tree uses `wireHtmx`, not `reinit` (avoids double-bound
directives).
*Enforced by:* `tests/unit/test_no_x_data_stack.py`,
`tests/unit/test_htmx_alpine_single_lifecycle.py`.
*ADRs:* 0048, 0083, 0089.

**18. User-visible errors use the toast store; never `location.reload()`
after CRUD.** Errors go through `Alpine.store('toast').push(msg, {level})`
— never `alert()`, silent `.catch()`, or `console.error` for actionable
failures. CRUD endpoints return HTMX partials on `HX-Request: true`; JS
swaps them in place and pushes a toast.
*Enforced by:* convention.
*ADRs:* (CLAUDE.md frontend error-handling discipline).

## Data model

**19. Enumerations are centralised, never hardcoded.** Fixed enums (every
value has matching code) are a `Literal` in `models/` plus a registry
entry (`editable=False`), served from code. Editable lists (open data,
e.g. model catalogs) are registry `editable=True` + the `enum_values`
table, edited in the Admin console. Never hardcode either kind in a
template, `<select>`, or JS array. Backend reads via
`ctx.enum_service.*`; frontend via `window.APP_ENUMS` or route context.
*Enforced by:* a guard test pinning registry values to
`get_args(<Literal>)`; `enums/registry.py`.
*ADRs:* 0080.

**20. Versioned prompts are immutable once promoted.** A Prompt has many
Versions; at most one is `production`; `production`/`archived` versions are
immutable (partial unique index + `PromptsRepo`). Annotation output is
materialised as `review_items` the user accepts/rejects, not raw
`output_json`. Published clip state is a snapshot layer
(`clip_versions`) on the existing write queue; switching versions
re-activates a snapshot and reconciles only the markers *we* authored
(preserving human/pre-existing ones).
*Enforced by:* `repositories/prompts.py::VersionImmutableError` + partial
unique index.
*ADRs:* 0010, 0038, 0099, 0100, 0101.

## Cloud, deploy & access

**21. Cloud Run runs one instance with Litestream-persisted SQLite.**
Pinned to a single instance (`maxScale=1`); SQLite is replicated to GCS via
Litestream (`--no-cpu-throttling` so it flushes under scale-to-zero).
Scale-to-zero releases the CatDV seat and VPN gracefully on the way down.
A single write connection on the request critical path — guard against
Litestream checkpoint lock contention.
*Enforced by:* convention; `docs/DEPLOY.md`.
*ADRs:* 0066, 0077.

**22. Deploys promote one image by tag.** Push to `main` → builds and
deploys *staging* (SHA-tagged single build). A `v*` tag promotes that
exact SHA image to prod (no rebuild; guarded that the image exists).
Staging and prod tunnels are mutually exclusive (shared WireGuard peer
key); staging persists to its own Litestream replica path.
*Enforced by:* the deploy workflow gated on `github.ref`.
*ADRs:* 0103, 0104, 0105.

**23. Access: IAP authenticates, the app authorises.** The IAP edge is
opened to `allAuthenticatedUsers` (never `allUsers`) — it only proves
*who* you are. The app's default-deny gate is the **sole** authorization
authority (2 roles: admin/member, managed in the admin console). The app
never edits the Google edge and holds no credential for it.
*Enforced by:* convention; the default-deny gate (`get_gate_state`).
*ADRs:* 0084, 0085, 0113.

**24. The cloud media cache is AI-store-only.** On GCP there is no local
proxy layer; the cache UI hides the local-media layer and acts on the
AI store. Thumbnails get a durable GCS-backed cache (the same treatment as
proxies) rather than a lazy ephemeral fetch.
*Enforced by:* convention.
*ADRs:* 0069, 0071, 0073.

## AI integration

**25. Gemini Live is browser-direct with server-minted ephemeral tokens.**
Audio bytes go straight from the browser to Google over WSS (no backend
bridge). The browser never sees the raw `GEMINI_API_KEY`; the server mints
a short-lived, single-use, config-bound ephemeral token
(`v1alpha auth_tokens`) and the browser presents it via `?access_token=`.
System prompt + tool declarations are bound into the token, withheld from
the browser. Wait for the server's `setupComplete` before sending content
(else the socket races closed); unlock the output `AudioContext`
synchronously inside the user gesture and reuse one long-lived context.
*Enforced by:* convention; `docs/gemini-live-lessons.md`.
*ADRs:* 0016, 0018, 0109, 0110, 0112.

**26. Run telemetry is local-first.** Usage/cost is captured locally first
with a deferred cloud pipeline; cost surfaces use total-spend semantics
and a shared `usd` filter. Model names are verified available in the
target region (e.g. `europe-west3`) before being added to a dropdown.
*Enforced by:* convention.
*ADRs:* 0058, 0059, 0080.

## Testing

**27. Walkthrough tests run the real app in-process, fully offline.**
`tests/walkthrough/` drives the FastAPI app via Playwright on port 8766 in
its own in-process instance with an injected `FakeArchive` — never the
`:8765` dev server, never the CatDV seat. UI changes (template, Alpine
component, page route, user flow) update the affected scenario in the same
PR. The unit/integration split: run the touched slice in-loop, the full
suite (`-n auto`) once before commit.
*Enforced by:* the `/e2e` skill; `tests/walkthrough/README.md`.
*ADRs:* 0111.
