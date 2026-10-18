# ADR 0130 — Replace Gemini Live voice with a text chat on Vertex

Date: 2026-10-18 · Status: accepted · Lifespan: Invariant ·
Supersedes: [0016](./0016-gemini-live-clip-assistant-browser-direct.md),
[0018](./0018-gemini-live-clip-assistant-wss-view-model.md),
[0109](./0109-live-setup-complete-handshake-and-payload-split.md),
[0110](./0110-live-output-audiocontext-gesture-unlock-and-reuse.md),
[0112](./0112-gemini-live-ephemeral-tokens.md) ·
Spec: `docs/specs/2026-10-18-clip-chat-on-vertex-design.md`

## Context

Live mode let people talk to Gemini about the clip on screen. The browser
opened a WebSocket straight to Google's Gemini API (AI Studio), using an
ephemeral token the server minted with `GEMINI_API_KEY` (ADR 0112).
Annotation, by contrast, runs on Vertex AI with the app's service account.

- **Billing.** AI Studio's Gemini API is moving to prepaid billing only,
  and switching back to postpaid is not supported
  (<https://ai.google.dev/gemini-api/docs/billing>). The `catdav` project's
  prepaid credits ran out, and Live failed with WebSocket close 1011
  "prepayment credits are depleted". Vertex bills postpaid on the same
  Cloud Billing account as annotation. The owner (Gory) wants one billing
  route.
- **No browser tokens on Vertex.** Vertex has no ephemeral tokens for the
  browser (<https://github.com/googleapis/js-genai/issues/766>). A
  browser-direct Live session on Vertex would need a server WebSocket relay.
- **What was built first.** Earlier in this same PR we revived Live on the
  AI Studio API: v1beta token minting and socket, `gemini-3.8-live`, context
  compression and session resumption, typed questions in the same session,
  and a dialog under the video. It kept the separate prepaid bill and the
  API key, and it hit the empty prepaid balance. Gory asked: "if we drop
  gemini live and switch to chat interface with gemini 3.8 flash, could we
  use vertex ai and simplify the solution? just no voice, the same
  functionality and one billing". We dropped the revival for that.

## Decision

- The clip page's ⋯ menu offers **Chat with AI**: a text chat about the
  clip, in the dialog under the video. There is no voice in or out.
- The browser holds no Google credentials. It calls the app
  (`POST /api/live/sessions`, `.../turns`, `.../end`), and the server answers
  each turn through `GeminiService.chat_stream` on Vertex, with the same
  service account as annotation. The answer streams back as NDJSON lines:
  Gemini's thinking headlines, the text as it is written, then the saved
  answer with any web searches and sources (Vertex reports searches only
  at the end). The stream ends with the answer and is cut off after 120 s,
  so it does not reopen the idle-stream problem of #131.
- Model: `GEMINI_CHAT_MODEL`, default `gemini-3.8-flash`, with Google Search
  grounding. 3.8 Flash is not served in `europe-west3` alone, so deployments
  set `GCP_LOCATION=global` (local `.env` and prod already do). Checked on
  2026-10-18: `gemini-3.8-flash` answers through Vertex in project `catdav`
  with the app's credentials.
- The system instruction is the editable prompt `chat.system_instruction.cs`
  plus the clip's Czech context (published annotations and the draft),
  rebuilt on every turn.
- The server stores the transcript turn by turn and rebuilds the
  conversation from it. Only the new turn carries a frame; earlier frames
  are not re-sent.
- Every turn records its tokens and cost in `run_telemetry`
  (`kind='live'`, call `chat_open` / `chat_turn` / `summary`).
  `gemini-3.8-flash` is on the rate card.

## Consequences

- Voice is gone. Answers come as text after a few seconds.
- One bill: chat, summaries and annotation all go through Vertex.
- No API key. `GEMINI_API_KEY` and the `GEMINI_LIVE_*` settings,
  `deploy/enable-gemini-live.sh`, token minting and the browser audio code
  are removed.
- Staging gets the chat. It never had a Gemini key, so Live was off there;
  the chat only needs Vertex.
- Names keep "live": the route prefix `/api/live`, the `live_sessions`
  table, and `run_telemetry` `kind='live'`. Renaming them would mean a
  migration and churn for no user benefit.
- Older databases keep the Live-era prompt row `live.system_instruction.cs`.
  It is unused and stays hidden from the annotation pickers.
- Deploy: no migration. Startup seeds `chat.system_instruction.cs` when it
  is missing, so each server gets it on its first boot of this version.
  Seeding only inserts: later edits to the seed file never reach a database
  that already has the prompt (edit it on the Prompts page), and edits made
  to the old Live prompt are not carried over; copy them by hand if wanted.
- The Secret Manager secret `gemini-api-key` is no longer used. The owner
  can delete it; the app and deploy no longer read it.
- `docs/gemini-live-lessons.md` stays as history in case voice comes back.

## Alternatives considered

1. **Top up AI Studio prepaid credits.** Blocked by payments profile
   permissions, and it keeps two billing routes. Rejected.
2. **Live on Vertex through a server WebSocket relay.** Keeps voice, but
   means more code and extra latency, and the server holds a long-lived
   socket per user. Possible later if people want voice back.
3. **Give the browser a one-hour OAuth access token** so it can call Vertex
   Live directly. The token would carry the service account's full scope,
   not just one Live session. Too broad. Rejected.
