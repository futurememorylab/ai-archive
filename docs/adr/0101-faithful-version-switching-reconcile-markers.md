# 0101. Faithful version switching via ReconcileMarkers (+ History UI disambiguation)

**Date:** 2026-06-17
**Status:** Accepted — refines [0100](./0100-publishing-audit-drop-provenance-and-reactivate-switching.md)

## Context

ADR 0100 (A3) re-based version switching onto `PublishService.reactivate`: "Make
live" re-PUTs an existing version's snapshot and marks it live, instead of
forking a new version. Operator QA afterwards found the re-PUT still does not
actually switch the clip, plus a clip-detail header defect. All reproduced
against live code (`_ops_from_snapshot` → `build_put_payload`):

- **B1 — switching leaves later-version markers.** `reactivate` re-asserted the
  snapshot with `AddMarkers`, which is additive; `build_put_payload` preserves
  existing markers it didn't touch. Switching a clip that holds v3's markers
  (A,B,C,D) back to v1 (A,B) wrote `A,B,C,D` — C/D from later versions survived.
  The headline read "Live v1" while the clip was still the v3 superset.
- **B2 — duplication / shift on non-25fps clips.** `reactivate` baked
  `DEFAULT_FPS = 25.0` into every marker and `_timecode_to_catdv` derives the
  frame as `round(secs * tc.fps)`. The dedup key is the integer frame `in.frm`.
  On a 30fps clip whose markers live at `frm = secs*30`, re-asserting at 25fps
  produced new frames that never matched, so markers duplicated at shifted
  frames (`A@100,A@120,B@200,B@240`).
- **B3 — header UI.** The version-history dropdown was clipped at the right edge
  of the annotation column (`.anno-scope-row` was a non-wrapping flex row whose
  tabs + the wide `DRAFT – UNPUBLISHED` pill + the dropdown overflowed), and two
  controls were both labelled "History" — the version dropdown and the
  live-AI-session panel tab.

A constraint shaped the fix: our version snapshots only ever record markers the
**app** authored (`_load_live_snapshot` starts from an empty base;
`clip_versions_backfill` builds from our synced review_items). They never capture
pre-existing or human-added CatDV markers. So a faithful switch cannot be a
wholesale array replace — that would silently delete an editor's manual markers.

## Alternatives

- **Wholesale replace** (clip markers := the version's set). Rejected — destroys
  markers we never authored (pre-existing / human-added directly in CatDV).
- **Read the live clip at enqueue time and diff there.** Rejected — spends a
  scarce CatDV seat on every switch and breaks offline; the merge belongs at
  drain time where the live clip is already read.
- **Extend `AddMarkers` with a `drop`/`replace` flag.** Rejected — risks the
  publish-forward path that shares the op; a distinct op isolates switch
  semantics.
- **Full faithful revert (also clear fields/notes later versions added).**
  Deferred — needs CatDV field-clear semantics (empty vs merge) we could not
  safely verify before the demo. Notes/fields stay overwrite-only and are never
  cleared, so a foreign value is never destroyed.

## Decision

- **New writeback op `ReconcileMarkers(desired, drop_secs)`.** Handled in
  `build_put_payload` where the live clip and its real fps are known. It DROPS
  the markers we authored in other versions (`drop_secs`, matched to clip frames
  at the clip's real fps), RE-ASSERTS the target version's markers (`desired`),
  and PRESERVES every marker we never authored. Because CatDV replaces the
  markers array wholesale on PUT, the computed list is the final set. (B1 fixed.)
- **Roll back only our markers.** `reactivate` computes `drop_secs` = (union of
  marker in-seconds across **all** the clip's versions) − (the target's
  in-seconds), so only our own later/other additions are removed.
- **fps from the live clip, never hardcoded.** `desired` markers carry a
  `Timecode` fps sentinel of `0.0`; `_timecode_to_catdv`'s existing
  `tc.fps if tc.fps > 0 else default_fps` fallback then derives every frame from
  the clip's real fps at drain time. `drop_secs` are matched at the same fps.
  (B2 fixed.)
- **UI.** `.anno-scope-row` gains `flex-wrap: wrap` so the version controls wrap
  instead of clipping (B3a). The two "History" controls are renamed: the
  publish-snapshot dropdown → **"Versions"**, the live-AI-session tab → **"Live
  sessions"** (B3b).

## Consequences

- Switching to an older version now reproduces that version's marker set exactly
  while keeping human/pre-existing markers, on clips of any fps. The headline
  "Live vN" tells the truth.
- The op vocabulary grows by one (`model.py`, `change_set_json.py`, `payload.py`,
  `sync_drawer.html` label "Switch version"). `AddMarkers` (publish-forward) is
  untouched; its tests stay green.
- **Not auto-repaired:** clips already corrupted by the old fps bug (duplicate
  markers at 25fps frames on a non-25fps clip) are left as-is — a repair pass is
  a separate task. Fields a later version newly introduced may still linger
  after a switch (out of scope here).
- Tests: `ReconcileMarkers` JSON round-trip; payload reconcile (drop ours, keep
  foreign, overwrite at shared frame, 30fps no-duplicate); `reactivate` emits the
  op with the right `desired`/`drop_secs` and no new version; an offline
  end-to-end fidelity test proving a human marker survives. Full suite green
  (1715 passed). Validated live against CatDV on a throwaway clip.
