# 0123. Merge the Gemini model catalog and pricing into one Admin tab

**Date:** 2026-06-23
**Status:** Accepted

## Context

The cost-prediction spec (PR1) added an Admin **"Models"** tab to edit the new
per-model `model_config` rate cards, sitting *alongside* the pre-existing
editable-enum tab **"Gemini generation models"** (`gemini_generation_model`,
the catalog of selectable model names stored in `enum_values`). Shipping PR1
produced two tabs that both read as "Gemini models", and the seam between them
was incoherent:

- The catalog had **8** models; the pricing tab listed only the **3** seeded
  rate cards. The other 5 catalog models showed a "no rate card" warning on
  the *enum* tab, but the *pricing* tab gave no way to add one — it only
  listed models already in `model_config`.
- Two facets of one entity ("a Gemini model we support") were split across two
  surfaces with no path between them.

The spec's §1 had treated catalog membership (the enum) and rate configuration
(`model_config`) as separate concerns with separate tabs. In use that was the
wrong decomposition.

## Alternatives

1. **Relabel only** — keep two tabs, rename them so they read as distinct
   ("Gemini models" vs "Model pricing"), move the warning. Minimal work, but
   still two places for one entity and still no way to price the 5 orphans.
2. **`model_config` as the single source of truth** — drop the editable enum
   entirely; make the pricing table own catalog membership too, and point the
   prompt dropdown (`generation_models()`) at `model_config`. Cleanest data
   model (no duplicated identity), but the largest refactor — it touches the
   prompt-creation dropdown, the enum registry, and needs an
   `enum_values → model_config` data migration.
3. **One tab, both layers (chosen)** — a single "Gemini models" tab whose
   spine is the catalog enum, each row joined to its `model_config` rate card.
   Keeps both storage layers (enum owns membership/default/enabled;
   `model_config` owns rates/resolution) behind one coherent UI.

## Decision

Adopt **alternative 3**. The merged tab:

- Lists **every** catalog model (the `gemini_generation_model` enum values),
  joined to its rate card via `cards.get(model)`. A model with no card renders
  a **"no rate card"** pill.
- **Saving rates upserts** — a new `ModelConfigRepo.set_rates` (SQLite
  `INSERT … ON CONFLICT(model) DO UPDATE`) creates the card if absent, updates
  it if present, and revives a soft-deleted one. On update it preserves
  `source_url`/`default_media_resolution` (not in the SET list); on insert
  they default to `''`/`'medium'`. So the 5 orphan catalog models become
  priceable in place.
- Carries the **catalog actions inline** (make-default / enable-disable /
  delete) and an **add-model row**, via thin routes that delegate to
  `enum_service` and re-render the merged partial. **Delete removes the model
  from both** the catalog (`enum_service.remove_value`) and `model_config`
  (`pricing_service.remove_model`).
- The rates route's not-found guard is **catalog** membership (404 if the
  model isn't a catalog value), not `model_config` membership — so unpriced
  catalog models are saveable.
- The generic editable-enum tab for `gemini_generation_model` is **retired**
  from the tab strip (excluded from the data-driven loop). The
  `/admin/enums/gemini_generation_model` route still functions; it is simply
  no longer linked. `_enum_view` drops its special-case `no_rate_card`/
  `rate_cards` logic (that warning now lives in the merged tab).

This deliberately revises spec §1 (which specified a *separate* Models tab).
Alternative 2's single-source data model remains a reasonable future
consolidation, but is deferred to avoid the dropdown/registry/migration churn
inside this change.

## Consequences

- **Positive:** one coherent surface for everything about a Gemini model; the
  8-vs-3 gap is gone (orphan catalog models are priceable in place); no
  feature lost from retiring the enum tab (default/enable/delete/add all moved
  inline). Both storage layers stay single-purpose; no migration needed.
- **Negative / accepted:** model identity is still duplicated across
  `enum_values` and `model_config` (alternative 2 would remove that, at higher
  cost). A model present in `model_config` but absent from the catalog is not
  shown — unreachable through normal use (seeds align; delete clears both),
  only via manual DB tampering. Saving rates for an unpriced model with any
  field left blank returns 422 with no inline message (forcing real values is
  preferred over defaulting to a misleading `0`); a friendlier inline error is
  a follow-up.
- **Tests/docs:** `test_admin_enums.py` now asserts the merged tab replaced
  the generic Gemini enum tab; the walkthrough scenario covers the unified tab
  (incl. the "no rate card" pill); spec §1 + acceptance flow 1 were amended to
  match.
