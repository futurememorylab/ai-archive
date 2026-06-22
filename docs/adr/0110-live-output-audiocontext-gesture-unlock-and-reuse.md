# 0110. Live output AudioContext: unlock in the gesture, reuse across sessions

**Date:** 2026-06-22
**Status:** Accepted

## Context

Gemini's voice was silent in the Live clip assistant on Safari, and — across
browsers — on the *second* time a session was started in the same page load.

The output audio path (`static/liveSession.js`) created its playback
`AudioContext` (`_audioCtxOut`) **lazily inside the WebSocket `onmessage`
callback** that receives Gemini's first audio chunk (`_enqueueAudio`), then
called `resume()` there. On teardown it `close()`d the context and nulled the
reference, so each session re-created one — again from a WS callback.

WebKit/Safari only lets an `AudioContext` leave the `suspended` state when it is
created or resumed **inside a user gesture**. A `resume()` issued from a WS
callback — seconds after the Live-button click, with no active gesture — is
ignored, so the scheduled `BufferSource`s play into a suspended context and
produce no sound. Chrome's "sticky activation" masks this on the first session
(any prior interaction lets a later `resume()` succeed), but by the second
session the original gesture is stale and the freshly re-created, never-resumed
context stays suspended there too. A `close()`d context can never be reused,
which guaranteed the lazy re-create on every subsequent session.

The input/mic context was unaffected: `getUserMedia` carries its own
activation, so capture (and therefore input transcription) always worked — which
is why the bug looked like "output only".

## Alternatives

1. **Keep lazy creation, just call `resume()` harder / on a timer.** Doesn't
   help: the problem is the *absence of a gesture*, not the number of resume
   calls. Rejected.
2. **Re-create the context each session but do it inside `start()`.** Fixes
   Safari, but still churns AudioContexts (WebKit caps how many a page may hold)
   and discards an already-unlocked object for no benefit.
3. **Unlock in the gesture AND reuse across sessions (chosen).** Create + resume
   + prime the output context synchronously at the top of `start()` (before the
   first `await`, while the click gesture is live), and on teardown `suspend()`
   it instead of `close()`, keeping the reference. The next `start()` resumes the
   same, already-unlocked context.

## Decision

- `start()` calls `_ensureOutputAudio()` **synchronously as its first action**,
  inside the Live-button gesture and before any `await`. The method creates the
  24 kHz output `AudioContext` if absent, `resume()`s it, and primes it with a
  one-sample silent buffer (the classic WebKit unlock).
- `_teardown()` **suspends** the output context and keeps the reference; it never
  `close()`s it. `_flushPlayback()` still resets `_nextPlayAt = 0`, so the reused
  context schedules the next session's first chunk at `currentTime`.
- `_enqueueAudio()` keeps a defensive `_ensureOutputAudio()` call for the case
  where the context auto-suspended between sessions.

## Consequences

- Gemini's voice plays on the first AND subsequent sessions, on Safari and
  Chrome.
- One long-lived output `AudioContext` per page instead of one-per-session —
  fewer contexts, no `close()`/re-create churn, and it stays unlocked.
- The gesture-timing constraint is now load-bearing: `_ensureOutputAudio()` must
  remain the first statement in `start()`, before any `await`. A comment in the
  code marks this; moving it after an `await` reintroduces the Safari silence.
- No automated coverage — browser autoplay/gesture behaviour isn't exercisable
  in the Python test suite. Verification is the manual flow: start a session and
  hear the voice, stop, start again and still hear it (see PR notes).
