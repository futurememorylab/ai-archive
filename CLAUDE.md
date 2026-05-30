# CLAUDE.md — catdv-annotator

Project-scoped guidance for Claude Code sessions working inside this repo. Network/auth context (VPN, credentials, contacts) lives in the parent `sikl/CLAUDE.md`; this file only covers what's relevant when editing or running the code here.

## CatDV session discipline (license seats)

CatDV Enterprise has a **2-seat session limit**, and in practice one seat is almost always taken by the human web client — so **assume 1 seat is available to this app**. A leaked `JSESSIONID` locks the server out until it times out server-side, which can be many minutes.

### Before starting a dev server

Always check for an existing instance first. Don't launch a second one:

```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
/bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
/usr/sbin/lsof -nP -iTCP@192.168.1.41:8080
```

If anything is listening on `8765` or connected to `192.168.1.41:8080`, **reuse it or shut it down first** — don't spawn another.

### Always shut down gracefully

Use `SIGTERM`, **never `SIGKILL`**. Only TERM lets FastAPI's `lifespan` run `ctx.aclose()`, which calls `DELETE /catdv/api/9/session` and frees the seat.

```bash
/bin/kill -TERM <pid>          # ✅ graceful — runs aclose()
/bin/kill -9 <pid>             # ❌ leaks the JSESSIONID — seat held until CatDV times it out
```

After kill, confirm in the server log:

```
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.   ← this line means the seat was released
INFO:     Finished server process [...]
```

If you only see `Finished server process` without the shutdown lines above, the seat may still be held — wait it out or ask the admin to kick the stale session.

### One-shot scripts must log out too

If you `POST /session` directly from a script or `curl`, you've taken a seat. Finish with:

```bash
curl -b /tmp/jar -X DELETE http://192.168.1.41:8080/catdv/api/9/session
```

Otherwise the seat stays held for the JSESSIONID's idle-timeout window.

### When a 502 says "Maximum:2"

`GET /` returning `502 Bad Gateway` with detail `"Web Client session limit reached (Maximum:2)."` means **a seat is stuck**. Don't keep retrying — that won't free anything. Either:

1. Wait it out (server-side timeout eventually drops the stale session).
2. Ask the admin to kick the session in the CatDV admin UI.
3. If you suspect it's your own leaked session: confirm no `uvicorn` / `python backend.app` process is still alive (`ps`, `lsof`) — if one is, `kill -TERM` it properly.

## Why this matters

The CatDV REST API binds the session to `JSESSIONID` and the seat is held *server-side*, not by our process. So even when our process dies, the seat can linger. The combination of (a) checking before starting and (b) graceful shutdown after running is what keeps the single available seat usable for the next dev session.

## Frontend: explore before implementing

Before designing or writing any frontend code (Jinja partial, Alpine
component, CSS, JS), search the codebase for an existing component that
already does the same thing or something close. **Reuse it. Extract it
into a shared partial if it isn't one yet. Do not parallel-evolve a
second renderer.**

Where to look first:

- `backend/app/templates/pages/` — all partials live here. Names
  starting with `_` are includes (e.g. `_anno_panels.html`,
  `_player_overlay.html`, `_video_list.html`, `_archive_picker.html`).
- `backend/app/static/` — `player.js`, `studio.js`, `app.css`. The
  `Alpine.data("player", ...)` block in `player.js` is the canonical
  video transport.
- `grep` patterns that pay off: `grep -rln "anno-\|range\|marker\|panels\|x-data" backend/app/templates/`.

There is also a small shared UI library for the primitives — buttons,
form fields, page headers, breadcrumbs, status pills, and JS formatters.
**Read `docs/design-language.md` and reuse it before hand-rolling any of
these.** The canonical pieces: the `.btn` system + `{{ ui.button(...) }}`
macro, `{{ ui.field(...) }}` / `{{ ui.textarea_field(...) }}`,
`{{ ui.page_header(...) }}`, `{{ ui.breadcrumb(...) }}`,
`{{ ui.status_pill(...) }}` (all in
`backend/app/templates/components/_ui.html`), the `:root` design tokens in
`backend/app/static/app.css`, and the `fmtTimecode` / `fmtBytes` /
`autosize` helpers in `backend/app/static/format.js`. Use tokens not raw
hex; use `.btn` not `*-btn`; call the formatters instead of re-deriving
timecodes or byte sizes.

Red flags that mean you're about to duplicate something:

- You're rendering scenes / markers / fields / notes from JSON — that
  is `_anno_panels.html` territory. Build the `panels` dict and
  `{% include %}`.
- You're writing a `<video>` with markers, timeline, or playhead — use
  `Alpine.data("player", ...)` + the shared overlay partial.
- You're writing a thumbnail + name + duration card for a clip — look
  at `_video_list.html` and the clip card patterns first.
- You're writing a search-and-pick modal for archive clips — the
  archive picker pattern already exists.

If you genuinely need a new component, say so in the spec/ADR and
explain why the existing one couldn't be extended (size? coupling? a
flag would have made it incoherent?). Default answer is reuse.

## Cache management: use the existing layers, don't bypass them

The app has three independent caches; every code path that touches
clip media must route through them rather than calling CatDV or GCS
directly. New features that "just fetch" leak through the offline
boundary, slam the seat-limited CatDV server, or quietly re-upload
multi-GB blobs.

### The three layers

| Layer | Service | Where bytes live | DB index | Offline-safe? |
|---|---|---|---|---|
| **Local proxy cache** | `ProxyResolver` (`services/proxy_resolver.py`) | `data_dir/cache/proxies/*` | `proxy_cache` table | Yes via `LocalCacheOnlyResolver` |
| **AI input store** (GCS) | `AIInputStore` (`archive/ai_store.py`, default `gcs` impl) | `gs://catdav-proxies/...` | `ai_store_files` table | Yes — `status()` is a DB lookup, no network |
| **Thumbnail cache** | `ThumbnailService` (`services/thumbnail_service.py`) | `data_dir/cache/thumbs/*.jpg` | None (filesystem-only) | Yes via `is_online_provider` gate |

Each layer owns one question: "does this clip's media exist here, and
where?" Code that needs media calls into the right layer and lets it
decide cache-hit vs fetch-or-fail.

### Standard call patterns

- **"I need the proxy file path for clip N"** → `await ctx.proxy_resolver.path_for_clip_id(N)`.
  Raises `ProxyNotFound` if offline and not cached. Don't call CatDV
  download yourself.

- **"I need to give Gemini this clip's bytes"** → `upload = await ctx.ai_store.status(clip_key); if upload is None: upload = await ctx.ai_store.ensure_uploaded(clip_key, local_path, mime)`. The fast-path
  (status first, upload only on miss) avoids the cost when the clip is
  already in GCS and lets runs proceed even when CatDV is offline. See
  `services/annotator.py::_process_item` for the canonical pattern.

- **"I need a thumbnail for clip N"** → `await ctx.thumbnail_service.get_or_fetch(N)`.
  Returns `None` if cache miss + offline; callers render a placeholder.
  Don't call `catdv_client.download_thumbnail` directly.

### Why the layers must stay separate

The user's network state is rarely "all online" or "all offline":

- **CatDV offline, GCS online** is the common case (VPN drop, seat
  limit, server maintenance). Proxy resolver fails; AI store still
  serves and ingests. Studio Run still works for clips already in GCS.
- **GCS offline, CatDV online** happens on locked-down hosts or auth
  blips. AI store fails; proxy resolver still serves locally cached
  files. Local playback still works.
- **Both online** is the happy path — every layer succeeds.
- **Both offline** — only locally-cached + AI-uploaded clips can be
  used. Pre-flight checks should warn before any expensive action.

If the user is ever fully offline, the entire app must remain
navigable; clips that *are* cached must remain usable. That falls out
naturally if every cache layer's miss → graceful return (None / clear
error), and every caller of a layer respects that contract.

### Red flags

If you are doing any of these, stop and reuse the existing service:

- Calling `ctx.catdv.download_*` from anywhere outside the cache
  services themselves.
- Calling `ctx._gcs_service` directly instead of through
  `ctx.ai_store`.
- Writing your own `if cached_file.exists()` guard instead of asking
  the resolver / thumbnail service / ai_store.
- Eagerly fetching on a page-render path (every cache fetch should
  either be user-initiated — via `/api/cache/prefetch` — or part of a
  bounded background job).
- Re-implementing the GCS-status check or proxy-cache lookup with raw
  SQL. The repos already do this; reuse them.

### When adding a new clip-touching feature

1. Decide which layer(s) the feature needs.
2. Read the existing call pattern in `services/annotator.py::_process_item`
   (annotator is the most cache-aware service) or the route handlers
   in `routes/media.py` / `routes/cache.py`.
3. Honor the offline path explicitly — your code must work when
   `is_online_provider()` returns False and when `status()` returns
   None. If it can't, surface a clear error naming WHICH cache layer
   missed, not a generic "fetch failed".
4. If the feature needs a *new* cache (e.g. waveform thumbnails for
   audio clips), prefer adding a new layer in the same shape — a
   service with `get_or_fetch` + `status` semantics, a DB index, and
   an `is_online_provider` gate — over wedging it into an existing
   one.

## Error handling discipline

Two helpers exist; route through them.

### Narrowing provider errors

`backend/app/archive/errors.py::is_provider_not_found(exc) -> bool` is
the **only** way to decide "this clip is gone" from a caught exception.
Recognises `NotFoundError` (the explicit type adapters raise for
documented absence) and `httpx.HTTPStatusError(404)`. Anything else is
transient by definition — treat as "try later", never as evidence of
absence.

Bare `except Exception:` is allowed only in event-loop watchdog code
(e.g. `sync_engine._loop`). Anywhere a caller might infer absence,
narrow with `is_provider_not_found(exc)`. Anywhere a caller has to mark
a record terminal (failed, error, orphan), get explicit evidence — do
not assume.

The `sync_engine._tick` catchall defaults to `mark_retryable` and
honours `settings.sync_max_attempts` before flipping to `mark_failed`.
The terminal transition uses `mark_failed(bump_attempts=True)` to do
status + attempts in one atomic SQL — never two separate commits.
Adding a new external-system caller? Mirror the same shape.

Catch `Exception`, not `BaseException` — the latter swallows
`asyncio.CancelledError` and breaks task cancellation.

See ADR 0042 for the full rationale.

### User-facing error strings

`backend/app/services/errors.py::humanise(exc) -> str` produces an
actionable, non-empty string for any exception. Used by `annotator`
job error messages today; **all new user-facing surfaces should use it
instead of `str(exc) or exc.__class__.__name__`** — the latter
silently returns `'HTTPStatusError'` for the most common SDK failures.

## Shell Environment

- This machine uses nvm; non-interactive shells don't have node/npm/npx on PATH. Source ~/.nvm/nvm.sh first, or use absolute paths.
- Python 3.14 venvs are known-broken on this machine — use 3.12 or 3.13.

## Specs must include a manual acceptance flow

Every design spec under `docs/specs/` must end with (or contain near
the bottom) a **Manual acceptance flows** section: a numbered list of
end-user click-throughs that, taken together, prove the spec was
actually implemented. One numbered flow per capability the spec
introduces. Each flow names the setup (URL, prerequisite data), the
actions, and the observable expected result.

This serves three purposes:
1. The reviewer/implementer at the end of the work has a concrete
   acceptance checklist — not just "all tests pass".
2. The spec's scope becomes tangible — if you can't write the
   click-through, the spec is too abstract.
3. Regressions on adjacent surfaces (the spec touches X to ship Y;
   the flow includes "X still works") get a named guard.

See `docs/specs/2026-05-26-prompt-studio-pr2-design.md` for the
expected shape. The bar is: a colleague who didn't write the code can
follow the flows on a running app and either tick them off or report
exactly which step broke.

## Recording decisions at end of session

When a session involves any non-trivial design call — a schema replacement,
an API shape choice, a deliberate deviation from the spec, a "we considered
X and Y, picked Z" moment — append a new ADR file to
`docs/adr/NNNN-slug.md` (one number higher than the last) before the
session ends. Use the MADR-lite format: a `# NNNN. <Title>` heading,
`**Date:**` / `**Status:**` metadata, then `## Context` / `## Alternatives` /
`## Decision` / `## Consequences` sections. See any existing ADR (e.g.
`docs/adr/0001-python-only-stack-no-node-frontend.md`) for the template.
Update the index table in `docs/decisions.md` with the new entry. Group
several related calls under one ADR when they share context (see the
PR 3 / PR 5 / PR 6 / PR 7 ADRs for the pattern).

The bar is "would a future contributor reading the diff ask *why*?" If
yes, document it. If the call was forced by an obvious constraint and the
diff itself makes the reasoning self-evident, skip it. Pure mechanical
work (renames, dependency bumps, test additions) does not need an entry.
