# ADR 0130 — Gemini Live moves to v1beta, gemini-3.8-live and session resumption

Date: 2026-10-18 · Status: accepted · Lifespan: Invariant ·
Amends: [0112](./0112-gemini-live-ephemeral-tokens.md) ·
Spec: `docs/specs/2026-10-18-live-mode-revival-design.md`

## Context

Live mode stopped working by October 2026. Google's current Live docs
(checked 2026-10-18) differ from what ADR 0112 built against:

- Ephemeral tokens are documented as **v1beta only**: mint with
  `POST v1beta/auth_tokens` (key in the `x-goog-api-key` header) and connect
  to `v1beta…BidiGenerateContentConstrained?access_token=`. 0112 used v1alpha
  for both.
- `gemini-3.1-flash-live-preview` is legacy and shuts down on 2026-11-17;
  the replacement is `gemini-3.8-live`, a native-audio model that rejects an
  explicit `speechConfig.languageCode` (Czech is supported; the system prompt
  sets the language).
- Without `contextWindowCompression` an audio+video session ends after
  2 minutes (15 for audio only), and every connection ends after about 10
  minutes (`goAway` first). Ours sends video frames, so sessions died at 2
  minutes.

We could not test against Google from the agent container, so this is
built from the docs and checked offline (walkthrough with a routed fake
socket); confirm on staging.

## Decision

- Mint and connect on **v1beta**; the token stays config-bound and
  single-use (0112 otherwise stands).
- Default `GEMINI_LIVE_MODEL=gemini-3.8-live`. `GEMINI_LIVE_LANGUAGE_CODE`
  (unset by default) adds `speechConfig.languageCode` for the legacy 3.1
  model, the fallback until its shutdown.
- The setup always carries `contextWindowCompression: {slidingWindow: {}}`
  and `sessionResumption: {}`. The browser keeps the latest
  `sessionResumptionUpdate.newHandle` and, on `goAway` or an unexpected
  close, reopens the socket with the same token and
  `sessionResumption.handle` (at most 5 times per session). Sending is gated
  on `setupComplete` again for each new connection.
- Typed questions use the same Live session (`clientContent`, one complete
  user turn), not a separate text API. When the mic is missing or blocked the
  session starts in typing mode instead of failing.

## Alternatives

1. **Typed input through `generateContent` on the server.** Cheaper text
   replies and no socket, but a second code path and a second history to
   persist and summarise. Rejected: spoken replies cost about $0.02 per
   minute, and one session lets people switch between talking and typing.
2. **Keep 3.1 and v1alpha.** Rejected: the model shuts down on 2026-11-17,
   and v1alpha no longer matches Google's docs.

## Consequences

- Replies still come back as audio plus the written transcript, also in
  typing mode.
- Open risk: users report `gemini-3.8-live` sometimes sends silent audio
  while the transcript is complete. If that shows up, set
  `GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview` and
  `GEMINI_LIVE_LANGUAGE_CODE=cs-CZ` until 2026-11-17.
- If Google ignores the resumption handle because the token locks the
  setup, a reconnect starts a fresh model context; the on-screen dialog and
  saved transcript are unaffected.
