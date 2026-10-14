# Writeback redesign — AI-owned markers and description (#123, related #43)

Status: design, awaiting approval · Issue: futurememorylab/ai-archive#123

## Problem

Writeback can only **add**. Publishing a draft emits `AddMarkers` (merged by
in-frame) and `AppendNote` on `pragafilm.popis.materialu`. So:

- an edited AI marker whose in-point moved becomes a duplicate;
- a marker deleted in a later draft is never removed from CatDV;
- every re-publish appends another summary block to the human description field;
- app-authored markers are indistinguishable from human ones in CatDV. The only
  "ours" signal is in-seconds remembered in `clip_versions` snapshots (ADR 0101
  `drop_secs`), which also collides with human markers on the same frame;
- bug: `publish_service._materialize`, `clip_versions_backfill` and
  `restore_service` collapse every non-`bigNotes` note target to `notes`, so
  *Make live* / *Restore* write the summary into CatDV's top-level `notes`
  instead of the field it came from.

The archive admin has added an event-marker category **`AI`** and a clip field
**`pragafilm.popis.ai`** so AI and human annotations can be told apart.

## CatDV 9.3 REST facts (research)

Source: catdv-docs.services.quantum.com REST API Reference + 9.3 release notes;
plus our own observations in ADR 0090/0101/0117.

| Fact | Status |
|---|---|
| `PUT /clips/{id}` is a partial update — only supplied properties change | documented |
| A supplied `markers` array replaces the clip's whole marker list | observed (ADR 0101), not documented |
| Markers have no ID; no per-marker endpoint exists | documented (Marker object has no id) |
| Marker = `{name, category, in, out?, description, fields}`; `category` is a user-defined string configured under *Event Marker Categories* | documented |
| The admin's "AI" marker type is carried as `"category": "AI"` | inferred (only candidate property) |
| User field write = `fields: {"pragafilm.popis.ai": "…"}`, value overwrites; no append | documented / inferred |
| Supplying some `fields` keys leaves the others untouched | inferred, **unverified** |
| Clearing a field (send `""`/`null`) | **unverified** |
| `color` on a marker | undocumented — stop sending it |

Unverified rows are settled by Task 1 (live probe) before any behaviour depends
on them.

## Decision

**The `AI` category is the ownership tag.** A marker with `category == "AI"`
belongs to the app; every other marker belongs to humans and is never modified.

### Markers

- New op `ReplaceAIMarkers(desired, legacy)` replaces both `AddMarkers` and
  `ReconcileMarkers` as the op the app **emits**. Payload rule
  (`build_put_payload`):

  ```
  keep   = [m for m in live if m.category != "AI" and not is_legacy(m)]
  result = keep + [to_catdv(d, category="AI") for d in desired]
  ```

  This gives update and delete without marker IDs. Publish and *Make live*
  become the same operation.
- **Publish replaces the AI set.** `desired` = all accepted marker items of the
  draft. A draft with **no marker items at all** (e.g. a fields-only image
  prompt) emits no marker op, so it never wipes the markers.
- **Gemini's category becomes a name prefix**: `"[Interview] Two men talk"`.
  It's done in `mapping.marker_to_catdv` at write time, inside the
  `MARKER_NAME_MAX` clamp. The draft editor and the prompt schema keep
  `category` unchanged. `color` is no longer sent; CatDV colours by category.
- **Human edits.** An AI-category marker edited in CatDV is overwritten on the
  next publish. To keep their edit, a human changes the marker's category; after
  that the app never touches it. This rule is documented in the UI help and the
  ADR.
- **Legacy cleanup.** Markers written before this change carry Gemini
  categories. `legacy` = `(in_secs, name)` pairs taken from the clip's
  `clip_versions` snapshots. A non-AI live marker is dropped only when **both**
  its frame (at the clip's real fps) and its name match a snapshot marker. Name
  matching makes this safe to run on every write, so nothing stores a "migrated
  once" flag. Human markers on the same frame survive unless they also have the
  same name.
- `AddMarkers` / `ReconcileMarkers` stay **deserializable**, so pending rows
  already in the queue still drain; nothing emits them any more. They are
  deleted in a follow-up once no deployment has them queued.

### Description

- The summary goes to **`pragafilm.popis.ai`** with mode **`replace`**
  (`SetField` semantics). The field is AI-only, so overwriting is correct.
  `pragafilm.popis.materialu` is never written by the app again.
- Seeds (`default_template.json`, `image_template.json`) change
  `summary_cz → {kind: note, target: pragafilm.popis.ai, mode: replace}`.
  Seeds only insert on first boot. Existing production prompts get a new
  version through Prompt Studio, a manual step listed in the acceptance flows.
  No data migration rewrites prompt versions.
- Snapshot fix: `_materialize`, backfill and restore keep each note under
  its real `target_identifier`. `_switch_ops` emits `ReplaceNote` per
  snapshot key, so *Make live* writes `pragafilm.popis.ai`, not `notes`.
- When a new version has no summary, the field is **not** cleared unless Task 1
  proves that clearing is safe. If it is safe, clearing it is part of the
  replace rule.

### Filesystem sidecar adapter

The adapter implements `ReplaceAIMarkers` with the same rule (sidecar markers
carry `category`). Today it raises `FatalProviderError` on `ReconcileMarkers`,
so *Make live* is broken offline as well.

### #43 follow-ups

- Optimistic apply is already honest: `applied_at` is the dedup key and
  `synced_at` confirms the write. Failures surface in the sync chip, the drawer
  and toasts. Unchanged here.
- The comment "timeline stays blue after apply" (12 June) predates e85842a
  (19 June). This PR's walkthrough asserts the published band turns orange
  after sync. If it doesn't, the walkthrough catches it and the fix is in
  scope.

## Out of scope

- Two-way sync and conflict detection on human edits of AI markers (decided:
  overwrite and document it).
- A bulk catalog migration script for legacy markers.
- Marker user fields.

## ADR

A new ADR, *"AI category is the ownership tag for writeback"*, supersedes the
marker parts of ADR 0101 (`drop_secs` ownership). It changes Invariant 9/9a in
`docs/architecture-invariants.md`: "ours" = `category == "AI"`.

## Manual acceptance flows

Run on the dev server against a **scratch clip**. Use one CatDV seat, and stop
the server with `server-stop`.

1. **First publish of a legacy clip**
   - Pick a clip that has old app markers (Gemini categories) and at least one
     human marker.
   - Re-run the annotation. Accept all items and click *Apply draft*.
   - After the sync chip clears, open the clip in the CatDV web client. The old
     app markers are gone. The human marker is untouched. The new markers have
     category **AI** and names like `[Interview] …`.
   - `pragafilm.popis.ai` holds the summary. `pragafilm.popis.materialu` is
     unchanged.
2. **Update and delete**
   - Re-run the same clip. In the draft, edit one marker's in-point and text,
     reject another, and apply.
   - In CatDV, the edited marker appears once, at the new in-point with the new
     text. The rejected one is gone. There are no duplicates.
   - `pragafilm.popis.ai` is replaced, not appended.
3. **Human ownership**
   - In CatDV, change one AI marker's category to `Event` and edit its text.
   - Re-run and publish the clip again. That marker keeps the human edit and
     is not removed.
4. **Fields-only draft keeps markers**
   - Publish an image-prompt draft that has no marker items on a clip with AI
     markers. The AI markers are unchanged.
5. **Make live**
   - Open *History* and click *Make live* on the previous version.
   - In CatDV, the AI marker set equals that version's markers. Human markers
     are untouched. The summary is in `pragafilm.popis.ai`, and the top-level
     `notes` is unchanged.
6. **Timeline colour (#43)**
   - After *Apply draft*, wait for sync. Without reloading, the timeline bands
     switch from blue (draft) to orange (published).
7. **Prompt migration**
   - In Prompt Studio, open the production prompt. Create a version whose
     `summary_cz` target is `pragafilm.popis.ai` with mode `replace`, and
     promote it. Flows 1–2 use this version.
8. **Offline (FS provider)**
   - With the filesystem provider, publish and then *Make live*. No
     `unknown ChangeOp` error appears, and the sidecar markers follow the
     same rule.
