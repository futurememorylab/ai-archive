# ADR 0127 — The app writes only what it owns (AI-category markers + `popis.ai`)

Date: 2026-10-14 · Status: accepted · Issue:
futurememorylab/ai-archive#123 (related #43)

Spec: `docs/specs/2026-10-14-writeback-redesign-ai-markers-design.md`.

## Context

Writeback could only **add**. Publishing a draft emitted `AddMarkers`
(merged by in-frame) and `AppendNote` on `pragafilm.popis.materialu`. That
overwrote and duplicated human data:

- An edited AI marker whose in-point moved became a duplicate; a deleted one
  was never removed.
- Every re-publish appended another summary block to the **human** description
  field.
- `AddMarkers` replaced a human marker on the same frame; `ReconcileMarkers`
  (*Make live*) dropped it; `SetField` overwrote human values in shared fields.
- App-authored markers were indistinguishable from human ones.
- `_materialize` / the version backfill / restore collapsed every note target
  except `bigNotes` into the top-level `notes`, so *Make live* / *Restore*
  wrote the AI summary into CatDV's human `notes`.

The archive admin added an event-marker category **`AI`** and a clip field
**`pragafilm.popis.ai`**, so AI and human annotations can be told apart.

## Decision

**The app writes only what it owns. It owns exactly two things: markers with
`category == "AI"`, and the field `pragafilm.popis.ai`
(`AI_OWNED_FIELDS`). Everything else in the archive is human — including
legacy app markers written before this change — and is never modified,
deleted or overwritten.**

The rule is enforced in **one place per provider**, the payload builder, not
in each caller:

- **CatDV:** `archive/providers/catdv/payload.build_put_payload`.
- **FS sidecar:** `archive/providers/fs/adapter.apply_changes`.

A ChangeOp that would write a non-AI marker, or a field/note target outside
`AI_OWNED_FIELDS`, raises `FatalProviderError` and is never silently written.

### Markers

- A new op, `ReplaceAIMarkers(desired)`, is the only marker op the app
  **emits**. The payload rule is:

  ```
  result = [m for m in live if m.category != "AI"]   # human: untouched, byte-identical
         + [to_ai(d) for d in desired]                # ours: full replace
  ```

  This gives update AND delete without marker IDs, makes publish and *Make
  live* the same operation, and keeps a same-frame human/AI pair (both stay).
  When several apply batches merge into one ChangeSet, the **freshest** marker
  op defines the AI set (a replace, not an accumulation).
- **Gemini's category becomes a `[Category] ` name prefix** at write time
  (`model.ai_marker_name`, inside the `MARKER_NAME_MAX` clamp); `category` is
  set to `"AI"` and `color` is no longer sent (CatDV colours by category). The
  view model strips the prefix back into a category chip for display.
- **Legacy markers are kept.** Markers written before this change carry Gemini
  categories, so they are treated as human: kept, shown as *Human*, never
  touched. ADR 0101's `drop_secs` ownership is **superseded** and the logic
  deleted.
- `AddMarkers` / `ReconcileMarkers` stay **deserializable** so queued rows
  drain through the same guard; nothing emits them any more.

### Description & fields

- The summary goes to **`pragafilm.popis.ai`** in **replace** mode — the only
  field the app writes. `popis.materialu`, top-level `notes`/`bigNotes`, and
  every other field are never written. This **supersedes** the
  field-overwrite behaviour of ADR 0090 and ADR 0097, and the
  `popis.materialu` append of the seeds.
- **Shared fields (decade, year) become suggestions.** A draft item whose
  target is not owned renders as *Suggestion — not written to CatDV* with a
  Copy button and no keep checkbox; the write queue never enqueues it and it
  does not count toward the draft's pending-review total.
- Snapshots keep each value under its **real** `target_identifier`
  (`_materialize` / backfill fix); `_switch_ops` emits `ReplaceAIMarkers` plus
  only `AI_OWNED_FIELDS` ops. A field is **never cleared** when a new version
  omits it — clearing is unproven (Task 1's live probe has not been run).

### Provenance UI

Three states on the clip page, coloured from existing tokens: **Human** (grey
`--text-3`), **AI · published** (orange `--accent`), **AI · draft** (blue
`--info`). `ui/view_models` adds `origin` to marker and field views and flags
suggestions; the templates render `status_pill`s, split the timeline band by
origin, and show a live legend.

### Human edits

An AI-category marker a human edits in CatDV is overwritten on the next
publish. To keep it, the human changes its category; from then on it is human
and is never touched.

## Consequences

- No more duplicate markers, no more stacked summary blocks, no more
  overwritten human fields.
- A clip published before this change may show its old (now "human") markers
  next to the new AI set. Accepted trade-off; no migration.
- Shared-field values are no longer written by the app at all — a human copies
  them across. Accepted per the spec.
- The guard fails a stale queued op to a non-owned target loudly instead of
  writing it; that op surfaces in the sync drawer rather than corrupting human
  data.

## Supersedes / updates

- Supersedes ADR 0101's `drop_secs` marker ownership.
- Supersedes the field-overwrite behaviour of ADR 0090 and ADR 0097.
- Updates Invariant 9 / adds Invariant 9b in
  `docs/architecture-invariants.md`.
