# 0112. Gemini Live: ephemeral tokens replace the browser-exposed key

**Date:** 2026-06-22
**Status:** Accepted — supersedes [0043](./0043-gemini-live-api-key-exposure-accepted-risk.md)

## Context

ADR 0043 shipped the raw `GEMINI_API_KEY` to the browser: the backend put
it in the WSS URL (`?key=`) and the browser opened a direct WebSocket to
Gemini Live with it. 0043 accepted this because an ephemeral-token attempt
(`authTokens.create`) had failed — the WSS handshake closed with code
**1007 "API key not valid"** the instant the client sent its `setup`
frame — and the single-operator-on-VPN threat model made the exposure
tolerable under deadline.

The exposure was the blocking prerequisite for several other decisions
(see ADR 0084) and meant the key was readable by anyone with the
operator's browser dev-tools.

Re-investigating against current Google docs and an empirical spike, the
1007 failure was **not** a Google limitation. Ephemeral tokens require a
different connection triad than the raw-key path, and 0043's attempt got
all three wrong:

| Axis | 0043 attempt (failed) | Correct for ephemeral tokens |
|------|----------------------|------------------------------|
| Query param | `?key=<token>` | `?access_token=<token>` |
| RPC method | `BidiGenerateContent` | `BidiGenerateContent`**`Constrained`** |
| API version | `v1beta` | `v1alpha` |

A token presented via `?key=` is read as an API key and rejected as
invalid — the WS upgrade always succeeds, so the rejection surfaces only
on the first (`setup`) frame, which is exactly the "1007 on setup"
symptom 0043 misread as a binding mismatch.

A spike (`/tmp/live_*_spike.py`, run against the real key) confirmed:
minting a single-use token via `v1alpha auth_tokens` and connecting to
`...ConstrainedQ?access_token=` returns `setupComplete`; reproducing
0043's `v1beta + BidiGenerateContent + ?key=` with that same token
reproduces the exact `1007 "API key not valid"`. A second spike confirmed
a **bound** token (config locked via `bidiGenerateContentSetup`) lets the
browser send a minimal `setup` frame (even `{}`) and silently constrains a
tampering client to the bound model/voice/system-prompt.

## Alternatives

1. **Keep shipping the raw key** (0043 status quo). Rejected: the secure
   path is now proven and small.
2. **Proxy the WSS through the backend.** Maximum control but doubles
   latency-sensitive audio through Python and would route audio through
   Cloud Run, colliding with the known path-MTU issues on that hop.
   Rejected as heavier and worse-fitting.
3. **Ephemeral tokens, config-bound** (chosen). The raw key authenticates
   only the server→Google mint call; the browser gets a short-lived,
   single-use token. Browser stays direct-to-Google (no added latency,
   audio never traverses the backend). Binding the full setup into the
   token additionally keeps the proprietary system prompt + tool
   declarations off the browser.

## Decision

- `live_sessions.mint_ephemeral_token` POSTs to
  `v1alpha/auth_tokens` with `uses=1`, a 30-min `expireTime`, a 2-min
  `newSessionExpireTime`, and `bidiGenerateContentSetup` = the full
  assembled setup. It returns the token `name`. The raw key is only the
  `?key=` auth on *this* call.
- `routes/live.py` `WSS_URL_TEMPLATE` →
  `v1alpha …BidiGenerateContentConstrained?access_token={token}`.
- The route strips `systemInstruction` + `tools` from the setup sent to
  the browser (`_TOKEN_ONLY_SETUP_FIELDS`); the token enforces the full
  config regardless, so the browser sends only non-secret fields.
- `startup.warn_browser_secret_exposure` → `log_live_token_mode`: logs the
  ephemeral-token posture at INFO when the key is set, warns only when the
  key is missing (Live unavailable). `main.py` updated.
- `liveSession.js` masks `access_token=` (and still `key=`) in its console
  log. README "Security caveats" rewritten.

## Consequences

- **Positive:** the raw key never reaches the browser; the system prompt +
  tool declarations don't either; tokens are single-use and short-lived;
  the browser keeps its low-latency direct connection. Removes 0043's
  deployment constraint.
- **Negative:** every session now makes one extra server→Google call to
  mint the token (cheap, mocked in tests). Ephemeral tokens are a
  Gemini-Developer-API feature (not Vertex) — fine, since the Live path
  already uses the Developer API key.
- **Residual / to verify manually:** `setupComplete` is proven, but a full
  audio round-trip (mic in → Czech voice out, transcripts, `end_session`
  tool-call) should be ticked off on a running app (see Manual acceptance).
  Sporadic forum reports of an "unregistered callers" error with
  `access_token` exist; not observed in the spike.

## Manual acceptance flows

1. **Key stays server-side.** With `GEMINI_API_KEY` set, open a clip,
   start a Live session, open dev-tools → Network/Console. Expect the
   `session-config` response's `ws_url` to contain
   `access_token=auth_tokens/…` and **no** raw key; the console "WSS
   opening" line shows `access_token=…` masked. Setup: any clip with a
   cached proxy.
2. **System prompt withheld.** In the same `session-config` response,
   `setup_payload` has `model`/`generationConfig` but **no**
   `systemInstruction` or `tools`.
3. **Audio round-trip works.** Start the session, allow the mic; expect a
   Czech spoken greeting + one-sentence frame description, then ask a
   question and get a spoken Czech answer with live transcript. Confirms
   the bound config (AUDIO modality, voice, system prompt) takes effect
   from the token.
4. **Missing-key warning.** Unset `GEMINI_API_KEY`, boot; expect the
   `log_live_token_mode` WARNING that Live audio is unavailable.
