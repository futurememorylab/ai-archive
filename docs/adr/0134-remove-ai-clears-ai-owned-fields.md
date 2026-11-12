# ADR 0134 — Removing a clip's AI annotations clears the AI-owned fields

Date: 2026-11-12 · Status: accepted · Lifespan: Invariant (9b) ·
Recorded after the fact for PR #161 (merged 2026-10-23) · Refines 0127 ·
Spec: `docs/specs/2026-10-23-remove-ai-annotations-design.md`

## Context

ADR 0127 limited write-back to what the app owns (AI-category markers and
`AI_OWNED_FIELDS`, today `pragafilm.popis.ai`), and invariant 9b ended with
"a field is never cleared". PR #161 added **Remove AI annotations** to the
clip's ⋯ menu. `PublishService.remove_ai` enqueues `ReplaceAIMarkers(())`
plus `SetField(value="")` and `ReplaceNote(text="")` for every target in
`AI_OWNED_FIELDS`, so it does clear a field. That contradicts the invariant
as worded; no ADR recorded the change. This records it (SDLC audit,
2026-11-12).

## Decision

- Clearing is allowed for AI-owned fields only, and only through the
  explicit Remove AI action. A non-AI-owned field is still never written,
  cleared or overwritten; the payload builder still raises
  `FatalProviderError` for any target outside `AI_OWNED_FIELDS`.
- The removal is a clip version (`origin='removed'`) on the normal write
  queue, so history keeps it, it syncs and retries like any publish, and the
  next publish starts from it. The unpublished draft is discarded in the
  same transaction.
- Both shapes the summary may have been written with are cleared (a field,
  or a note in replace mode), because on CatDV both land in
  `fields[target]`.

Invariant 9b changes from "a field is never cleared" to: **a non-AI-owned
field is never cleared; removing AI annotations clears only AI-owned
fields.**

## Consequences

- Any future op that empties a value must target `AI_OWNED_FIELDS` and come
  from an explicit user action; the ownership guard still blocks the rest.
- Human edits typed into `pragafilm.popis.ai` in CatDV are lost on Remove
  AI. That field is the app's by definition (0127); the confirm dialog says
  what will be removed.

## Alternatives considered

1. **Leave the summary in place and remove only markers.** Leaves AI text
   in the archive after the user asked to remove it. Rejected.
2. **Write a placeholder ("removed") instead of an empty value.** Puts app
   text where people expect nothing. Rejected.
