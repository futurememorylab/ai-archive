# Batches redesign: named batches, visible contents, plain statuses

**Date:** 2026-10-17
**Status:** Approved (design canvas "Batches redesign", Gory, 2026-10-17)
**Extends:** `docs/specs/2026-06-02-batches-hub-design.md`

## Problem

Archivists didn't understand the Batches page:

- A batch is labelled by its prompt ("#41 Obraz & sekvence v7"). Every batch
  run with the same prompt looks the same, so the label doesn't say which
  clips it covers.
- Nothing on the row shows what's in the batch; you have to click through to
  a filtered search.
- Two progress bars (Completed, Reviewed), a status pill and a metric strip
  repeat the same facts in different words ("Awaiting review", "Applied",
  "Drafts produced").
- In the search results, the "Drafts" column mixes "Live v3" and "Draft
  12m · 3f", which users read as jargon.

## Goals

1. **Every batch has a name** a person can recognise and change.
2. **The row shows what's in the batch**: a stack of clip thumbnails that
   stays the same width at 3 clips or 140, and an expandable list of every
   clip with its status.
3. **One next step per batch**, grouped by what it needs: *Needs you*,
   *Running*, *Done*.
4. **One plain status vocabulary** in the batches page and the search results
   column: To review, Running, Waiting, Failed, Saving to CatDV…, Couldn't
   save, In CatDV, Stopped.
5. **Same sizes as the search page.** Thumbnails are the search list's
   64×36 `.thumb` (same shimmer and empty placeholder), buttons are the
   shared `.btn` / `.btn.sm`, row text is 13px with 11px sub-lines, and the
   expanded clip list *is* the shared `_video_list.html` table. Nothing gets
   a bespoke size.

Out of scope: the clip page's Draft / Live controls (#137 just reworked
them; renaming those is a follow-up), the per-clip "Batch" status pills
shown while a search is filtered to one batch.

## How a batch is named

A batch is still the set of jobs sharing a `run_group` (or a single job).
The name is stored on each job as `jobs.batch_name`; the batch reads the
name of its first job (lowest id).

- **New batch (Batches page picker)** and **Annotate selected (search
  page)** show a *Batch name* field, pre-filled and editable:
  - the search text, when the clips were found by searching ("Tatry");
  - otherwise the first clip's name, plus "and N more" for several clips
    ("SIKL_0412 Výlet do Tater and 23 more").
  Until the user types in the field, the default follows the selection.
- **Single-clip annotate** (clip page) and any API caller that sends no
  name: the server applies the same rule from the clip names it has cached
  (`clip_cache`), falling back to "Clip 123".
- **Rename** at any time from the batch row (pencil → inline field →
  Enter). The new name is written to every job in the batch.
- Names are trimmed, must not be empty, and are capped at 120 characters.

## Migration of existing batches

Migration `0032_jobs_batch_name.sql`:

1. `ALTER TABLE jobs ADD COLUMN batch_name TEXT`.
2. Backfills every existing non-studio batch with the content rule above
   (first clip name from `clip_cache`, else "Clip <id>", plus "and N more"
   for the batch's distinct clips), written to all of the batch's jobs.
   Studio jobs stay NULL; they never appear on the Batches page.

Nothing else changes in `jobs`, `job_items` or the run-group grouping, so
the migration is additive and old rows keep working. If a name is ever
missing at read time (a job with no items), the page shows "Batch #<id>".

## The Batches page

- Shared `.page-hdr` ("Batches", `+ New batch` as `.btn.primary`), then one
  muted line: what a batch is and that nothing reaches CatDV until it's
  reviewed. The metric strip goes.
- Three sections, each with a small uppercase heading (the `.vlist` header
  style), a count and a one-line hint. Empty sections are hidden.
  - **Needs you**: anything with clips to review, failed clips or failed
    write-backs.
  - **Running**: any job still running.
  - **Done**: everything else, including "Saving N to CatDV…" and Stopped.
- One row per batch:
  - **Thumbnail stack**: up to 6 clips in full; past 6, five thumbnails and
    a "+N" tile of the same size. `<img loading="lazy">` on
    `/api/media/<id>/thumb`, exactly like the search list. The first clip
    ids per batch come from one window-function query for the whole page.
  - **Name** (13px, weight 500) with a rename button, and a sub-line:
    clip count · started · prompt vN · model · cost.
  - **Progress**: one segmented bar (reviewed, to review, saving, running,
    waiting, failed, stopped) and a legend with counts, plus a headline
    ("16 clips to review", "Running · 23 of 38 done", "All reviewed").
  - **Outline and toggle**: each batch is an outlined card. A caret starts
    the row and turns when it opens; clicking anywhere on the top line
    except its buttons, menu or rename form opens or closes the clip list
    inside the card (the caret is the keyboard toggle).
  - **Actions** (`.btn.sm`), the clip page's pattern: one main action and a
    ⋯ menu. The main action is Cancel while running, else Review N clips
    (primary), else Retry N failed, else Restart N stopped (runs the clips
    a Cancel stopped; clips that already ran keep their results). The menu
    holds whichever of Retry / Restart isn't the main action, then Delete
    batch.
  - **Delete batch** removes the batch's jobs and their items only. What
    it produced stays: annotations keep their drafts and accepted results
    on the clips (their `job_id` is cleared), so unreviewed drafts still
    show under To review on the Clips page. It is refused (409, menu item
    disabled) while the batch runs.
- **Live updates** patch the page in place: sections and rows are matched by
  key and only the row blocks whose content changed are replaced, so
  thumbnails and open clip lists don't reload while a batch runs.
- **Show clips** loads `/batches/clips?job_ids=…` into the row: the shared
  `_video_list.html` (thumbnail column, no cache column) with a Status pill
  and the error text for failed clips, ordered to-review first, then
  failed, running, waiting, the rest. It shows the first 50 clips; larger
  batches get "Open all N in search", which goes to the existing
  batch-filtered search.

## Search results column

- "Drafts" becomes **Annotation**: Not annotated (—), To review (counts in
  the tooltip: "12 markers, 3 fields"), In CatDV (the tooltip names the
  version), Saving to CatDV…, Couldn't save, Changed in CatDV.
- The **Batch** column shows the batch name (ellipsis, full name in the
  tooltip) instead of "#41". The Batch filter dropdown lists batch names.

## Manual acceptance flows

Test data: at least two batches, one with more than six clips and some
clips still to review, and one fully reviewed.

1. **Rows show names and contents**
   - Open `/batches`. Batches appear under Needs you / Running / Done.
   - The batch with more than six clips shows five thumbnails and a "+N"
     tile; the thumbnails match the search list's size.
   - Each row's sub-line names the prompt version and model.
2. **Existing batches got names**
   - On a database from before this change, every older batch shows a name
     like "<first clip> and N more" instead of a prompt name.
3. **Rename**
   - Click the pencil on a batch, type "Tatry 1934", press Enter.
   - The row shows the new name; reload, it's still there.
   - Open the search page: that batch's clips show "Tatry 1934" in the
     Batch column and in the Batch filter.
4. **Show clips**
   - Click a batch row's name line or progress bar (not a button). The
     caret turns down and a clip table like the search list opens under the
     row with each clip's status; failed clips show their error.
   - Click a clip: its clip page opens.
   - Back on `/batches`, click the open row's top line: the list closes and
     the caret points right again. Clicking Review or ⋯ never toggles it.
5. **New batch name**
   - Click + New batch, search "Tatry", tick two clips. The Batch name
     field reads "Tatry". Change it and start; the new row carries that
     name under Running, then Needs you.
6. **Search page Annotate selected**
   - On the search page, tick a clip and choose Annotate selected. The
     Batch name field reads the clip's name; start it and check the
     Batches page.
7. **Plain statuses**
   - In the search results, the Annotation column reads To review / In
     CatDV / Not annotated; hovering To review lists the marker and field
     counts.
8. **Stop and restart**
   - Start a batch of a few clips and open `/batches` while it runs. The
     row's thumbnails stay put (no flashing) as its progress updates.
   - Click Cancel. The row moves to Done and reads Stopped, with a
     "Restart N stopped" button.
   - Click it. The batch moves back to Running and runs only the stopped
     clips.
9. **Delete a batch**
   - On a finished batch with drafts to review, open ⋯ and choose Delete
     batch. The confirmation says saved results stay on their clips; accept.
   - A "Batch deleted." toast shows and the row is gone.
   - On the Clips page, filter To review: the batch's clips are still
     there with their drafts.
   - On a running batch, ⋯ shows Delete batch greyed out ("Cancel it
     first").
