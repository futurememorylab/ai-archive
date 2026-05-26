# 0027. Image annotation prompt + prompt media_kind

**Date:** 2026-05-26
**Status:** Accepted

## Context

After image clips became viewable/annotatable (ADR 0026), running the
default scene-marker prompt on a still produced nonsensical timestamped
"scenes". Stills need a prompt with no scenes/timecodes, while keeping the
Czech summary + era metadata — and the output had to be stored and indexed
exactly like video output.

## Decision

Tag each prompt with `media_kind` (`video` / `image` / `any`, migration
0011; existing prompts backfilled to `video`). Seed a dedicated image
prompt that is the video prompt's schema/target_map **minus `scenes`**,
reusing the same `summary_cz`/`decade`/`years` keys and the same CatDV
targets (`pragafilm.popis.materialu`, `pragafilm.dekáda.natočení`,
`pragafilm.rok.natočení`). Filter the Annotate dropdown by the clip's kind
(`media_kind == clip.kind or "any"`). Surface and edit `media_kind` in the
prompts UI (create selector, detail-page control via `PATCH
/api/prompts/{id}`, and a kind badge).

## Alternatives

- **Strip `scenes` from the single prompt at runtime** — rejected: mutating
  user-authored schema/target_map is brittle and leaves contradictory
  scene instructions in the prompt body; gives operators no way to tune
  image wording.
- **Manual prompt choice only** — rejected: easy to run the scene prompt on
  a still by mistake.

## Consequences

- Image and video annotations are stored and indexed identically (same
  `annotations` row, same `annotations_fts` trigger, same `review_items`
  note/field kinds) — the only difference is the absence of markers. This
  is guaranteed by the seed content reusing identical targets, not by code.
- `media_kind` lives on `prompts` (stable across versions), edited at the
  prompt level rather than per version.
- Operators can author custom image-only / video-only / any prompts.
