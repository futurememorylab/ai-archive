# 0121. Seed rate cards for the full Gemini catalog at Global rates; guard catalog↔card coverage

**Date:** 2026-06-23
**Status:** Accepted

## Context

The `gemini_generation_model` enum offered 8 selectable models, but
`SEED_RATE_CARDS` (`services/pricing.py`) only priced the 3 2.5-series models.
The five 3.x/3.5 models were added to the dropdown later without rate cards, so
every estimate/cost surface for them showed "no rate card" (after ADR 0119 made
that graceful) — i.e. they were selectable but un-priced. The user asked for
cards for all models, which requires *real* per-1M pricing (wrong numbers
silently corrupt every estimate and budget — the exact failure ADR 0119
guarded against).

Two questions: where do the prices come from, and at which region/tier.

## Alternatives

- **Guess / map from nearest tier.** Rejected — fabricated billing data.
- **Leave them un-priced** and rely on the graceful "no rate card" UX.
  Rejected — the models are real and in active use; an accurate card is better
  than a permanent warning.
- **Source from official pricing, ask the region/tier.** Chosen.

**Region/tier — Global vs europe-west3 non-global (+10%).** Considered seeding
the europe-west3 "non-global" rates (+10%). Rejected for now: (a) the existing
three 2.5 cards use Global standard rates, so non-global would make the catalog
internally inconsistent; (b) the +10% non-global surcharge for GA Gemini-3
models only takes effect 2026-07-01 — Global pricing is the *currently* correct
rate; (c) research found the Gemini 3.x family isn't even offered as a pinned
europe-west3 single-region endpoint yet (only 2.5-pro/2.0-flash are) — EU access
is via the multi-region endpoint. The admin can bump any card +10% in the UI if
they later pin a regional endpoint.

## Decision

Add five Global-standard rate cards to `SEED_RATE_CARDS`, each tagged
`source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing"` (the
spec §5 provenance field), verified against the official Vertex pricing page and
cross-checked by multiple secondary sources:

| model | input (txt/img/vid) | audio | cached | output |
|---|---|---|---|---|
| gemini-3-flash-preview | 0.50 | 1.00 | 0.05 | 3.00 |
| gemini-3.1-pro-preview | 2.00 | 2.00 | 0.20 | 12.00 |
| gemini-3.1-flash-lite | 0.25 | 0.50 | 0.025 | 1.50 |
| gemini-3.1-flash-lite-preview | 0.25 | 0.50 | 0.025 | 1.50 |
| gemini-3.5-flash | 1.50 | 1.50 | 0.15 | 9.00 |

`gemini-3.1-pro-preview` is tiered (×2 above 200K tokens); like the existing
2.5-pro card, the single-rate card uses the ≤200K base (the app's clips are far
under 200K). The seeds reconcile into `model_config` at boot with
`default_media_resolution="medium"` (unchanged `reconcile_seeds`).

**Coverage guard.** New `tests/unit/test_rate_card_coverage.py` asserts every
`gemini_generation_model` enum value has a `SEED_RATE_CARDS` entry — so a future
catalog addition without a card fails CI rather than silently shipping an
un-priced model. Tests that relied on a *real* catalog model being un-priced
were repointed at synthetic `gemini-*-unpriced` ids (the un-priced UX path still
matters and stays covered).

## Consequences

- **Positive:** all selectable models are priced with authoritative, sourced
  numbers; estimates/budgets are correct for the 3.x/3.5 models; the coverage
  guard prevents regression. Editable in the admin UI as usual (DB overrides the
  seed; `pricing_version` bumps on edit).
- **Accepted / follow-ups:** `gemini-3.1-flash-lite-preview` is a deprecated
  model ID (shut down mid-2026), carded only because it's still in the dropdown —
  a future catalog cleanup should drop it. The +10% europe-west3 non-global
  surcharge (from 2026-07-01) is *not* applied; revisit if a regional endpoint is
  pinned. Tiered >200K pricing for 3.1-pro isn't expressible in the flat card
  (same known limitation as 2.5-pro).
- **Adjacent (same change set):** the Admin Prompts tab gained usage columns —
  total footage annotated (`Σ media_duration_secs` + run count) and estimated vs
  actual cost (`Σ est_cost_usd_p50` vs `Σ cost_usd`) per prompt version, via one
  batched `totals_by_prompt_version` query (N+1-guarded) — turning the prompt
  list into a usage dashboard.
