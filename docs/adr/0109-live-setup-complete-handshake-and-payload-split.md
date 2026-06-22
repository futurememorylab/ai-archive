# 0109. Gemini Live: respect the setupComplete handshake; split the initial turn out of the setup frame

**Date:** 2026-06-22
**Status:** Accepted

## Context

The browser-direct Gemini Live assistant (`liveSession.js`, ADR 0043 threat
model) was unstable — "sometimes works, sometimes not." The root cause was a
race in the WSS bring-up. On `ws.onopen` the client sent the `setup` frame and
then, **in the same tick**, sent the initial Czech context turn, flipped
`state = "active"`, and let the already-running mic worklet stream
`realtimeInput.audio`. The Live API requires the client to wait for the
server's `setupComplete` message before sending any `clientContent` or
`realtimeInput`; `_onWsMessage` never handled `setupComplete` at all. Whether a
session worked depended purely on whether `setupComplete` happened to arrive
before the first audio chunk — otherwise Gemini closed the socket (1007/1008)
or silently dropped the early frames.

Two adjacent defects compounded the "feels broken" perception: barge-in
(`serverContent.interrupted`) was ignored, so queued model audio kept playing
over the operator; and the output `AudioContext` was created lazily inside a
WS callback (not a user gesture), so under the autoplay policy it could start
suspended and produce no sound.

Separately, the `/session-config` response carried the initial context turn
**inside** `setup_payload` (key `initial_context_turn`), forcing the frontend
to shallow-copy and `delete` it before sending — and risking it leaking into a
bound setup if we ever switch from the raw key to ephemeral tokens. The
response also duplicated the API key as a bare `token` (already in `ws_url`)
and returned an `inactivity_s` the frontend never reads (it uses the
server-rendered template arg).

## Alternatives

- **Add a fixed delay before sending content instead of gating on
  `setupComplete`.** Rejected — still a race, just a longer one; the protocol
  gives us an explicit ACK to key off.
- **Recreate the output AudioContext on each barge-in to stop sound.**
  Rejected — heavy, and re-running into the suspended-context problem. Tracking
  the scheduled `BufferSource` nodes and `.stop()`-ing them is precise and cheap.
- **Leave the payload shape and just stop the frontend delete-dance.** Rejected
  — the smuggled field is the actual hazard; making the API return a pure
  `setup_payload` removes it at the source.

## Decision

1. **Gate on `setupComplete`.** `onopen` sends only `{ setup }`. A new
   `_onSetupComplete()` (routed from `_onWsMessage`) is the sole trigger for
   sending the initial turn, flipping to `active`, and starting timers. Mic
   chunks in `_onCaptureChunk` are gated on a `_setupComplete` flag in addition
   to socket readiness.
2. **Handle barge-in.** `serverContent.interrupted` flushes queued playback via
   `_flushPlayback()`, which `.stop()`s tracked `BufferSource` nodes and resets
   the play cursor. Teardown also flushes.
3. **Resume the output context** when it is `suspended`.
4. **Split the payload.** `/session-config` returns `initial_context_turn` as a
   separate top-level field; `setup_payload` is the pure
   `BidiGenerateContentSetup`. Dropped the redundant `token` and the unused
   `inactivity_s` from the response. Dropped the always-zero `search_calls` from
   the persisted transcript body (DB column kept for back-compat).

## Consequences

- Sessions start deterministically; the early-frame race is gone.
- The model stops talking when interrupted; audio plays even when the context
  would otherwise start suspended.
- `assemble_setup_payload` is unchanged (still returns the combined dict, its
  unit tests still hold); the route does the split, so the browser only ever
  sees a pure setup frame.
- Guard tests: `tests/unit/test_live_session_js.py` pins the handshake,
  capture-gating, barge-in, and the no-delete-dance invariants (source-scan,
  same pattern as `test_studio_uploads_js.py`); `test_routes_live.py` pins the
  new response shape. Full behavioural verification still requires a live mic +
  WSS (cannot run headless).
