# Prompt Studio — Sets, Source Tabs & Navigator Polish (Spec A)

**Date:** 2026-06-08
**Status:** Approved — ready for implementation planning
**Scope:** Frontend + thin data-model change. Archive-only. **No** upload subsystem.

## Context

Prompt Studio organises CatDV archive clips into flat "folders"
(`studio_folder` / `studio_folder_clip`, keyed by integer `clip_id`).
Three problems motivate this work:

1. **"Folder" is the wrong word.** The structure is one level deep — these
   are *sets*, not folders. The vocabulary should say so.
2. **No source distinction.** Every clip is a CatDV archive clip. A future
   feature (Spec B) adds *uploaded* videos; the navigator needs to
   distinguish the two sources, and in a cloud deployment with no archive
   connected the Archive affordance should disappear entirely.
3. **The navigator looks unfinished.** A target mockup (provided as a
   screenshot during the brainstorming session) shows a richer clip
   navigator: source tabs, a catalog
   sub-header, selection checkboxes, and clip cards with a year + SMPTE
   timecode overlay on the thumbnail.

This spec (**A**) ships the rename, the source tabs, the archive-absent
handling, the visual restyle, and a **bulk run** action. The actual upload
subsystem — accepting video files, a dedicated upload cache category, AI-store
upload for Gemini, uploaded-clip thumbnail generation, making uploaded clips
runnable — is **Spec B**, brainstormed and built separately. In Spec A the
**Uploaded tab is a stub** ("Uploads coming soon").

### Key prior-art / reuse (do not re-implement)

| Concern | Existing thing to reuse | Location |
|---|---|---|
| Run a prompt on one clip | `POST /api/studio/runs` + poll `GET /api/studio/runs/{id}` | `routes/studio.py:127`, `studioStore.js::runOnFocusedClip` |
| Archive clip picker | `clipPickerCore()` + `_clip_picker_main.html` + `_clip_picker_basket.html` | `static/studio.js`, `templates/pages/` |
| Toasts | `Alpine.store('toast').push(msg,{level})` | `static/toast.js` |
| HTMX↔Alpine re-init | `window.htmxAlpine.reinit(el)` | `static/htmxAlpine.js` |
| Timecode / byte formatting | `fmtTimecode`, `smpte` global | `static/format.js`, shared Jinja env |
| Buttons / fields / pills | `.btn` system, `_ui.html` macros, `app.css` tokens | `templates/components/_ui.html`, `static/app.css` |
| Archive connectivity | `live_ctx` presence + `live_ctx.archive` | `context.py` (CoreCtx/LiveCtx) |

## Alternatives considered

- **Tab model — source-partitioned vs filter-within-mixed-sets.** Chosen:
  **source-partitioned**. Each tab is its own collection; a set belongs to
  exactly one source. Rejected the mixed-set filter model because it forces
  per-clip source tracking inside a set and makes the catalog sub-header
  ambiguous.
- **Rename depth — UI-only vs code+routes vs full incl. DB.** Chosen: **full
  rename including the DB tables**. Studio is young; the migration is
  low-risk, and anything short of a full rename leaves "folder means set"
  drift in the schema.
- **Loose clips at tab root.** Rejected. Every clip stays in a set (today's
  model), so no nullable-membership / virtual-bucket complexity.
- **Catalog scoping.** The `catalog 881507` sub-header is **cosmetic context
  only**; sets stay global. Rejected per-catalog set scoping as out of scope.
- **Bulk run — client loop vs server batch endpoint.** Chosen: **client loop
  reusing the existing per-clip endpoint** with bounded concurrency. Maximum
  reuse, zero run-engine change. Rejected the new `/runs/batch` endpoint to
  avoid new server code.
- **Richer card fields.** Of the mockup's extra elements, Spec A builds
  **selection checkboxes** and **year + timecode on the thumbnail**. The
  illustrative `edge: city` label and notes/description snippet are **not**
  built (no backing field / out of scope); run counts stay as the existing
  coloured **run-dots**, not a number.

## Decision

### 1. Data-model rename + source column

**Migration `0015_studio_sets.sql`:**

```sql
ALTER TABLE studio_folder      RENAME TO studio_set;
ALTER TABLE studio_folder_clip RENAME TO studio_set_clip;
ALTER TABLE studio_set ADD COLUMN source TEXT NOT NULL DEFAULT 'archive'
    CHECK (source IN ('archive','uploaded'));
-- replace global UNIQUE(name) with per-source uniqueness
DROP INDEX IF EXISTS <existing name unique index>;        -- confirm name at impl time
CREATE UNIQUE INDEX studio_set_source_name ON studio_set(source, name);
```

> SQLite caveat: if `UNIQUE(name)` is a *column constraint* (not a named
> index) it can't be dropped with `DROP INDEX`. If so, the migration rebuilds
> the table (create `studio_set_new` with the new schema, `INSERT … SELECT`,
> drop old, rename). The implementation plan must check the actual 0013
> schema and branch accordingly. Existing rows must be preserved and default
> to `source='archive'`.

- `studio_set_clip` keeps `(set_id, clip_id)` PK and `ON DELETE CASCADE`.
- Rename `StudioFoldersRepo` → `StudioSetsRepo`; all `*_folder*` methods →
  `*_set*`. Add a `source` parameter:
  - `list_sets_with_counts(conn, source)` — sets of that source + per-set
    `clip_count`.
  - `clip_total_for_source(conn, source)` — sum of clips across that source's
    sets, for the tab badge.
  - `create_set(conn, name, source)`.
- Routes: `/api/studio/folders*` → `/api/studio/sets*`. Page routes
  `/studio/_folders`,`/studio/_folder` → `/studio/_sets`,`/studio/_set`.
  `GET /api/studio/sets` and `GET /studio/_sets` accept `?source=archive`
  (default `archive`).

### 2. Source tabs (Archive / Uploaded)

- New tab-bar partial at the top of the navigator: **Archive (count)** |
  **Uploaded (count)**, icons per the mockup, active tab accent-underlined
  (design token, not raw hex).
- `studioStore.navSource: 'archive' | 'uploaded'`, persisted to
  `localStorage` alongside the existing layout prefs. Default = `'archive'`
  when archive is available, else `'uploaded'`.
- Switching tabs swaps the whole set list (HTMX `GET /studio/_sets?source=…`,
  re-init via `window.htmxAlpine.reinit`).
- **Uploaded tab (Spec A stub):** renders a coming-soon empty state (cloud
  icon + "Uploads coming soon"). Its badge count is `0`. No "+ new set" and
  no upload action under this tab in Spec A.

### 3. Archive-absent handling

- Page/partial context gains `archive_available = (live_ctx is not None and
  live_ctx.archive is not None)`, computed in `routes/pages/studio.py`.
- When `archive_available` is false:
  - **Hide the Archive tab.**
  - Default `navSource` to `'uploaded'`.
  - Show the coming-soon stub. The studio shell (prompt editor, player,
    versions) stays fully navigable.
- When true: both tabs render; Archive is the default.

### 4. Navigator visual restyle

- **Sub-header** (Archive tab): `catalog {id} · N sets` + `[+]` new-set
  button. `{id}` is cosmetic context from the connected archive; `N` is the
  set count. The `[+]` opens the existing inline new-set input.
- **Set card** (`_studio_set_card.html`): chevron (single-expand preserved),
  selection checkbox, set icon, name, count badge. Badge shows
  `selected/total` when that set has a current selection, else `total`.
- **Clip card** (`_studio_set_clip_card.html`, renamed from
  `_studio_clip_card.html`):
  - larger thumbnail with **year + SMPTE timecode** overlay (via
    `fmtTimecode` / `smpte`; falls back to existing `m:ss` duration when no
    timecode);
  - **selection checkbox**;
  - **focus radio-dot** indicator for the focused clip (replaces relying on
    the `.selected` card outline alone — both may coexist);
  - name + `id:N · year` tag (unchanged);
  - existing **run-dots** (`has_cur` / `has_other`) — unchanged;
  - hover **remove-X** (unchanged behaviour, calls `window.studio.removeClip`
    now taking `setId`).
- All styling via `app.css` tokens, `.btn` system, `_ui.html` macros. No new
  primitives, no raw hex.

### 5. Selection + bulk run

- `studioStore.selectedClipIds`: a `Set` of clip ids, scoped to the current
  tab; cleared on tab switch. Clip checkbox toggles membership; set-level
  checkbox selects/deselects all clips currently rendered in that set.
- A **bulk-action bar** appears when `selectedClipIds` is non-empty:
  **"Run on N clips"** + **Clear**.
- **Bulk run = client loop, reusing the existing endpoint:**
  - `studioStore.runOnSelectedClips()` iterates `selectedClipIds`, POSTing
    `/api/studio/runs {prompt_version_id: activeVersionId, clip_id, model}`
    per clip with **bounded concurrency (2 in flight)** to protect the CatDV
    seat and Gemini quota, polling each `run_id` to terminal status.
  - Progress surfaced as "Running 3/8…" on the action bar; the existing
    `running` state machine guards re-entry.
  - Reuses `runOnFocusedClip`'s POST/poll shape — extract the shared
    single-run primitive (`_runOne(clipId)`) and have both call it.

### 6. Error handling & offline

- Per-clip bulk-run failure → `Alpine.store('toast').push(…, {level:'error'})`
  and the loop continues with the remaining clips. Run-dots refresh via the
  existing HTMX partial swap — **never `location.reload()`**.
- Sets are DB-backed → the navigator (tabs, sets, clip cards) works fully
  offline. The archive *picker* remains gated on archive-online (unchanged);
  when offline the "+ Add from archive" path returns its existing clear error.

### 7. Tests (TDD)

- **Migration:** rows from `studio_folder`/`studio_folder_clip` survive into
  `studio_set`/`studio_set_clip` with `source='archive'`; `UNIQUE(source,name)`
  enforced; same name allowed across sources.
- **Repo:** `list_sets_with_counts(source)` returns only that source's sets
  with correct counts; `clip_total_for_source` sums correctly.
- **Route:** `GET /api/studio/sets?source=` partitions; `archive_available`
  false hides the Archive tab and defaults to Uploaded.
- **Perf:** extend the existing N+1 / `assert_query_count` guard to the
  renamed sets-list render (statement count flat for 10 vs 100 vs 1000 sets).
- **Bulk run (store unit):** `runOnSelectedClips` fires one POST per selected
  clip, respects the concurrency cap, and aggregates progress; a failing clip
  toasts and does not abort the rest.
- **Regression:** single-clip `runOnFocusedClip` and the archive picker
  add-clips flow still pass.

## Consequences

- **Schema churn is one-time and additive-by-rename.** Any external reference
  to `studio_folder*` or `/api/studio/folders*` breaks — there are no public
  API consumers, but the grep-and-rename must be exhaustive (templates, JS
  component names, the `window.studio` shim, page routes, tests).
- **Spec B inherits a clean seam.** The `source` column and the
  `?source=` partition mean Spec B only adds the `'uploaded'` write path and
  the upload cache layer — no navigator rework.
- **Bulk run adds load.** Bounded concurrency (2) keeps the CatDV seat and
  Gemini quota safe; without the cap, a large selection could stampede.
- **The Uploaded tab ships visibly empty.** Users see "coming soon" before
  the feature exists — acceptable as a signpost, and it makes the cloud /
  no-archive deployment coherent (Uploaded becomes the default surface).

## Manual acceptance flows

Setup: a running backend (`/studio`) with the archive connected and at least
one prompt version plus a couple of sets containing archive clips.

1. **Rename is complete.** Navigate to `/studio`. Confirm the UI says "set"
   everywhere (sub-header "N sets", "+ new set", empty states). Create a set,
   rename it, add clips from the archive picker, remove a clip, delete the
   set — every action succeeds and the list updates in place (no full reload).

2. **Source tabs.** Confirm an **Archive** tab and an **Uploaded** tab at the
   top of the navigator, with the Archive tab active by default and showing a
   clip count. Click **Uploaded** → the set list is replaced by an "Uploads
   coming soon" empty state. Click **Archive** → the sets return. Reload the
   page → the last-selected tab is remembered.

3. **No archive connected.** Stop/disconnect the archive (or run a build with
   no `live_ctx.archive`). Reload `/studio`. Confirm the **Archive tab is
   hidden**, the **Uploaded** tab is selected by default showing "coming
   soon", and the rest of the studio (prompt editor, player, version list)
   is still navigable.

4. **Restyled clip cards.** In an Archive set, expand it and confirm each clip
   card shows a thumbnail with the **year and SMPTE timecode** overlaid, a
   **selection checkbox**, the clip name, the `id:N · year` tag, and the
   existing run-dots. Click a card → it becomes the focused clip (focus dot +
   highlight) and the player loads it.

5. **Bulk run.** Tick the checkboxes on three clips (or a set checkbox to
   select all of its clips). Confirm a bulk-action bar shows **"Run on 3
   clips"**. Click it → the bar shows progress (1/3 → 3/3); when it finishes,
   all three clips show the active-version run-dot and their outputs are
   saved (open each clip's output to confirm). Trigger one clip to fail (e.g.
   force an error) and confirm a toast appears and the other two still
   complete.

6. **Regressions hold.** With no multi-selection, focus a single clip and run
   it via the normal single-clip control — it still works. The "+ Add from
   archive" picker still searches and adds clips. With the archive offline,
   the picker shows its existing clear error rather than crashing.
