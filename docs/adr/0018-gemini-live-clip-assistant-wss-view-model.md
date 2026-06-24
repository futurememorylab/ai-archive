# 0018. Gemini Live clip assistant: browser-direct WSS, separate view-model

- **Date:** 2026-05-23
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

Adding a Czech voice assistant to the clip-detail page
(spec `docs/specs/2026-05-23-gemini-live-clip-assistant-design.md`;
plan `docs/plans/2026-05-23-gemini-live-clip-assistant.md`). Two
calls during implementation deserve their own note: (1) the audio
path is browser→Google→browser direct, not through our backend;
(2) the live context-text builder uses a purpose-built view-model
shape, not the existing `clip_detail()` / `_build_draft_for_clip`
view-models passed to `clip_detail.html`.

**Alternatives & choices.**

1. *Audio routing.* The plan's spec §3.2 documents a prior PoC that
   bridged Live audio through FastAPI; that path added enough latency
   and re-encoding that Gemini misunderstood the Czech speech. The
   options reduced to (a) keep the bridge and tune the codec, or
   (b) mint a single-use ephemeral token server-side via
   `authTokens.create` and have the browser open the WSS directly.
   We chose (b). Backend's role is now exactly three HTTP calls
   (`POST authTokens.create`, `POST sessions/{id}/transcript`,
   `POST sessions/{id}/summarize` via non-Live `generateContent`)
   and no socket. The browser owns the WSS lifecycle, mic capture,
   playback, frame extraction, and the inactivity timer. The result:
   the FastAPI process never sees a PCM byte.

2. *View-model shape for `build_context_text`.* The plan's tests for
   `services/live_context.py` assume `fields` is a `dict[ident, value]`
   and markers carry `in_smpte` / `out_smpte`. The existing clip
   view-model (`ui/view_models.clip_detail`) returns `fields` as a
   list of `{identifier, name, value}` dicts and markers without
   smpte (only `in_secs`/`out_secs`). Options: (a) bend the builder
   to accept the existing list+secs shape, (b) keep the builder's
   simple shape and convert at the loader boundary. We chose (b):
   `routes/pages.py` exposes `_build_clip_view_model_for_live` and
   `_build_draft_view_model_for_live` that produce dict-shaped
   fields and smpte-stamped markers, which `routes/live.py` calls
   via `load_clip_for_live` / `load_draft_for_live`. This keeps the
   pure Czech-text builder readable (no defensive type-sniffing) and
   localises the format choice to one helper per side.

## Consequences

For (1): the spec's "audio never traverses backend" is the
single hardest-locked decision in the design — losing it would
re-introduce the PoC's latency failure. The route surface is
deliberately tiny so it's hard to accidentally drift toward a
WebSocket route on our side; the only Gemini HTTP calls inside
the process are the token mint and the post-session summarise.

For (2): the existing clip view-model is shaped for HTML
rendering (pre-stringified field values, marker objects geared to
the timeline). The Live context block is a free-text Czech blob
the model reads — it wants raw values to format itself
(`pragafilm.rok.natočení: 1928, 1929` not `pragafilm.rok.natočení: 1928, 1929`
re-fixed-up by a string view), and human-readable smpte timestamps
for the markers because the model never reads `in_secs=12.5`. Trying
to share one VM would have meant the live builder doing inverse
operations (parsing the existing string values back into lists, then
re-formatting; converting `_secs` back to smpte). Two narrow
purpose-built helpers cost less than that.
