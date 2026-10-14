# Writeback redesign — AI-owned markers and description (#123, related #43)

Status: design, awaiting approval · Issue: futurememorylab/ai-archive#123

## Problem

Writeback can only **add**. Publishing a draft emits `AddMarkers` (merged by
in-frame) and `AppendNote` on `pragafilm.popis.materialu`. This causes several
problems:

- An edited AI marker whose in-point moved becomes a duplicate.
- A marker deleted in a later draft is never removed from CatDV.
- Every re-publish appends another summary block to the **human** description
  field.
- The app overwrites human data. `AddMarkers` replaces a human marker on the
  same frame. `ReconcileMarkers` (*Make live*) drops it. `SetField` overwrites
  human values in shared fields (decade, year).
- App-authored markers look the same as human ones, in CatDV and in the UI.
- Bug: `publish_service._materialize`, `clip_versions_backfill` and
  `restore_service` collapse every note target except `bigNotes` into `notes`.
  As a result, *Make live* / *Restore* write the summary into CatDV's top-level
  `notes`.

The archive admin has added an event-marker category **`AI`** and a clip field
**`pragafilm.popis.ai`**, so AI and human annotations can be told apart.

## CatDV 9.3 REST facts (research)

Sources: the REST API Reference at catdv-docs.services.quantum.com, the
CatDV Server 9.3 release notes, and our own observations in ADR
0090/0101/0117.

| Fact | Status |
|---|---|
| `PUT /clips/{id}` is a partial update: only the supplied properties change | documented |
| A supplied `markers` array replaces the clip's whole marker list | **verified live** (probe, 2026-10-14) |
| Markers have no ID, and there is no per-marker endpoint | documented (the Marker object has no id) |
| A marker is `{name, category, in, out?, description, fields}`. `category` is a user-defined string configured under *Event Marker Categories* | documented |
| The admin's "AI" marker type travels as `"category": "AI"` | **verified live**: it reads back exactly `"AI"` |
| A user field is written as `fields: {"pragafilm.popis.ai": "…"}`; the value overwrites and there is no append | documented / inferred |
| Supplying some `fields` keys leaves the other keys untouched | **verified live** (all 24 other fields unchanged) |
| Clearing a field (sending `""` or `null`) | **verified live**: `""` removes the field (GET omits it) |
| A `color` property on markers | undocumented, so stop sending it |

Task 1 (a live probe) settles the unverified rows before any behaviour relies
on them.

> **Probe run (2026-10-14, scratch clip 913922, restored afterwards):**
>
> ```
> - clip 913922: 1 markers (1 non-AI), 25 fields
> - markers: sent 2, GET returns 2 (REPLACE)
> - markers: AI marker read back with category ['AI'] (want ['AI'])
> - markers: human markers are byte-identical after an AI-marker PUT
> - fields: 'pragafilm.popis.ai' read back as 'probe-writeback-ai-value'
> - fields: the other keys are untouched after writing only 'pragafilm.popis.ai' (per-key merge CONFIRMED)
> - clear: after PUT 'pragafilm.popis.ai'="", GET returns None
> - restore: original markers and probe field written back
> ```
>
> Clearing is now known to be safe. The implementation still never clears
> `pragafilm.popis.ai` when a new version has no summary; clearing it is a
> follow-up.

## The rule

> **The app writes only what it owns. It owns exactly two things: markers
> with `category == "AI"`, and the field `pragafilm.popis.ai`. Everything
> else in CatDV is human and is never modified, deleted or overwritten. That
> includes legacy app markers written before this change.**

The rule is enforced in one place for each provider, the payload builder,
and not in each caller:

- **CatDV:** `build_put_payload`.
- **FS sidecar:** `fs/adapter.apply_changes`.

Any op that would write a non-AI marker, or a field outside
`AI_OWNED_FIELDS = ("pragafilm.popis.ai",)` (a constant in `archive/model.py`),
raises `FatalProviderError` and is never silently written. A guard test pins
this down.

### Markers

- A new op, `ReplaceAIMarkers(desired)`, replaces `AddMarkers` and
  `ReconcileMarkers` as the op the app **emits**. The payload rule is:

  ```
  result = [m for m in live if m.category != "AI"]          # human: untouched, byte-identical
         + [to_catdv(d, category="AI") for d in desired]   # ours: full replace
  ```

  This gives update and delete without marker IDs. Publish and *Make live*
  become the same operation. Same-frame collisions no longer matter: a human
  marker and an AI marker on the same frame both stay.
- **Publish replaces the AI set.** `desired` is every accepted marker item of
  the draft. A draft with **no marker items** (for example a fields-only image
  prompt) emits no marker op, so it never wipes the AI markers.
- **Gemini's category becomes a name prefix**, for example
  `"[Interview] Two men talk"`. This happens at write time in
  `mapping.marker_to_catdv`, inside the `MARKER_NAME_MAX` clamp. The view model
  strips the prefix back into the category chip for display. The draft editor
  and the prompt schema keep `category` unchanged. `color` is no longer sent;
  CatDV colours markers by category.
- **Legacy markers are kept.** Markers the app wrote before this change carry
  Gemini categories. They are treated as human: kept, shown as *Human*, and
  never touched. The `drop_secs` logic (ADR 0101) is deleted. A clip that was
  published before may therefore show its old markers next to the new AI set.
  That is the accepted trade-off.
- **Human edits.** An AI-category marker that someone edits in CatDV is
  overwritten on the next publish. To keep it, the human changes its category.
  From then on it is human and is never touched. The UI help and the ADR both
  state this.
- `AddMarkers` and `ReconcileMarkers` stay **deserializable**, so rows already
  in the queue still drain. When those rows drain, they also pass through the
  guard: only `AI` markers are written, and human markers are kept. Nothing
  emits these ops any more. They are deleted in a follow-up once no deployment
  has them queued.

### Description and fields

- The summary goes to **`pragafilm.popis.ai`** in **replace** mode, the only
  field the app writes. `pragafilm.popis.materialu`, the top-level `notes` and
  `bigNotes`, and every other field are never written by the app.
- **Shared fields such as decade and year become suggestions only.** Draft
  items whose target is not in `AI_OWNED_FIELDS` render as *Suggestion — not
  written to CatDV*. They have no keep checkbox; they offer a copy button so a
  human can paste the value into CatDV themselves. The write queue never
  enqueues them. They don't count towards the draft's "pending review" total,
  so a draft with only suggestions left counts as fully applied.
- Seeds (`default_template.json`, `image_template.json`) change to
  `summary_cz → {kind: note, target: pragafilm.popis.ai, mode: replace}`.
  The decade and year targets stay in the seeds, but the rule above turns them
  into suggestions. Seeds only insert on first boot, so existing production
  prompts get a new version through Prompt Studio (see acceptance flow 7).
  Until then, the guard makes an old `popis.materialu` target a suggestion and
  never a write.
- Snapshot fix: `_materialize`, the backfill and restore keep each value under
  its real `target_identifier`. `_switch_ops` emits ops only for
  `AI_OWNED_FIELDS`, so *Make live* writes `pragafilm.popis.ai` and nothing
  else.
- If a new version has no summary, the field is **not** cleared, unless Task 1
  proves that clearing is safe.

### UI — who wrote what, and in which state

There are three provenance states on the clip page:

| State | Source | Timeline band | Marker-card pill | Editable |
|---|---|---|---|---|
| **Human** | CatDV marker with category ≠ `AI` | grey (`--text-3`) | `Human` | no, read-only |
| **AI · published** | CatDV marker with category = `AI` | orange (`--accent`) | `AI · published` | no (re-run to change) |
| **AI · draft** | review item not yet applied | blue (`--info`) | `AI · draft` | yes (keep / edit / drag) |

- `ui/view_models._marker_view` adds `origin: "human" | "ai"`, based on
  `category == "AI"`. The draft arrays carry `origin: "ai_draft"`. The
  constant `"AI"` lives in `archive/model.py` (`AI_MARKER_CATEGORY`), next to
  `AI_OWNED_FIELDS`, and is not hardcoded in templates.
- The pill renders with `ui.status_pill` from `_ui.html`; no new primitive is
  needed. The band colours come from the existing tokens: the published row
  splits into a human class and an AI class (`.range.origin-human` is grey).
- The timeline legend reads `● Human N · ● AI published N · ● AI draft N`.
- The Fields tab adds the same pill to each field. `pragafilm.popis.ai` is
  *AI*; every other field is *Human*. Suggestion items show
  `AI · suggestion`.
- The *Summary* echo in the Description tab shows `pragafilm.popis.ai` (AI)
  and `popis.materialu` (Human) as separate, labelled blocks. Today it echoes
  only the top-level `notes`.

### Filesystem sidecar adapter

The sidecar adapter implements `ReplaceAIMarkers` and the same guard. Sidecar
markers already carry `category`. Today the adapter raises
`FatalProviderError` on `ReconcileMarkers`, so *Make live* is broken offline
too.

### #43 follow-ups

- Optimistic apply is already honest. `applied_at` is the dedup key, and
  `synced_at` confirms the write. Failures surface in the sync chip, the drawer
  and toasts. This design doesn't change that.
- The comment "timeline stays blue after apply" (12 June) predates commit
  e85842a (19 June). The walkthrough in this PR asserts that the band turns
  orange after sync. If it doesn't, the fix is in scope.

## Out of scope

- Two-way sync, and conflict detection when a human edits an AI marker
  (decided: overwrite, and document it).
- Any migration or cleanup of legacy markers. They stay.
- Marker user fields, and writing shared fields.

## ADR

A new ADR, *"The app writes only what it owns (AI category + popis.ai)"*.

- It supersedes ADR 0101's `drop_secs` ownership.
- It supersedes the field-overwrite behaviour of ADR 0090 and ADR 0097.
- It updates Invariant 9/9a in `docs/architecture-invariants.md`.

## Manual acceptance flows

Run these on the dev server against a **scratch clip**. Use one CatDV seat,
and stop the server with `server-stop`.

1. **Publish next to human and legacy markers**
   - Pick a clip that has old app markers and at least one human marker.
     Record its full marker list and its fields in the CatDV web client.
   - Re-run the annotation, accept all items, and click *Apply draft*.
   - After the sync chip clears, check CatDV:
     - Every previously recorded marker is still there, unchanged.
     - The new markers have category **AI** and names like `[Interview] …`.
     - `pragafilm.popis.ai` holds the summary.
     - `pragafilm.popis.materialu`, the decade, the year and `notes` are
       unchanged.
2. **Update and delete AI markers only**
   - Re-run the same clip. In the draft, edit one marker's in-point and text,
     reject another, and apply.
   - In CatDV, the edited AI marker appears once, at its new position with its
     new text. The rejected marker is gone. There are no duplicates, and every
     non-AI marker is unchanged.
   - `pragafilm.popis.ai` has been replaced, not appended to.
3. **Human takes ownership**
   - In CatDV, change one AI marker's category to `Event` and edit its text.
   - Re-run and publish again. That marker keeps the human edit, and the clip
     page shows it as **Human** (grey).
4. **Provenance UI**
   - On the clip page, check the three states:
     - Human markers have grey bands and a `Human` pill, with no edit control.
     - Published AI markers are orange, with `AI · published`.
     - Draft items are blue, with `AI · draft`, plus keep and edit controls.
   - The legend counts match.
   - In the Fields tab, decade and year draft items show
     `AI · suggestion` with a copy button and no keep checkbox.
5. **Fields-only draft keeps markers**
   - On a clip with AI markers, publish an image-prompt draft that has no
     marker items. The AI markers are unchanged.
6. **Make live**
   - In *History*, click *Make live* on the previous version.
   - In CatDV, the AI marker set now equals that version's markers. Human
     markers are untouched.
   - Only `pragafilm.popis.ai` changed. `notes` and the other fields are
     unchanged.
7. **Timeline colour (#43)**
   - After *Apply draft* and sync, the bands move from blue to orange without
     a reload, and the human bands stay grey.
8. **Prompt migration**
   - In Prompt Studio, give the production prompt a new version whose
     `summary_cz` targets `pragafilm.popis.ai` with mode `replace`, and
     promote it.
   - Before you do this, a draft from the old version shows its summary as a
     *suggestion*, and nothing is written to `popis.materialu`.
9. **Offline (FS provider)**
   - With the filesystem provider, publish, then click *Make live*.
   - There is no `unknown ChangeOp` error. Non-AI sidecar markers are kept
     unchanged.
