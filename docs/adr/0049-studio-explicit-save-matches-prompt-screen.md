# 0049. Studio prompt editing uses explicit save (matches the prompt screen)

**Date:** 2026-06-02
**Status:** Accepted

## Context

Two surfaces let you edit a prompt's draft body: the **prompt screen**
(`_prompt_detail.html` + `promptEditor.js`) and the **Prompt Studio** card
(`_studio_prompt_card.html` + `studio.js::studioPromptCard`). They had
diverged into two different editing models:

- Prompt screen: explicit `✓ Save changes` button shown only when the draft
  diverges from its baseline; PUT on click; re-baseline + success toast. The
  editable textarea takes the accent (`--accent`, `#f5a623`) border on focus.
- Studio: **debounced auto-save** (`@input.debounce.700ms="save()"`), a
  `dirty` flag that only meant "save in flight", and a footer that flipped
  `saving…` / `saved`. No visible "this is editable" affordance on the editor.

Auto-save silently writes a new draft body on every keystroke pause, which is
surprising next to the prompt screen's explicit model and gives the user no
clear "you have unsaved edits" / "click to save" moment. We want one editing
behaviour across both surfaces.

## Alternatives

1. Make the prompt screen auto-save too (converge on auto-save).
2. Make Studio explicit (converge on the prompt screen's model).
3. Leave them divergent.

## Decision

Converge on the **prompt screen's explicit-save model** (alternative 2).
In the Studio card, for a draft on the `cur` side:

- The editable fields are the **body and the model**. `hasChanges` is a getter
  over both: `editorBody !== baseline || model !== modelBaseline`. The textarea
  seeds `baseline`/`editorBody` on init and updates `editorBody` on `@input`.
  No debounce, no auto-save.
- The **model picker** moves into the card's own scope (no nested
  `x-data="modelPicker"`). It is gated by `canEdit` (`side==='cur' && draft`):
  `:disabled="!canEdit"` on the toggle, and `pickModel(m)` is a no-op unless
  `canEdit`. Picking a model flips `hasChanges` (via the getter) and is
  persisted by `save()` (`model: this.model`). This mirrors the prompt screen,
  where the model lives in the editable `draft` and is `:disabled="!canEdit"`.
- A `✓ Save changes` button (`btn sm primary`) sits in the card **header**
  next to the model picker, `x-show="hasChanges"`. `save()` guards on
  `hasChanges`/`saving`, round-trips `target_map`/`output_schema` unchanged
  (the Studio pane only edits body + model), PUTs, then re-baselines body +
  model (`hasChanges` → false) and pushes a `Changes saved.` success toast —
  exactly as `promptEditor.save()` does.
- The editable `.pc-editor` gets a **persistent** `var(--accent)` border. The
  template only renders `.pc-editor` for an editable draft (production/archived
  versions render a read-only `.pc-readonly` `<pre>`), so an always-on accent
  border is an unambiguous "this is editable" signal — the chosen variant over
  focus-only or only-when-dirty, for the strongest affordance.

The compare (`cmp`) card never renders a Save button, never gates a picker as
editable, and never saves.

Because the model now lives on the card, the **run model** (`store.activeModel`)
is re-seeded from the card's version in `init()` — on page load and on every
HTMX version swap (the swapped card re-inits). Previously `htmx:afterSwap`
updated `activeVersionId` but not `activeModel`, so the run model went stale
after a version switch; routing it through the card's `init()` fixes that. A
`pickModel()` selection also writes `activeModel`, so "Run" uses the model
currently shown in the picker.

## Consequences

- One mental model for editing prompts; the prompt screen and Studio behave
  identically (explicit save, accent highlight on the editable field).
- Edits are no longer persisted on a keystroke timer — the user must click
  Save. This is the intended trade: predictability over silent writes.
- The `dirty`-means-"saving" flag is gone; `hasChanges` (unsaved edits) and
  `saving` (PUT in flight) are now distinct and drive both the button and the
  three-state footer (`saving…` / `draft · unsaved changes` / `saved`).
- Markup is pinned by `tests/integration/test_studio_prompt_card_route.py`
  (Save button present for draft/cur, absent for production and for the cmp
  card; no `debounce`; picker is `:disabled="!canEdit"` and routes through
  `pickModel(`; the card x-data seeds the version's model + state).
- The old `modelPicker` Alpine.data component (a store-proxy that decoupled the
  picker from the version's saved model) is deleted — it had one caller.
- **Known follow-up:** the Studio picker's model list is a hardcoded 4-entry
  Jinja `{% set %}` that has drifted from `promptEditor.js`'s canonical
  `MODELS` (it omits the gemini-3.x models and lists a `gemini-2.0-pro` the
  prompt screen doesn't). A version whose model isn't in the list renders no
  `is-current` highlight; now that the model is editable+saved, the two lists
  should be unified (single source). Not done here to keep this change to
  behavior parity.
- **Compare diff now tracks the saved version.** The cmp-card line-diff
  (`_studio_diff.html` + `studio-diff.js::cmpDiff`) recomputed only via
  `x-init` and an `x-effect` reading `$root.mode`/`$root.activeVersionId`/…
  But inside `cmpDiff`'s own `x-data`, `$root` is the diff div, so those were
  `undefined` element-property reads with **no reactive dependency** — the
  effect never re-fired, so saving a draft (or switching the cur version) left
  the diff showing pre-save text. Fixes: (a) read the deps off `$store.studio`
  (reactive), (b) `save()` bumps a new `store.savedTick` the effect watches,
  and (c) `readText` reads the editable card's saved `baseline` (via
  `Alpine.$data(card)`) instead of the live edit buffer, so the diff reflects
  the saved version, not unsaved keystrokes. Pinned by
  `tests/integration/test_studio_compare.py::test_cmp_diff_reacts_to_store_signals_including_save`.
- Cross-ref ADR 0048 (shared studio state lives in `Alpine.store`, not
  `_x_dataStack`); this change stays within `studioPromptCard`'s own x-data.
  `Alpine.$data(el)` (public API) is used to read the cur card's saved
  `baseline` from the diff — distinct from the forbidden `_x_dataStack`
  internal.
