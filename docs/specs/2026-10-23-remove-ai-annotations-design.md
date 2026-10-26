# Remove a clip's AI annotations — design

Date: 2026-10-23 · Requested by Gory

## Problem

An operator wants to throw away a clip's published AI annotation and annotate
it again from scratch. Today the only way is to edit the clip by hand in the
archive, marker by marker, and the app still shows the clip as "In archive".

## What it does

The clip ⋯ menu gets **Remove AI annotations** (a danger row), shown only when
the archive holds AI markers or the AI description for the clip. It opens a
confirm dialog:

> **Remove AI annotations?**
> This deletes 2 AI markers and the AI description from the archive.
> Human markers, the human description and all other metadata stay as they are.
> The AI draft waiting for review is discarded too. *(only when there is one)*
> The clip goes back to Not annotated, so you can annotate it again.
>
> [Cancel] [Remove from archive]

Confirming queues the write; the sync chip and the clip page follow it like a
publish, and the AI markers and description disappear from *In archive* once it
lands.

## How AI and human data are told apart

The rule is ADR 0127's ownership rule, unchanged. The app owns exactly:

- markers whose **category is `AI`** (`AI_MARKER_CATEGORY`), and
- the field **`pragafilm.popis.ai`** (`AI_OWNED_FIELDS`).

Everything else is human and never written: markers with any other category
(including legacy app markers from before ADR 0127, which carry a Gemini
category), `pragafilm.popis.materialu`, the decade/year fields, top-level
`notes`/`bigNotes`, every other field.

The removal sends exactly the write a publish of "nothing" would:

- `ReplaceAIMarkers(desired=())`: the CatDV payload builder sends
  `[every live non-AI marker, byte-identical]` and no AI markers;
- `SetField` and `ReplaceNote` of `""` on `pragafilm.popis.ai` (both shapes the
  summary may have been written with; on CatDV both land in `fields[target]`;
  `""` clearing a field was verified live on 2026-10-14).

Any other target would raise `FatalProviderError` in the payload builder, so
the removal cannot reach human data even by mistake. The write has no expected
etag: it is computed from the live clip at write time and touches only owned
data, so a human edit meanwhile is not a conflict. An `AI` marker a human edited
in the archive is removed too (same as on a publish); to keep one, change its
category first.

Known limit: legacy AI markers written before ADR 0127 are indistinguishable
from human markers in the archive, so they stay. Removing them is a manual job.

## App side

- `PublishService.remove_ai` records the removal as a clip version with
  origin `removed` (snapshot without AI markers and AI description; its
  `annotation_id` is the newest run it clears), queues the ops above stamped
  with that version, and discards the clip's unapplied draft items
  (annotation-bound only; Studio rows are untouched). One commit.
- When the write lands the removal version goes live and supersedes the old
  one. `live_version_num_by_clip` skips `removed` versions, so the status
  column reads "—" (Not annotated).
- `ClipVersionsRepo.reset_clip_ids`: clips whose newest version is a live or
  in-flight removal with no newer annotation. The Annotation status filter
  treats them as not annotated (and not *In archive*); the clip page shows no
  draft for them. A new run ends the reset; the next publish starts from the
  removal's snapshot.
- Route: `POST /api/review/clips/{id}/remove-ai` → `{"version_id": …}`. Same
  access as Accept & apply.

## Manual acceptance flows

1. **Remove, human data stays.** Open a clip that is *In archive* and has at
   least one human marker and a human description. Click ⋯ → *Remove AI
   annotations*. The dialog names the number of AI markers and the AI
   description. Click *Remove from archive*. Within a few seconds the toast says
   it synced; *In archive* lists only the human markers and the human
   description. Open the clip in the archive's own client: the human markers,
   description and other fields are as before; no `AI` marker and no AI
   description remain.
2. **Cancel changes nothing.** Repeat 1 up to the dialog and click *Cancel*:
   nothing is written, the sync chip stays idle, the markers are unchanged.
3. **Back to Not annotated.** After 1, go to the clip list. The clip's status
   column shows "—"; with Annotation status = *Not annotated* the clip is
   listed, with *In archive* it is not.
4. **Annotate again.** Annotate the clip from 3. A fresh draft appears; accept
   and apply it. The new AI markers and description are written and the clip
   reads *In archive* again.
5. **Open draft is discarded.** Annotate a clip, leave the draft unreviewed,
   then remove its AI annotations: the dialog says the draft is discarded too,
   and afterwards the Draft tab is empty.
6. **Nothing to remove.** A clip with no `AI` markers and no AI description
   shows no *Remove AI annotations* row in the ⋯ menu.
