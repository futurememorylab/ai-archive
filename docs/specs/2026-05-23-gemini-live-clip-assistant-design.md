# Gemini Live Clip Assistant — Design Spec

**Date:** 2026-05-23
**Status:** Draft, awaiting review
**Author:** Peter Hora (with Claude)
**Builds on:** existing clip-detail player (`clip_detail.html`,
`static/player.js`), prompt-management surface
(`repositories/prompts.py`), and the project's existing Gemini /
GCP scaffolding in `settings.py`.

---

## 1. Motivation

The clip-detail page already gives the operator everything needed to look
at a single archival frame — the proxy is playing, timecodes are visible,
all CatDV metadata and `pragafilm.*` fields are on the right. What's
missing is a way to *talk* about that frame in real time with a
knowledgeable assistant: ask where it was shot, what year it could be,
what the building in the background is, with the answer arriving as Czech
speech within ~600 ms. The collection is private Czech home-movie
footage from the 1920s–1940s; the operator is Czech; the fastest way
to enrich descriptions is voice, not typing.

Gemini Live API native audio fits exactly this shape: bidirectional Czech
voice, multimodal input (the current frame plus annotation context), and
Google Search grounding for location and historical queries.

### Non-goals

- **Automatic annotation writes.** The assistant never writes to the
  draft or published annotation columns. Output lands in a read-only
  *Live history* surface; the operator decides what to copy out.
- **Backend bridging of audio.** A prior PoC showed that piping Live API
  audio through our FastAPI process introduces enough latency and
  re-encoding artifacts that Gemini misunderstands the Czech speech.
  Audio bytes must flow browser ↔ Google directly.
- **Offline support.** Live needs the internet by definition; the feature
  is hidden when `mode != "online"`.
- **E2E audio tests.** Live calls are metered and flaky to assert
  against; audio quality is verified manually per the checklist in §8.

---

## 2. User-visible behavior

### 2.1 Entry point

A new button `🎤 Live` in the clip-detail header, sitting in the same
`<span class="cache-actions">` group as *Evict local* / *Cache video* /
*Annotate ▾*. Visible whenever:

- `mode == "online"` (hidden in `offline` / `forced_offline`), and
- the clip is loadable in the player (`clip.duration_secs > 0`).

The button stays present whether or not the local proxy is cached — Live
works against whatever the `<video>` element is currently rendering.

### 2.2 Active-session header overlay

Pressing `🎤 Live` requests mic permission, mints an ephemeral token,
opens the WSS to Gemini, and **replaces** the contents of the
clip-detail header row with the live control bar:

```
┌─ [● REC 0:42]   [📸 Send frame]   [■ Stop] ────────┐
├─ ai: „Na snímku je vidno staré auto…"             │
│   you: „Můžeš odhadnout dekádu?"      [▾ expand]  │
├──────────────────────────────────────────────────┘
│   [video frame]                                  │
│   timeline ─●────────────────────────────────────│
```

- `● REC mm:ss` — live indicator with elapsed time.
- `📸 Send frame` — explicit "send current frame" trigger.
- `■ Stop` — explicit close, also available via voice (§3.4).
- Below the header, a **thin transcript strip** shows the last 2–3
  lines. Clicking *expand* grows it into a taller scrolling panel
  without disturbing the player or annotation column.

The player, timeline, annotation column, and keyboard shortcuts stay
fully usable during the session so the operator can scrub freely while
talking.

### 2.3 Frame delivery

- **At session start:** the frame currently visible in `<video>` is
  captured and sent as the first user turn's image part, alongside the
  assembled annotation context (see §3.3 for the exact shape — it
  includes both published CatDV data and the operator's in-progress
  draft, clearly labeled in Czech).
- **On `<video>` `pause` event:** the current frame is auto-captured and
  pushed as a `realtimeInput` image part. (Playing → pausing is the
  natural "look at this" gesture during scrubbing.)
- **On `📸 Send frame` click:** same, manual trigger — useful when the
  video is paused already and the operator wants to re-send after a
  step.

All frames go out as JPEG quality 0.85, downscaled if larger than
1280 × 720 (typical proxy is already at or below this). The
canvas-extraction path is same-origin because the proxy is served by
the local backend.

### 2.4 Session end

The session ends on **any** of:

| Trigger | `end_reason` recorded |
|---|---|
| Operator clicks `■ Stop` | `user_stop` |
| Model invokes `end_session()` function tool (voice cue) | `voice_stop` |
| 60 s of mutual silence (no audio in either direction) | `inactivity` |
| `<video>` page is navigated away | `navigate` |
| WSS errors / closes unexpectedly | `error` |

On close, the browser:
1. Restores the original header row (cache actions, etc.).
2. POSTs the assembled transcript to
   `POST /api/live/sessions/{id}/transcript`.
3. POSTs `POST /api/live/sessions/{id}/summarize`, which runs a regular
   (non-Live) `generateContent` call to distill a short Czech summary
   and stores it.

The `🎤 Live` button reappears so the operator can start a fresh
session.

### 2.5 Live history panel

A new tab in the right column, alongside *Published / Draft*:
**History**. It lists past Live sessions for this clip in reverse
chronological order, showing date, duration, and end reason. Expanding
a row reveals the transcript and Czech summary. It is **read-only**;
nothing is auto-pushed into draft annotations.

---

## 3. Architecture

### 3.1 Surface choice

Live audio uses the **Gemini Developer API**
(`generativelanguage.googleapis.com`), not Vertex AI, because:

- Its `authTokens.create` endpoint mints **ephemeral, purpose-bound,
  single-use tokens** with `liveConnectConstraints` (model, voice,
  language, tools, system instruction). Vertex AI Live requires raw
  OAuth bearer tokens with broader Vertex-wide scope — a worse fit for
  a token that lives in the browser.
- Billing posts to the same GCP project (enabling the API is a one-line
  gcloud call; see §7).
- The rest of the app's Gemini usage (batch annotation flow on Vertex)
  is unaffected; only the Live feature uses the Developer API.

### 3.2 Components

```
browser (Alpine)            backend (FastAPI)             Google
─────────────────           ──────────────────            ──────
liveSession() component  ── GET /session-config ──▶  authTokens.create
                                                       (cs system instr.,
                                                        tools, voice, …)
                            ◀── {token, setup_payload, session_id} ──
WSS open (direct)  ─────────────────────────────────▶  Gemini Live
  PCM 16kHz up ────────────────────────────────────▶
  PCM 24kHz down ◀────────────────────────────────
  inline JPEG frames ─────────────────────────────▶
  function_call: end_session ◀─────────────────────
  text deltas ◀────────────────────────────────────

(close)
POST /sessions/{id}/transcript ──▶ live_sessions row
POST /sessions/{id}/summarize ───▶ generateContent ──▶  Gemini (non-Live)
                            ◀── summary_cs stored ──
```

**Browser** owns: WSS lifecycle, mic capture / playback, frame
extraction, transcript assembly, inactivity timer, function-tool
handling.

**Backend** owns: token mint, system-prompt + annotation-context
assembly, transcript persistence, post-session summarization, history
read API. **Backend never touches the audio bytes.**

### 3.3 Live setup payload

The backend assembles a `liveConnectConstraints` payload like:

```jsonc
{
  "model": "models/<settings.gemini_live_model>",
  "config": {
    "responseModalities": ["AUDIO"],
    "speechConfig": {
      "languageCode": "cs-CZ",
      "voiceConfig": {
        "prebuiltVoiceConfig": { "voiceName": "<settings.gemini_live_voice>" }
      }
    },
    "outputAudioTranscription": {},
    "inputAudioTranscription": {},
    "systemInstruction": {
      "parts": [ { "text": "<latest live.system_instruction.cs prompt>" } ]
    },
    "tools": [
      { "googleSearch": {} },
      { "functionDeclarations": [
        { "name": "end_session",
          "description": "Ukončit aktuální živou relaci na žádost uživatele.",
          "parameters": {
            "type": "object",
            "properties": { "reason": { "type": "string" } },
            "required": ["reason"]
          }
        }
      ]}
    ]
  }
}
```

The browser sends this verbatim as the first WSS `setup` message, then
sends a `clientContent` user turn containing the initial JPEG frame and
a text part with the assembled annotation context. The context is two
clearly-labeled Czech blocks so the model never conflates committed
data with the operator's working hypothesis:

```
=== Publikované anotace (z CatDV) ===
Název klipu: <clip.name>
Formát: <clip.format>   FPS: <clip.fps>   Délka: <clip.duration smpte>
Poznámky:
<clip.notes>
Rozšířené poznámky:
<clip.big_notes>
Markery (čas → popis):
- <smpte> – <smpte>  „<marker.name>" — <marker.description>
- ...
Vlastní pole (pragafilm.*):
- pragafilm.rok.natočení: <values>
- pragafilm.dekáda.natočení: <value>
- ... (jen vyplněná pole)

=== Rozpracované anotace (můj draft, ještě neuložené do CatDV) ===
Draft markery:
- <smpte> – <smpte>  „<draft_marker.name>" — <draft_marker.description>
- ...
Draft pole:
- <field>: <value>
- ...
Draft poznámky:
<draft.notes>

(Konec kontextu. Následuje aktuální snímek a moje otázka.)
```

If either block is empty (a clip with no draft yet, or a freshly-imported
clip with no published custom fields), it is omitted entirely rather
than printed as an empty section. All Czech text is run through
`view_models._fix` first for mojibake repair (see
[[catdv-mojibake-display-fix]]).

### 3.4 Czech system instruction

Managed as a prompt template named `live.system_instruction.cs` in the
existing prompt-management system. Initial seed (operator can iterate
through the prompts UI without redeploying):

> *Jsi asistent pro popis archivních filmových záběrů ze soukromého
> českého archivu, převážně z let 1920–1950 (formáty 9,5 mm a 16 mm,
> domácí filmy). Uživatel ti pošle aktuální snímek z proxy videa
> a metadata k záběru. Metadata obsahují dva bloky: **Publikované
> anotace** (data již uložená v CatDV — ber je jako daná) a
> **Rozpracované anotace** (uživatelův draft, jeho pracovní hypotéza —
> užitečný kontext, ale ne pravda; pokud vidíš ve snímku rozpor
> s draftem, klidně to zmiň). Tvým úkolem je pomoci popsat scénu,
> odhadnout lokaci, dataci, identifikovat objekty a historický kontext.
> Komunikuj výhradně česky, krátkými větami vhodnými pro hlasovou
> odpověď. Pokud potřebuješ ověřit lokaci, historickou událost,
> vozidlo, módu nebo jiný detail, použij nástroj `googleSearch`.
> Pokud uživatel vyjádří přání ukončit konverzaci (např. „konec",
> „děkuji, ukonči to", „dobře, to stačí"), zavolej nástroj
> `end_session` s krátkým odůvodněním. Buď stručný a věcný.*

### 3.5 Function-tool voice-stop

`end_session(reason)` declared in `tools.functionDeclarations`. The
browser handler is straightforward:

```js
onToolCall(call) {
  if (call.name === "end_session") {
    this.endReason = "voice_stop";
    this.transcript.push({ role: "system", text: `Konec: ${call.args.reason}` });
    this.close();   // graceful WSS close, triggers persistence flow
  }
}
```

Stop button uses the same `close()` path with `endReason = "user_stop"`.

### 3.6 Inactivity timer

A single rolling timer in the Alpine component, reset on either inbound
audio chunk or outbound audio chunk. On expiry → `close()` with
`endReason = "inactivity"`. Default 60 s, configurable via
`settings.gemini_live_inactivity_s`.

---

## 4. Backend surface

### 4.1 New routes (`backend/app/routes/live.py`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/live/session-config?clip_id=…` | Mint ephemeral token, return setup payload, insert pending session row. |
| `POST` | `/api/live/sessions/{id}/transcript` | Persist transcript JSON, end_reason, ended_at. Idempotent on retry. |
| `POST` | `/api/live/sessions/{id}/summarize` | Run non-Live `generateContent`, persist `summary_cs`. Idempotent (no-op if already set). |
| `GET` | `/api/live/sessions?clip_id=…` | List sessions for the History panel (id, started_at, duration, end_reason, has_summary). |
| `GET` | `/api/live/sessions/{id}` | Full transcript + summary for the expand view. |

All routes 404 when `app_state.mode != "online"`.

### 4.2 Service (`backend/app/services/live_sessions.py`)

- `assemble_setup_payload(clip, draft) -> dict` — prompt + tools +
  speech + initial context turn. Pulls published data from the clip
  object and draft data (markers, fields, notes) from the existing
  `annotations` repository for this `clip_id`; omits either block if
  empty; runs all Czech text through `view_models._fix` for mojibake
  cleanup (see [[catdv-mojibake-display-fix]]).
- `mint_ephemeral_token(setup_payload) -> AuthToken` — HTTP POST to
  `authTokens.create` with `uses=1`, `expireTime=+30min`.
- `summarize(session_id) -> None` — load transcript, call
  `generateContent` with a Czech "shrň konverzaci ve 2–4 větách" prompt,
  store result. Idempotent.

### 4.3 Repository (`backend/app/repositories/live_sessions.py`)

CRUD on the new `live_sessions` table; the only consumer of that table.

### 4.4 Schema (`backend/migrations/NNN_live_sessions.sql`)

```sql
CREATE TABLE live_sessions (
  id              TEXT PRIMARY KEY,            -- uuid v4
  clip_id         INTEGER NOT NULL,
  prompt_version  INTEGER,
  state           TEXT NOT NULL,               -- pending | active | ended | failed
  started_at      TIMESTAMP,
  ended_at        TIMESTAMP,
  end_reason      TEXT,                        -- user_stop|voice_stop|inactivity|navigate|error
  transcript_json TEXT,
  summary_cs      TEXT,
  frame_count     INTEGER NOT NULL DEFAULT 0,
  search_calls    INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_live_sessions_clip
  ON live_sessions (clip_id, created_at DESC);
```

`pending` rows older than 1 hour are reaped by a cleanup query that
runs on app startup (cheap; the table will stay small for the
single-operator workload).

### 4.5 Settings additions (`backend/app/settings.py`)

```python
gemini_api_key: str                                 # required for Live
gemini_live_model: str = "gemini-2.0-flash-exp"     # confirm Live-capable model at impl time
gemini_live_voice: str = "Aoede"                    # placeholder; confirm Czech-capable voice
gemini_live_inactivity_s: int = 60
```

---

## 5. Frontend (`backend/app/static/liveSession.js`)

New Alpine component `liveSession(clipId)` composed into the existing
`x-data` on the clip-detail root, alongside `player()` and
`clipAnnotate()`. Public API the template uses:

- `state` — `"idle" | "connecting" | "active" | "closing"`.
- `transcript` — array of `{role, text, ts}` for the strip.
- `elapsedFmt` — `"mm:ss"` for the REC indicator.
- `start()` — wires up everything.
- `sendFrame()` — manual button handler.
- `close(reason)` — graceful close + persistence.
- `expanded` — toggle for the transcript strip's tall view.

Internal subsystems:

- **`audioCapture`** — `AudioContext({sampleRate: 16000})` + an
  `AudioWorkletNode` that emits Int16 PCM chunks (100 ms) → base64 →
  `realtimeInput.audio`.
- **`audioPlayback`** — `AudioContext({sampleRate: 24000})` + an
  `AudioBufferSourceNode` queue fed from inbound PCM chunks.
- **`frameSender`** — shared `OffscreenCanvas` reused per snapshot, JPEG
  0.85, downscale to 1280×720 max.
- **`wsClient`** — opens
  `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key=<ephemeral>`
  (auth path confirmed against current docs during impl).
- **`inactivityTimer`** — 60 s rolling.

The template change in `clip_detail.html` is minimal: add the button,
add a `<template x-if="liveSession.state !== 'idle'">` block that
overlays the header row with the control bar + transcript strip, and
include `liveSession.js` in the static assets.

---

## 6. Error handling

| Failure | Effect |
|---|---|
| Mic permission denied | Inline error in header; no WSS opened. Pending session row reaped by the 1 h cleanup. |
| `authTokens.create` fails (quota / invalid key) | Toast with the API error; no row inserted. |
| WSS open / unexpected close | Whatever transcript exists → `POST /transcript` with `end_reason='error'`; summarize still attempted (skipped server-side if transcript is empty). |
| Summarize call fails | Row stays `ended` with empty `summary_cs`; the History panel shows a *"Generate summary"* retry button hitting `POST /summarize` again. |
| Browser navigates away mid-session | `beforeunload` handler closes WSS, fires `POST /transcript` via `navigator.sendBeacon`. Summary triggered lazily on first History-panel open if missing. |
| Live model returns no audio | Yellow banner in strip ("zvuk nedostupný, čtěte přepis"); session continues as text-only. |

---

## 7. Infrastructure — `deploy/enable-gemini-live.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT="${GCP_PROJECT_ID:?set GCP_PROJECT_ID}"

# 1. Enable Generative Language API on the project.
gcloud services enable generativelanguage.googleapis.com --project="$PROJECT"

# 2. Mint an API key restricted to the Generative Language API.
gcloud alpha services api-keys create \
  --display-name="catdv-live-tokens" \
  --api-target="service=generativelanguage.googleapis.com" \
  --project="$PROJECT"

# 3. Print the key value so the operator can paste into .env as GEMINI_API_KEY.
KEY_NAME="$(gcloud alpha services api-keys list \
  --filter='displayName=catdv-live-tokens' \
  --format='value(name)' --project="$PROJECT" | head -1)"
gcloud alpha services api-keys get-key-string "$KEY_NAME" --project="$PROJECT"
```

The existing service-account credentials are **not** used for Live.
Existing Vertex usage (batch annotation) stays as-is.

---

## 8. Testing

### 8.1 Automated (TDD per project default)

- **`tests/services/test_live_sessions.py`** — `assemble_setup_payload`
  with: (a) rich `pragafilm.*` published fields + a non-empty draft;
  (b) published-only clip with no draft; (c) draft-only (freshly-cached
  clip not yet annotated in CatDV); (d) both empty (smoke); (e) mojibake
  in both published `notes` and a draft marker description.
  Token-mint HTTP shape (mocked); summarize idempotency + call shape.
- **`tests/repositories/test_live_sessions_repo.py`** — CRUD, state
  transitions, list-by-clip ordering, pending-row cleanup.
- **`tests/routes/test_live.py`** — happy paths, 404 in `offline` mode,
  transcript persistence, summarize idempotency endpoint, History list +
  detail.

### 8.2 Manual verification checklist (run with a real Gemini key)

The audio path is verified by hand because real Live calls are metered
and assertions over audio quality are brittle:

- [ ] `🎤 Live` button appears only when `mode == "online"` and clip has
      duration > 0.
- [ ] First click triggers mic permission prompt; denial shows inline error.
- [ ] Header overlays with REC / Send frame / Stop controls; player and
      annotation column remain interactive.
- [ ] Round-trip Czech latency feels < ~600 ms (the PoC benchmark).
- [ ] Initial frame is delivered (mention something visible to Gemini and
      confirm a relevant answer).
- [ ] Initial context includes both published and draft annotations;
      Gemini correctly distinguishes them when asked
      (e.g. *"co o tomhle vím z CatDV, a co jsem si k tomu psal?"*).
- [ ] `<video>` pause auto-sends current frame; manual *Send frame* works.
- [ ] Saying *"konec"* / *"děkuji, ukonči to"* triggers `end_session`
      tool call and closes the session.
- [ ] 60 s of mutual silence closes the session.
- [ ] `beforeunload` mid-session persists transcript (visible in History
      panel after reload).
- [ ] History panel lists the session; expand shows transcript + Czech
      summary.
- [ ] *Generate summary* retry works for sessions left without one.
- [ ] `googleSearch` is exercised on at least one location/dating query.

---

## 9. Files touched

### New

- `backend/app/routes/live.py`
- `backend/app/services/live_sessions.py`
- `backend/app/repositories/live_sessions.py`
- `backend/app/models/live_session.py`
- `backend/app/static/liveSession.js`
- `backend/migrations/NNN_live_sessions.sql`
- `deploy/enable-gemini-live.sh`
- `docs/specs/2026-05-23-gemini-live-clip-assistant-design.md` (this file)

### Modified

- `backend/app/main.py` (register the new router)
- `backend/app/settings.py` (4 new fields)
- `backend/app/templates/pages/clip_detail.html` (button + header overlay + transcript strip)
- `backend/app/templates/pages/_anno_panels.html` (History tab)
- `backend/app/seed.py` (seed the `live.system_instruction.cs` prompt)
- `README.md` (env vars, gcloud script reference)

---

## 10. Open items for implementation

These are pinned for the implementation plan, not blockers for the
design:

- **Confirm the current Gemini Live model name and Czech-capable voice
  name.** Both move; verify against live docs at plan-write time and
  pin defaults in `settings.py`.
- **`authTokens.create` exact request shape and WSS URL.** Confirm
  against current Generative Language API docs; the structure described
  in §3.3 is the conceptual shape, not a verbatim contract.
- **`googleSearch` grounding citations** — decide whether to render
  citation chips inside the transcript strip or just keep them in the
  saved transcript JSON.
