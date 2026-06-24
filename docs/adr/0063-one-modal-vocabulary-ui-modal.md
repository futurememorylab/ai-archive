# 0063. One modal vocabulary: ui.modal shell + canonical .modal-* classes

**Date:** 2026-06-09
**Status:** Accepted
**Lifespan:** Invariant

Implements "Candidate B" — a non-goal of the menu-consolidation spec
(`docs/specs/2026-06-09-popover-menu-module-and-design-language-guard-design.md`),
done as a stacked follow-up. Cross-ref ADR 0062.

## Context

Two modal vocabularies had drifted: `modal-overlay` / `modal-dialog` /
`modal-field` / `modal-label` (the duplicate-prompt dialog) vs `modal` /
`modal-backdrop` / `modal-card` / `modal-hdr` / `modal-actions` (bulk-annotate,
new-batch, archive picker), plus `modal-foot` (archive picker) and
inline-styled `modal-h` titles. Each re-wrote escape / backdrop-close. The
design-language guard (ADR 0062) already *froze* all `modal-*` classes; this
collapses them.

## Alternatives

- **Fully declarative** `ui.modal(body=…, actions=…)`: rejected — modal bodies
  vary wildly (a form, a multi-pane clip picker) and Jinja has a single `call`
  slot; same reason the menu macro stayed chrome-only.
- **Keep two vocabularies, dedupe CSS only**: rejected — leaves the divergence
  and the per-modal escape/backdrop wiring in place.

## Decision

One canonical vocabulary: `.modal` (overlay) + `.modal-backdrop` +
`.modal-card` (`.sm` = narrow form, `.nb-card` = wide picker) +
`.modal-hdr` / `.modal-title` + `.modal-body` + `.modal-actions`. A
`ui.modal(state, label='', card_cls='')` macro owns the overlay + backdrop +
escape; the caller supplies the body + actions through the single call slot,
and a custom `.modal-hdr` when the header needs more than a title (e.g. the
new-batch selected-count). Form fields inside modals use the existing
`.field` / `ui.field` system — `modal-field` / `modal-label` are retired. The
HTMX-injected archive picker (present-or-absent in `#modal-root`, no show-flag)
uses the `.modal-*` classes directly rather than the macro.

The guard's modal list flips from a frozen burn-down (`MODAL_GRANDFATHERED`) to
an enforced canonical set (`MODAL_ALLOWED`): a new `modal-*` outside it now
fails CI.

## Consequences

- Escape-to-close is uniform now (the bulk-annotate modal gains it).
- One place owns overlay / backdrop / `role=dialog`; the redundant
  `modal-overlay` / `modal-dialog` / `modal-foot` / `modal-h` / `modal-field` /
  `modal-label` classes are deleted, along with two inline-styled titles.
- Modals join menus as an *enforced* (not merely frozen) vocabulary. The clip
  media card (Candidate D) remains the last frozen-but-unconsolidated axis.
