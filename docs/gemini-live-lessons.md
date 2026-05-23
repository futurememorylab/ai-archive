# Gemini Live Integration — Lessons Learned

What we wish we'd known on day one of wiring the Czech voice assistant
into the clip-detail page. Each item below is grounded in an actual
error message we hit; the commit hash next to it is the fix.

Read alongside:
- `docs/specs/2026-05-23-gemini-live-clip-assistant-design.md` — what
  the feature is supposed to do.
- `docs/decisions.md` — durable architectural decisions.

---

## 1. Audio bytes must never traverse the backend

**Symptom (PoC, pre-feature):** Czech speech came back scrambled; Gemini
could not understand the user. The bridge added enough latency and
re-encoding to break recognition.

**Fix:** Browser opens the WSS directly to Google. Backend's role is
limited to (a) returning the setup payload, (b) returning the API key
for `?key=`, (c) persisting transcripts, (d) running the post-session
summarize via plain `generateContent`. **No FastAPI WebSocket route.**

This is the single hardest-locked decision; everything else accommodates
it.

---

## 2. The Live API endpoint surface

Mistakes the spec made, all corrected during integration:

| Spec said | Reality | Failure mode |
|---|---|---|
| Setup wraps fields under `config` (REST shape) | Flat — `model`, `generationConfig`, `systemInstruction`, `tools`, `outputAudioTranscription`, `inputAudioTranscription` all at top level. Only `responseModalities` and `speechConfig` nest in `generationConfig`. | 400 from `authTokens.create`: *"Unknown name \"config\" at 'auth_token.bidi_generate_content_setup'"* (commit `eeacc55`) |
| WSS path is `v1alpha.GenerativeService.BidiGenerateContent` | Live native-audio models exist only on `v1beta`. The v1beta path also accepts API keys minted from the v1alpha `authTokens.create`. | Close `1008 model is not found for API version v1alpha` (commit `1cb588f`) |
| Auth via `?access_token=<token>` | `?access_token=` is the OAuth-bearer slot (Vertex style) and isn't accepted by the Generative Language API. Use `?key=<token>`. | Close `1008 "Method doesn't allow unregistered callers"` (commit `62248b5`) |
| Audio chunks go in `realtimeInput.mediaChunks: [{...}]` | Deprecated. Current shape: `realtimeInput.audio: {…}` and `realtimeInput.video: {…}` — single object, not an array. | Close `1007 "realtime_input.media_chunks is deprecated. Use audio, video, or text instead."` (commit `1cb588f`) |
| Server sends JSON as text WSS frames | Server sends JSON as **binary** WebSocket frames (because we set `binaryType="arraybuffer"`). Trying to `JSON.parse(ArrayBuffer)` silently throws; you lose every server message including `setupComplete` and every audio chunk. | No audio out, no transcripts; console showed only `WSS msg (binary): 26 bytes` (commit `5ea15d4`) |

---

## 3. The model list is *not* what the docs page suggests

The spec pinned `gemini-2.5-flash-preview-native-audio-dialog`. That
model name doesn't exist on either surface.

**Discover the truth via the API itself:**

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models?key=${GEMINI_API_KEY}&pageSize=200" \
  | jq '.models[] | select(.supportedGenerationMethods | any(. == "bidiGenerateContent")) | .name'
```

As of 2026-05-23 this returned four model names. We picked
`gemini-3.1-flash-live-preview` because it's the **half-cascade Live
model** — the native-audio family does not support Czech (see §4).

When in doubt, list models against the live API; don't trust hand-typed
model names from a docs page.

---

## 4. Native-audio Live ≠ multilingual Live

`gemini-2.5-flash-native-audio-*` rejects `speechConfig.languageCode: "cs-CZ"`
with: *"Unsupported language code 'cs-CZ' for model
models/gemini-2.5-flash-native-audio-latest"*.

**Two families exist:**

- **Native-audio** (e.g. `gemini-2.5-flash-native-audio-*`) — fewer
  languages, higher voice quality, true end-to-end audio in the model.
- **Half-cascade** (e.g. `gemini-3.1-flash-live-preview`) — STT → text
  model → TTS, far more languages including Czech, slightly less
  natural voice.

For non-English production work today, half-cascade is the only path.
Switch back to native-audio if/when Czech is added.

---

## 5. Ephemeral tokens (`authTokens.create`) are not free

The spec called for `authTokens.create` with `liveConnectConstraints`
bound at mint time — purpose-bound, single-use, ~30-min tokens. We
implemented it. Then:

- Token name is `auth_tokens/<hex>`, **not** `tokens/<hex>` as the spec
  guessed.
- The bare hex (the part after the `/`) is what goes into `?key=` —
  passing the full resource name closes the socket with `1007 "API key
  not valid"`.
- Even after fixing the prefix, `?key=<bare hex>` opens the WSS
  handshake but Google closes with `1007 "API key not valid"` the
  moment the client sends `{setup: …}`. We could not get the
  bound-setup to match Google's validator. Setup expansion shaping,
  field ordering, and `?access_token=` variants were all tried.

**Current state:** raw `GEMINI_API_KEY` is sent to the browser, and the
browser passes it as `?key=` to the WSS. Trade-off accepted because:

- Single-operator local app over WireGuard VPN.
- API key is scoped via `gcloud alpha services api-keys create
  --api-target=service=generativelanguage.googleapis.com` — it can only
  hit the Generative Language API, not other GCP surfaces.
- If exposure becomes a concern, revisit ephemeral tokens. By then
  Google may have fixed whatever caused the persistent rejection.

If you try again: the official `@google/genai` JS SDK uses
ephemeral tokens internally for browser-direct Live. Reading its
WSS-open code (not the docs) is probably the only way to discover the
correct field/shape Google actually validates.

---

## 6. `turnComplete` controls who speaks first

`clientContent.turns: [{role:"user", parts:[…]}]` with `turnComplete: false`
means "I'm not done with this user turn yet" — the model **will not
respond** until either you set it to `true` later or VAD on a real-time
audio input declares the user done.

The spec mistakenly used `turnComplete: false` for the initial context
turn, so Gemini just sat silent. Fix: `turnComplete: true` on the
initial turn AND append an explicit "Pozdrav mě" instruction so the
model speaks on connect — useful both for "did voice output actually
wire up?" verification and as the natural session opener.

---

## 7. Static asset caching during dev iteration

Every JS edit we made hit the same wall: the browser cached the old
`liveSession.js` and **regular `location.reload()` did not refresh it**.
Only `Cmd+Shift+R` (or `Disable cache` in DevTools) actually bypasses
the disk cache.

If this becomes a chronic friction point during ongoing Live work, the
template-level fix is to mount `/static/` with `Cache-Control: no-cache`
in dev mode, or append a `?v=<build_nonce>` query string to the
`<script>` tags. We didn't bother — only one person iterates on this
file and Cmd+Shift+R is fine for now.

---

## 8. Czech quotes in JS string literals

Bug hit at the end: I wrote
```js
"Pozdrav mě česky („Dobrý den") a popiš snímek."
```

Looks fine. But the closing `"` after `den` is the ASCII `"` (U+0022),
which closes the outer double-quoted JS string — making the rest of
the file a syntax error. Alpine then failed to mount the entire
clip-detail x-data block, so the title and player disappeared too,
which masked the real cause.

**Lessons:**
- Czech text in JS literals: use single-quoted JS strings or use the
  matching Czech right-curly-quote `”` (U+201D), not ASCII `"`.
- A blank-page UI symptom on a page that already worked usually means
  a `SyntaxError` on a recently-edited script — check the browser
  console *first*, then bisect.

---

## 9. What the manual debug loop looked like

For future Live-API debugging on this project, the most efficient
investigation pattern is:

1. Open DevTools → Console *and* Network → WS.
2. Click 🎤 Live. Watch for `WSS opening`, `WSS open`, `WSS msg`,
   `WSS close` log lines from `liveSession.js`.
3. **If the close fires within ~1 second:** Google rejected our setup.
   Read the `reason` text on `wsclose` event — Google's error messages
   are unusually specific (e.g. *"Unsupported language code 'cs-CZ' for
   model …"*, *"realtime_input.media_chunks is deprecated"*). Each
   message identifies the exact field to fix.
4. **If no audio plays despite mic permission granted:** check that
   server frames are reaching `_onWsMessage`. The binary→text decode
   trap (§2 last row) is invisible by default — `[live] WSS msg:` log
   lines confirm the decode is working.
5. **If you suspect the model name or language is bad:** query
   `models.list` (§3) rather than guessing.

The Live API's error messages are good. Trust the close reason; don't
re-derive from the docs.

---

## 10. Future work (when motivated)

- **Restore ephemeral tokens.** The security improvement is real;
  inspect the official Google JS SDK source to figure out the actual
  bound-setup contract.
- **Switch back to native-audio** when Czech is added.
- **Cache-bust static assets** for dev (see §7) if we expect more JS
  iteration here.
- **Render `googleSearch` citations** in the History panel transcript.
  Currently the citations come back inside the transcript JSON but
  the History view doesn't surface them as clickable chips.
- **`outputAudioTranscription` text styling** in the live strip — right
  now both the user-STT and model-TTS-transcript lines are rendered
  identically; visual distinction would help.
