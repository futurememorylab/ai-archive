# 0043. Gemini Live API key browser exposure — accepted risk

**Date:** 2026-05-30
**Status:** Superseded by [0111](./0111-gemini-live-ephemeral-tokens.md) —
the raw key is no longer shipped to the browser; the 1007 failure below
was a wrong endpoint+version+param triad, not a Google limitation.

## Context

`backend/app/services/live_sessions.py::mint_ephemeral_token` returns
the raw `settings.gemini_api_key` to the browser, where it is used to
authenticate the WSS handshake to Gemini Live.

The intended design was real ephemeral-token auth via
`https://generativelanguage.googleapis.com/v1alpha/auth_tokens`. The
WSS handshake opens with the ephemeral token, but the moment the
client sends its `setup` frame, the server closes the connection with
code 1007 "API key not valid" — verified empirically across multiple
attempts. The most likely cause is a binding mismatch between the
`setup` bound at mint time and the `setup` sent over WSS, but the
issue is reproducible against Google's documented example.

## Alternatives

1. **Keep grinding on the ephemeral-token flow.** Each attempt has
   cost a day or more of head-scratching with no progress. Continuing
   under the project's deadline pressure is not justified.
2. **Proxy WSS through the backend.** Backend opens the WSS connection
   to Gemini using its server-side credential, browser opens a WSS
   connection to the backend, backend forwards audio frames in both
   directions. Significant work; doubles latency-sensitive audio
   bytes through the Python process; non-trivial backpressure
   handling.
3. **Ship the raw key, narrow the threat model, document loudly**
   (chosen). The app is a single-operator local tool used on the
   operator's own laptop behind a project VPN. The browser is the
   operator's; the network is the operator's. Under that model the
   exposure is acceptable — but it is fragile to any model change.

## Decision

- `mint_ephemeral_token` returns `settings.gemini_api_key` directly.
- A boot-time WARNING (in `backend/app/startup.py::
  warn_browser_secret_exposure`) fires whenever the key is configured.
- `README.md` has a "Security caveats" section naming the exposure
  and the threat model.
- Any deployment outside the single-operator-on-VPN model triggers a
  redesign — proxying via the backend (Alternative 2) is the most
  likely path.

## Consequences

- **Positive:** Live audio works today, against today's Gemini Live
  surface, within the documented threat model. The exposure is
  auditable (boot log + README + this ADR) rather than buried in a
  code comment.
- **Negative:** the risk is real and any change to the threat model
  (multi-user, public network, untrusted browser session) means the
  app cannot ship until the auth flow is rebuilt. A future operator
  who doesn't read this ADR could deploy under the wrong model
  without realising.
- **Forward-looking:** if Google fixes the WSS / `setup` binding for
  ephemeral tokens, swap them in. Otherwise Alternative 2 is the
  fallback when constraints change.
