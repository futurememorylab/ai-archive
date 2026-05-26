# Prompt Studio

**Date:** 2026-05-26
**Status:** Approved (design)

## Problem

We can author prompts on `/prompts` (name, body, target_map, output_schema,
model, draft/production/archived versions) and we can run prompts in
production through the annotation jobs pipeline. What's missing is the
iteration loop in between: pick a few representative clips, run a draft
prompt against them, see structured output, compare against an older
version, tweak the body, re-run. Today that loop only exists by running
a full production job and reading markers back from CatDV — too slow,
too entangled with the catalog, and impossible for backtesting old
versions side by side.

A previous attempt (PR #9, commit `8a9b2bb`) shipped a "testbench + run
UI + worker pipeline" in a single change and was reverted (`1065546`)
for being too large to land safely. The spec, plan, and ADR from that
attempt were removed from main in `14dc801`. This design supersedes
that attempt with a smaller, vertically-sliced scope.

## Goals

- A dedicated `/studio` screen where the user picks a prompt, picks a
  clip from a curated set of folders, and runs any version against
  that clip to see structured output.
- Output for each (version, clip) is persisted so we can compare two
  versions on the same clip side by side, with line-level diffing of
  both the prompt body and the structured output.
- Studio reuses the existing player, cache, archive picker, jobs
  pipeline, and prompt model — minimal new infrastructure.
- Runs never write to CatDV. Studio is purely for iteration; promotion
  to production stays on `/prompts`.

## Non-goals (v1)

- Uploading videos into the studio (only archive-picked clips in v1).
- Multi-clip selection / batch runs / batch UI / queue UI.
- Stacked + unified compare layouts (side-by-side only in v1).
- Nested folders / drag-to-move clips between folders.
- Per-user "compare layout" preference, tweaks panel, theme switcher.
- Editing target_map, output_schema, version metadata, or promotion
  from Studio. All of those stay on `/prompts`.
- Run history viewer. We persist every run forever but the UI only
  shows the *latest* run per (version, clip) in v1.

## Design source

The visual design is a React prototype bundle from claude.ai/design,
extracted locally to `/tmp/catdv_design/`. The primary screen file is
`project/studio.jsx`; data shapes are in `project/studio-data.js`;
styling tokens are in `project/styles.css`. Per the bundle README, we
recreate the visual output in our stack (Jinja + Alpine + HTMX); we do
not copy the prototype's internal structure unless it happens to fit.

We **drop the "testbench" framing** from the prototype. Studio has
plain folders of clips, globally scoped (not per-prompt). Switching
the active prompt does not swap the folder tree.

## Routes & navigation

- New side-rail entry **Studio** (flask icon, `IconFlask`), positioned
  after Prompts. Active state matches `/studio` and any subpath.
- New page `GET /studio` → `studio.html`. Active prompt held in URL as
  `?prompt_id=N` (so deep-linking and reload work).
- HTMX partials:
  - `GET /studio/_folders` — folder tree
  - `GET /studio/_clips?folder_id=` — clips in folder (with per-clip
    "ran with this/other version" indicator)
  - `GET /studio/_run?prompt_version_id=&clip_id=` — right-pane output
    card for that pair
  - `GET /studio/_archive_picker?q=` — modal body for adding clips
    from the archive into a folder
- The existing `/prompts/{id}` detail menu (`_prompt_menu.html`) gets
  a new item **"Open in Studio"** that links to
  `/studio?prompt_id={id}`.

We **do not** add a separate Templates nav entry. The mock's
"Templates" screen maps to our existing `/prompts` page.

## Page layout

```
┌─────────────────────────────────────────────────────────────────┐
│ studio-hdr   [🧪] [Prompt ▾] [model ▾]         [▶ Run · v5]     │
├──────────────┬──────────────────────────────────────────────────┤
│ video-panel  │ studio-right                                     │
│              │ ┌──────────────────────────────────────────────┐ │
│ Folders      │ │ player (only when a clip is focused)         │ │
│  └ Clips     │ └──────────────────────────────────────────────┘ │
│              │ ┌──────────────────────────────────────────────┐ │
│              │ │ compare row: 1 or 2 prompt-cards             │ │
│              │ │  · cur  (active version, editable if draft)  │ │
│              │ │  · cmp  (optional, read-only)                │ │
│              │ └──────────────────────────────────────────────┘ │
└──────────────┴──────────────────────────────────────────────────┘
```

When no clip is focused the player region collapses and the compare
row fills the right pane (mock's `.no-player` modifier).

## Data model

One new migration adds three tables and one column.

```sql
CREATE TABLE studio_folder (
  id         INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL UNIQUE,
  created_at TEXT    NOT NULL
);

CREATE TABLE studio_folder_clip (
  folder_id INTEGER NOT NULL REFERENCES studio_folder(id) ON DELETE CASCADE,
  clip_id   INTEGER NOT NULL,
  added_at  TEXT    NOT NULL,
  PRIMARY KEY (folder_id, clip_id)
);

CREATE TABLE studio_run (
  id                INTEGER PRIMARY KEY,
  prompt_version_id INTEGER NOT NULL REFERENCES prompt_version(id),
  clip_id           INTEGER NOT NULL,
  job_id            INTEGER REFERENCES job(id),
  status            TEXT    NOT NULL,        -- pending|running|ok|error
  output_json       TEXT,
  duration_s        REAL,
  tokens_in         INTEGER,
  tokens_out        INTEGER,
  cost_usd          REAL,
  model             TEXT,                    -- model actually used
  error             TEXT,
  started_at        TEXT,
  finished_at       TEXT
);
CREATE INDEX studio_run_lookup
  ON studio_run(prompt_version_id, clip_id, finished_at DESC);

-- on existing job table:
ALTER TABLE job ADD COLUMN kind TEXT;  -- null = annotation, 'studio' = studio run
```

`studio_folder.name` is globally unique (no nested folders, no
namespacing in v1). `studio_run` stores one row per execution;
history is kept forever. UI queries:

```sql
-- latest run per (version, clip)
SELECT * FROM studio_run
WHERE prompt_version_id = ? AND clip_id = ?
ORDER BY finished_at DESC LIMIT 1;

-- per-clip "has any run with this version" / "has any run with another"
-- for the version dots on clip cards
SELECT prompt_version_id FROM studio_run
WHERE clip_id = ? AND status = 'ok'
GROUP BY prompt_version_id;
```

`studio_run.model` records the model that actually ran (which may
differ from `prompt_version.model` if the Studio header's model
picker was used to override per-run).

## REST API

All under `/api/studio`:

```
GET    /api/studio/folders
POST   /api/studio/folders                          {name}
PATCH  /api/studio/folders/{fid}                    {name}
DELETE /api/studio/folders/{fid}

GET    /api/studio/folders/{fid}/clips
POST   /api/studio/folders/{fid}/clips              {clip_ids: [...]}
DELETE /api/studio/folders/{fid}/clips/{cid}

POST   /api/studio/runs                             {prompt_version_id, clip_id, model?}
                                                    → {run_id, job_id}
GET    /api/studio/runs/{run_id}
GET    /api/studio/runs?prompt_version_id=&clip_id=&latest=1
```

No batch endpoints, no folder reordering, no clip-move-between-folders
in v1 (remove from one, add to another).

## Job pipeline integration

A studio run creates a `job` row with `kind='studio'` and a linked
`studio_run` row carrying the prompt version, clip, and (optional)
model override. `services/annotator.py` branches on `kind`:

- `kind IS NULL` (existing path) — annotate → write markers/fields/notes
  back to CatDV.
- `kind = 'studio'` — annotate → write structured output and stats to
  `studio_run`, **skip** the CatDV-write step entirely.

Cancel and retry semantics already live on the job layer, so studio
runs get cancellation, error capture, and SSE progress for free. The
Studio frontend subscribes to the existing per-job SSE stream and
swaps the right-pane output card on completion.

## Video panel (left rail, ~320px)

Replaces the mock's `TestBench`. Top to bottom:

- Header row: `Folders                                 [+ New folder]`
- Folder list (flat, no nesting). Each folder row shows name and clip
  count. Single-expand-at-a-time: clicking a different folder collapses
  the previous one.
- When a folder is expanded, its clip cards render inline beneath the
  header. At the bottom of the expanded list: `[+ Add from archive]`
  opens the archive picker modal.
- Per-folder kebab/context menu: **Rename**, **Delete**.

**Clip card**: matches the mock's `TBClipCard` minus the batch
checkbox. Shows thumb (existing CatDV poster pipeline from
`_video_list.html`), name, monospaced tag line (`tag · note`),
SMPTE duration badge on the thumb, footer `N runs · id:catalog_id`,
and **two stacked run-dots** in the top-right corner:

- accent dot if the **currently active prompt version** has been run
  on this clip
- info-blue dot if **any other version** has been run on this clip
- no dot if no runs

Hover on the card reveals a small `X` (remove from folder). Click
anywhere else on the card focuses the clip — loads the player and
output panel on the right via HTMX.

**Archive picker modal**: search box + results list reusing the
existing clips-list cell partials. Multi-select inside the picker is
allowed (one-shot add), but the focused-clip-at-a-time runtime
constraint is unchanged.

## Studio header

```
┌──────────────────────────────────────────────────────────────────┐
│ [🧪]  Prompt name ▾   [gemini-2.5-pro ▾]   [▶ Run · v5]          │
└──────────────────────────────────────────────────────────────────┘
```

**Prompt picker** (left): dropdown of all active (non-archived)
prompts. Each row: name, description, current version + status (e.g.
`v5 · draft`, `v4 · production`). Picking a row updates URL to
`?prompt_id=N` and reloads the studio for that prompt.

**Model picker** (middle): defaults to the active version's stored
`model`. Lets the user override per-run for comparing models without
mutating `prompt_version.model`. Each `studio_run` records the model
that actually ran.

**Run button** (right): label reflects the active card's version,
e.g. `Run on this clip · v5`. States:

- idle, clip focused → enabled
- idle, no clip focused → disabled, tooltip "Click a clip in a folder
  to focus it"
- running → `⟳ Running… (00:08)` with elapsed time; click cancels the
  underlying job
- just finished → flashes "✓ Done" then returns to idle label

There is no per-clip batch "Run on N clips" in v1. One focused clip,
one click, one run.

## Right pane: player

When a clip is focused, the player mounts above the compare row,
reusing the existing player from `_anno_panels.html` + `player.js`
that the clip detail and review pane already use. Same cache/proxy
resolution, same transport, same scrubbing.

The only **new** layer is marker overlay rendering on the existing
timeline: read `scenes[]` from the latest `studio_run.output_json`
for cur (and cmp when set) and draw two stacked range rows on the
timeline. Legend below the transport: `● v5 run · N scenes` and (if
compare is on) `◆ v4 run · M scenes`.

No frame extraction, no transport changes, no new player behavior —
just the overlay.

## Right pane: compare row

One or two **prompt-cards** side by side.

- **cur card** — always present. Bound to whichever version is active
  in its own version picker. Editable textarea if `version.status =
  'draft'`; read-only `<pre>` otherwise. Tabs: **Prompt | Output**.
  Footer shows run stats (`N scenes · 7.4s · 612 tok · $0.02`) when
  output exists.
- **cmp card** — hidden by default (single-version mode). A
  **+ Compare** button on the cur card materializes the cmp card with
  the next-most-recent version that isn't cur. Each card has its own
  version picker; either can be flipped to any version. The cmp card
  has a **Diff vs {cur.label}** toggle (PR2) that swaps its body into
  a line-diff of the current mode (Prompt or Output).

**Editing semantics**: only draft versions are editable; all versions
are runnable. Editing the draft body auto-saves on debounce to
`prompt_version.body`; clicking Run also flushes. No separate "Save"
button in Studio. (The `/prompts` detail page keeps its existing
save-on-dirty button for body/target_map/output_schema/model edits
there.)

**Output panel** (the Output tab): renders the latest run's
`output_json` against the prompt version's `target_map`:

- `scenes` section, formatted with SMPTE timecodes and `evidence_secs`
  pills, labeled `→ CatDV markers`.
- Each non-scenes field rendered as a row labeled by its target_map
  entry (e.g. `summary_cz → pragafilm.popis.materialu`,
  `decade → pragafilm.dekáda.natočení`).

Empty state when no run exists for (version, clip): "No run yet. Hit
Run to execute this version on the selected clip."

## Run flow

1. User has prompt selected (`?prompt_id=N`), folder open, one clip
   focused, optionally a model picked.
2. User clicks Run. Frontend posts
   `POST /api/studio/runs {prompt_version_id, clip_id, model?}`.
3. Backend creates `studio_run` (status=pending) + `job` (kind=studio,
   linked), enqueues for worker.
4. Worker picks up job, calls `services/gemini.py` with the version's
   body + target_map + output_schema + chosen model + the clip's
   proxy media; parses structured output; writes
   `studio_run.output_json` + stats + `status='ok'`. **Skips** the
   CatDV-write step.
5. Frontend was subscribed to the job's progress channel (the
   existing events SSE stream under `/events`; polling fallback is
   acceptable if a job-scoped SSE doesn't exist yet). On completion
   it HTMX-swaps `/studio/_run?prompt_version_id=…&clip_id=…` into
   the right-pane output card. Run-dots on the focused clip card
   update.

Errors set `studio_run.status='error'`, `studio_run.error=<msg>`; the
output card renders an error state with the message.

## Slicing into PRs

To prevent the "single giant PR" failure mode of the previous attempt,
the work lands in three vertically-sliced PRs, each independently
shippable.

**PR1 — Studio shell + run loop**
- Migration: `studio_folder`, `studio_folder_clip`, `studio_run`,
  `job.kind` column.
- `/api/studio/folders`, `/api/studio/folders/.../clips`,
  `/api/studio/runs` endpoints.
- `services/annotator.py` branch on `kind='studio'`.
- `studio.html` page + nav entry + side rail icon.
- Video panel: folder tree, archive picker modal, clip cards (with
  run-dots — the data is available the moment runs are persisted),
  focus behavior.
- Studio header: prompt picker, model picker, Run button.
- Right pane: player reuse + single prompt-card (cur only). Output tab
  rendering from `studio_run.output_json`.
- "Open in Studio" link from `/prompts/{id}` menu.

**PR2 — Version compare**
- Version picker chip on the cur card (was single-version in PR1).
- `+ Compare` button → cmp card materializes with version picker.
- `PromptDiff` (line-LCS) and `OutputDiff` (line-LCS over
  JSON.stringify of output) — ported from `studio.jsx`.
- **Diff vs {cur.label}** toggle on cmp card.
- Player overlay: two stacked range rows for cur + cmp scenes.

**PR3 — Polish**
- Visual matching pass against `styles.css` from the design bundle
  (typography tokens, color palette, spacing, hover states).
- Tighten run-state transitions on the Run button (the brief "✓ Done"
  flash, elapsed-time ticker, cancel affordance).
- Empty-state and error-state polish across the right pane.

## Risks & mitigations

- **Risk:** Annotator worker behavior diverges between `kind=null` and
  `kind='studio'` paths.
  **Mitigation:** Single branch point in `services/annotator.py`,
  covered by a test that runs the same prompt/clip through both paths
  and asserts CatDV write is invoked vs. skipped.
- **Risk:** `studio_run.output_json` shape varies across prompts.
  **Mitigation:** It's whatever the prompt version's `output_schema`
  produced. UI renders defensively: known fields (`scenes`,
  `summary_cz`) render with their own widgets; unknown fields render
  as generic key-value rows.
- **Risk:** Studio runs eat the same CatDV session seat as normal
  jobs (which would be fine — the studio path doesn't *write* to
  CatDV but still needs a session to pull the proxy).
  **Mitigation:** Studio runs share the existing CatDV client and
  session discipline; no separate session.
- **Risk:** Folder list grows unbounded, becomes unwieldy.
  **Mitigation:** Flat-folder constraint keeps the UI shallow; if
  this becomes a problem, add nesting in a follow-up.

## Open questions

None blocking. Items deferred to follow-ups:
- Should `studio_run` history be exposed (timeline view of all past
  runs on a clip)? Probably yes, but out of v1.
- Should we mark a run as "preferred" to disambiguate when multiple
  runs exist for the same (version, clip)? Currently "latest wins";
  may revisit if backtesting workflows demand it.
- Stacked / unified compare layouts: defer to a later PR; the
  side-by-side layout from PR2 is sufficient for the iteration loop.
