# Clip chat on Vertex: text chat with Gemini about the clip

Date: 2026-10-18 · ADR: [0130](../adr/0130-clip-chat-on-vertex-replaces-live.md)

## Goal

Let people ask Gemini about the clip they are looking at, as a text chat,
billed on Vertex like annotation. This replaces Live mode (voice over a
browser WebSocket to the AI Studio Gemini API), which stopped when the
prepaid AI Studio credits ran out. One billing route, no API key, and the
same features minus voice.

## User flow

1. On the clip page, open ⋯ and choose **Chat with Gemini**.
2. A dialog opens under the video. Gemini greets in Czech and describes the
   frame on screen. "Gemini is answering…" shows while it waits.
3. The input row has **Send frame**, **End chat**, a text box ("Ask about
   this clip…") and **Send**. Enter sends.
4. A typed question goes with the frame on screen, so "what is this?" means
   what you are looking at.
5. **Send frame** adds "Frame at 0:12" to the transcript and asks Gemini to
   describe that frame.
6. **End chat** asks "End chat?" with **Keep chatting** / **End chat**. After
   ending, the transcript stays under the video and the History tab lists
   the chat with a short summary.
7. Starting another chat on the same page keeps the earlier one above a
   "New chat" divider, greyed.
8. Leaving or reloading the page mid-chat ends the chat (reason `navigate`).
   The transcript is already saved.

## Backend

Routes keep the Live-era prefix `/api/live`:

| Route | Body | Returns |
|---|---|---|
| `POST /api/live/sessions` | `{clip_id, frame_b64?}` | `{session_id, reply}`: the greeting about the frame |
| `POST /api/live/sessions/{id}/turns` | `{kind: "typed" \| "frame", text, frame_b64?}` | `{reply}` |
| `POST /api/live/sessions/{id}/end` | `{end_reason: user_stop \| navigate \| error}` | |
| `POST /api/live/sessions/{id}/summarize` | | Czech summary, 2–4 sentences |
| `GET` history endpoints | | unchanged |

- Each turn is answered by `GeminiService.chat`
  (`backend/app/services/gemini.py`) on Vertex, with the app's service
  account. Model: `GEMINI_CHAT_MODEL`, default `gemini-3.8-flash`. Google
  Search grounding is on.
- The system instruction is the prompt `chat.system_instruction.cs` (seeded
  from `backend/seeds/chat_system_instruction_cs.json`, editable on the
  Prompts page, hidden from the annotation pickers) plus the clip's Czech
  context: published annotations and the draft. It is rebuilt on every
  turn.
- The summary uses `GEMINI_MODEL`, also through Vertex.
- 3.8 Flash is not served in `europe-west3` alone. Deployments use
  `GCP_LOCATION=global` (local `.env` and prod already do).

## Data

- The transcript is saved turn by turn in `live_sessions.transcript_json`.
  The table keeps its Live-era name.
- The server rebuilds the conversation from the stored transcript. Only the
  new turn's frame is sent; earlier frames are not re-sent.
- Older databases keep the unused prompt row `live.system_instruction.cs`,
  hidden the same way.

## Costs

Each call writes its tokens and cost to `run_telemetry` with `kind='live'`
and call `chat_open`, `chat_turn` or `summary`. `gemini-3.8-flash` is on
the rate card, so cost is recorded, not left blank.

## What's removed

- Voice in and out, the browser audio code (`liveAudio.js`,
  `audio-worklet-recorder.js`) and the REC header bar.
- Ephemeral token minting, `GEMINI_API_KEY` (setting and the deploy secret
  mapping), `GEMINI_LIVE_MODEL`, `GEMINI_LIVE_VOICE`,
  `GEMINI_LIVE_LANGUAGE_CODE`, `GEMINI_LIVE_INACTIVITY_S` and
  `deploy/enable-gemini-live.sh`.
- The 60-second inactivity auto-end, the voice `end_session` tool and the
  automatic frame on pause.
- The Secret Manager secret `gemini-api-key` is no longer read. The owner
  can delete it.

Staging never had a Gemini key, so Live was off there. The chat only needs
Vertex, so it works on staging too.

## Risks

- Answers take a few seconds; there is no streaming.
- Each turn sends the whole conversation text, so long chats cost more per
  turn. Frames are not re-sent, which keeps this small.
- If Vertex stops serving `gemini-3.8-flash` in the configured location,
  every chat fails with a toast. Set `GEMINI_CHAT_MODEL` to a model that
  is served there.

## Manual acceptance flows

1. Open a clip whose video is ready. Open ⋯ and choose **Chat with
   Gemini**. The dialog appears under the video, shows "Gemini is
   answering…", then a Czech greeting that describes the frame on screen.
2. Type a question in the box and press Enter. It appears on the right, the
   box empties, and Gemini's answer appears on the left after a few seconds.
3. Move the video to another shot and press **Send frame**. The video
   flashes, "Frame at …" (the current time) appears in the transcript, and
   Gemini describes that frame.
4. Press **End chat**. A dialog asks "End chat?". Press **Keep chatting**:
   the dialog closes and the chat carries on (ask another question to
   check).
5. Press **End chat** again and confirm with **End chat**. The input row
   goes away and the transcript stays under the video. Open the History
   tab: the chat is listed with a short Czech summary.
6. Choose **Chat with Gemini** again on the same page. The earlier chat
   stays above a "New chat" divider, greyed, and a new greeting appears
   below it.
7. Make Vertex fail in a running chat: turn off the machine's network (or
   the server's), then ask a question. A toast shows the error, your
   question stays in the transcript and the chat stays open. Turn the
   network back on and ask again: Gemini answers.
8. Start a chat, then reload the page. In the History tab the chat is
   listed with reason `navigate`.
