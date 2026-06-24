# 0016. Gemini Live clip assistant: browser-direct + Developer API

- **Date:** 2026-05-23
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

Add a Czech voice assistant to the clip-detail view that
sees the current frame plus all annotation context and can ground
location / historical questions via Google Search. Spec:
`docs/specs/2026-05-23-gemini-live-clip-assistant-design.md`.

## Alternatives

(a) Backend WSS bridge — browser ↔ FastAPI ↔ Vertex
AI Live — reusing the existing service-account credentials. (b) Vertex
AI Live opened browser-direct, using a 1-hour OAuth bearer minted from
the service account. (c) Gemini Developer API
(`generativelanguage.googleapis.com`) opened browser-direct, using
`authTokens.create` to mint a single-use ephemeral token with
`liveConnectConstraints` baked in.

## Decision

(c). Audio flows browser ↔ Google directly; the FastAPI
process is only used to mint the token, assemble the system instruction
+ clip-context setup payload from the existing prompt-management
system, persist the post-session transcript, and run a non-Live
`generateContent` to produce a Czech summary stored alongside it in a
new `live_sessions` table. The assistant never writes to draft or
published annotations — its output lives in a read-only *History* tab
on the clip page.

## Consequences

Option (a) was tried as a PoC and failed — the extra hop and
re-encoding scrambled the audio enough that Gemini could not understand
Czech speech. The user's instruction was explicit: implement direct
browser communication. Between (b) and (c), the Developer API's
`authTokens.create` is purpose-built for browser-direct Live — tokens
are single-use, short-lived (≤30 min), and bound to a specific model /
voice / tools / system-instruction so a leaked token can only open one
specific kind of session. Vertex's OAuth bearer is broader-scoped (full
Vertex AI for ~1 h) and has no equivalent constraint mechanism. The
existing Vertex usage (batch annotation flow) is unaffected; both
surfaces bill the same GCP project. A small one-shot `gcloud` script
in `deploy/enable-gemini-live.sh` enables the Generative Language API
and mints a project-scoped API key.
