# 0090. Write-back routes notes/bigNotes to top-level clip properties, not the user-fields map

**Date:** 2026-06-17
**Status:** Accepted

## Context

The write-back path (`WriteQueue` → `SyncEngine` → `CatdvArchiveAdapter.apply_changes`
→ `build_put_payload` → `PUT /catdv/api/9/clips/{id}`) translates accepted review
items into a CatDV PUT body. `build_put_payload` collected **every** `SetField`,
`AppendNote`, and `ReplaceNote` op into a single `field_changes` dict and emitted it
as `payload["fields"]`.

But in CatDV's clip JSON, `notes` and `bigNotes` are **top-level** clip properties,
while user-defined metadata lives in a separate `fields` map. This is visible in a
real captured clip (`tests/fixtures/catdv_clip_sample.json`) and is exactly how the
read path treats them (`mapping.from_catdv_clip` reads `raw.get("notes")` /
`raw.get("bigNotes")` top-level; `payload._existing_text` reads them top-level too).

So a note write produced `PUT {"fields": {"notes": "..."}}`, which asks CatDV to set a
*user-defined field identified by "notes"* — not the built-in Notes property. The real
notes field was never updated. Because the queue row was still marked `applied` and the
Published view re-reads the (unchanged) top-level notes, the app reported success while
the annotation silently never landed. This hit the most common annotation: an AI
summary written to Notes.

Two things hid the bug: the in-process fake CatDV server merges any PUT body verbatim
(`existing.update(body)`), so the unit test passed regardless of placement; and the read
path was already correct, so round-trips through our own cache looked fine. The FS
provider, which writes notes into its top-level `notes` dict, was the working reference.

## Alternatives

1. **Translate `fields.notes` → top-level inside `CatdvClient.put_clip`.** Rejected:
   hides the real payload shape one layer below where it's built, and the client is a
   thin transport that should not know clip semantics.
2. **Special-case notes only in the adapter after `build_put_payload`.** Rejected: the
   payload builder already owns the field-vs-note distinction (`_existing_text`), so the
   write routing belongs in the same place — keeping read and write symmetric.
3. **Route by op type (any `AppendNote`/`ReplaceNote` → top-level).** Rejected: a note op
   may legitimately target a `pragafilm.*` user text field, which must stay under
   `fields`. Routing must key on the *target*, not the op type.

## Decision

`build_put_payload` routes note ops whose target is in `TOP_LEVEL_NOTE_TARGETS`
(`"notes"`, `"bigNotes"`) to the top level of the PUT body
(`payload["notes"]` / `payload["bigNotes"]`); all other note targets and every
`SetField` continue to go under `payload["fields"]`. This mirrors `_existing_text`'s
read routing exactly, so reads and writes stay symmetric. The three payload unit tests
that previously asserted `payload["fields"]["notes"]` were corrected to assert
top-level placement (they had codified the bug), and a new test pins that a note
targeting a user field still routes under `fields`.

## Consequences

- AI summaries and other notes/bigNotes annotations now actually persist to CatDV.
- The `notes`/`bigNotes` constant lives next to the builder; adding another built-in
  top-level note property is a one-line change.
- The fake CatDV server's permissive merge still cannot catch a future placement
  regression — the precise guard is the `build_put_payload` unit test, which asserts
  the exact PUT-body shape. A round-trip test through the fake would not have caught
  the original bug and was deliberately not added.
