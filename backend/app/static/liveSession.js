// Alpine component for the Gemini Live clip assistant.
// Composes into clip_detail.html's existing x-data alongside player() and clipAnnotate().
// Audio bytes flow browser ↔ Google directly via WSS; this component only
// talks to our backend for token minting and post-session persistence.
// Mic capture and playback live in liveAudio.js (mixed in below).

// Reconnects allowed per session when Google ends a connection (~every 10
// minutes) before the session gives up.
const LIVE_MAX_RESUMES = 5;

function liveSession(clipId, config) {
  return Object.assign(liveAudio(), {
    // ── state ────────────────────────────────────────────────────────────
    state: "idle",                  // idle | connecting | active | closing
    transcript: [],                 // this session: [{role, text, ts, kind}]
    earlier: [],                    // earlier sessions on this page, kept on screen
    draftText: "",                  // the typed question being written
    micOn: false,                   // false → typing only (no mic, or blocked)
    confirmEnd: false,              // the "End chat?" dialog is open
    elapsedFmt: "0:00",
    sessionId: null,
    inactivityS: (config && config.inactivityS) || 60,

    // ── internals ────────────────────────────────────────────────────────
    _ws: null,
    _wsUrl: null,
    _frameCount: 0,
    _usage: { summed: {}, last: null, messages: 0 },
    _startedAt: 0,
    _elapsedTimer: null,
    _inactivityTimer: null,
    _endReason: null,
    _setupPayload: null,
    _initialTurn: null,
    _setupComplete: false,          // gate: no content/audio until server ACKs setup
    _resumeHandle: null,            // latest sessionResumptionUpdate handle
    _resumes: 0,

    // ── public API ───────────────────────────────────────────────────────
    async start() {
      if (this.state !== "idle") return;
      // Unlock output audio HERE, synchronously, inside the Live-button gesture.
      // WebKit/Safari only lets an AudioContext leave the suspended state when
      // resumed during a user gesture; doing it later (in the WS callback that
      // receives Gemini's audio) is ignored → silent voice, and reliably silent
      // on the 2nd session once the original gesture is stale. Must run before
      // the first `await` below, or the gesture is already spent. See ADR 0110.
      this._ensureOutputAudio();
      if (this.transcript.length) {
        this.earlier.push(...this.transcript, { role: "system", text: "New session", kind: "divider" });
      }
      this.transcript = [];
      this.draftText = "";
      this._frameCount = 0;
      this._usage = { summed: {}, last: null, messages: 0 };
      this._setupComplete = false;
      this._resumeHandle = null;
      this._resumes = 0;
      this._endReason = null;
      this.state = "connecting";
      try {
        const config = await this._fetchConfig();
        this.sessionId = config.session_id;
        this._setupPayload = config.setup_payload;
        this._initialTurn = config.initial_context_turn;
        this._wsUrl = config.ws_url;
        this.micOn = await this._openMic();
        if (!this.micOn) {
          this._note("No microphone available. Type your questions below.");
        }
        this._openWs();
      } catch (e) {
        this._fail(String(e.message || e));
        this.state = "idle";
        await this._teardown();
      }
    },

    async close(reason) {
      this.confirmEnd = false;
      if (this.state === "idle" || this.state === "closing") return;
      this.state = "closing";
      this._endReason = reason || this._endReason || "user_stop";
      this._closeWs();
      await this._persistAndSummarize();
      await this._teardown();
      this.state = "idle";
    },

    init() {
      // Auto-send frame on player pause while session is active.
      const v = document.querySelector("video.video");
      if (v) {
        v.addEventListener("pause", () => {
          if (this.state === "active") this.sendFrame();
        });
      }
      // Persist transcript on navigation away mid-session.
      window.addEventListener("beforeunload", () => {
        if (this.state === "active") {
          this._endReason = "navigate";
          this._persistAndSummarize();
        }
      });
    },

    // True while a connection is set up; false during a resume reconnect.
    canSend() {
      return this.state === "active" && this._setupComplete;
    },

    sendFrame() {
      if (!this.canSend()) return;
      const b64 = this._captureFrameJpegB64();
      if (!b64) return;
      this._ws.send(JSON.stringify({
        realtimeInput: {
          video: { mimeType: "image/jpeg", data: b64 },
        },
      }));
      this._frameCount += 1;
      this._flashFrameSent();
      this._resetInactivity();
    },

    // Typed question: one complete user turn. clientContent keeps it in order
    // with the context already sent, and turnComplete makes Gemini answer now
    // (it also interrupts a reply in progress, like speaking over it would).
    sendText() {
      const text = this.draftText.trim();
      if (!text || !this.canSend()) return;
      this._closeBubble();
      this.transcript.push({ role: "user", text, ts: Date.now(), kind: "typed" });
      this._ws.send(JSON.stringify({
        clientContent: { turns: [{ role: "user", parts: [{ text }] }], turnComplete: true },
      }));
      this.draftText = "";
      this._resetInactivity();
    },

    // Pulse the player's camera-flash overlay to confirm the current frame was
    // captured + sent to Gemini. Re-trigger by removing the class, forcing a
    // reflow, then re-adding — so rapid successive sends each flash.
    _flashFrameSent() {
      const el = document.querySelector(".frame-flash");
      if (!el) return;
      el.classList.remove("flash");
      void el.offsetWidth; // force reflow so the animation restarts
      el.classList.add("flash");
    },

    _captureFrameJpegB64() {
      const v = document.querySelector("video.video");
      if (!v || !v.videoWidth) return null;
      const maxW = 1280, maxH = 720;
      const scale = Math.min(1, maxW / v.videoWidth, maxH / v.videoHeight);
      const w = Math.round(v.videoWidth * scale);
      const h = Math.round(v.videoHeight * scale);
      let canvas = this._frameCanvas;
      if (!canvas) {
        canvas = this._frameCanvas = document.createElement("canvas");
      }
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h;
      }
      canvas.getContext("2d").drawImage(v, 0, 0, w, h);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
      return dataUrl.substring(dataUrl.indexOf(",") + 1);
    },

    async _fetchConfig() {
      const r = await fetch(`/api/live/session-config?clip_id=${clipId}`);
      if (!r.ok) throw new Error(`Couldn't start Live (server answered ${r.status}).`);
      return r.json();
    },

    _openWs() {
      console.log("[live] WSS opening:", this._wsUrl.replace(/(access_token|key)=[^&]+/, "$1=…"));
      const ws = new WebSocket(this._wsUrl);
      this._ws = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        // Send ONLY the setup frame and wait. The session does not become
        // active and no content/audio is sent until the server replies with
        // `setupComplete` (handled in _onSetupComplete). Sending content here
        // was the flakiness race. setup_payload is already the pure setup —
        // the initial turn arrives as its own field (config.initial_context_turn).
        // A reconnect adds the resumption handle so Gemini keeps the dialog.
        const setup = this._resumeHandle
          ? { ...this._setupPayload, sessionResumption: { handle: this._resumeHandle } }
          : this._setupPayload;
        ws.send(JSON.stringify({ setup }));
      };
      ws.onmessage = (evt) => {
        // Live API actually delivers JSON over BINARY WebSocket frames
        // (arrayBuffer because we set ws.binaryType="arraybuffer"). Decode
        // here so the rest of the pipeline sees text.
        const text = typeof evt.data === "string"
          ? evt.data
          : new TextDecoder("utf-8").decode(new Uint8Array(evt.data));
        this._onWsMessage({ data: text });
      };
      ws.onerror = (e) => console.warn("[live] WSS error event:", e);
      ws.onclose = (e) => this._onWsClose(e);
    },

    // Detach handlers first so closing on purpose never reads as a drop.
    _closeWs() {
      const ws = this._ws;
      if (!ws) return;
      ws.onclose = ws.onmessage = ws.onerror = null;
      try { ws.close(); } catch {}
    },

    _onWsClose(e) {
      console.warn("[live] WSS close:", "code=" + e.code, "reason=" + JSON.stringify(e.reason || ""));
      if (this._resume()) return;
      this._fail(`Live connection closed (code ${e.code}${e.reason ? ": " + e.reason : ""}).`);
      if (this.state === "active") {
        this.close("error");
      } else if (this.state === "connecting") {
        // Closed before setupComplete (e.g. a rejected setup): show the error
        // rather than a stuck spinner.
        this.state = "idle";
        this._teardown();
      }
    },

    // Google ends every connection after ~10 minutes (it warns with goAway
    // first). Reopen with the latest resumption handle; the session, its
    // transcript and the mic stay as they are. Returns false when the session
    // can't resume (no handle yet, or out of attempts).
    _resume() {
      if (this.state !== "active" || !this._resumeHandle || this._resumes >= LIVE_MAX_RESUMES) {
        return false;
      }
      this._resumes += 1;
      this._setupComplete = false;
      this._closeWs();
      this._openWs();
      return true;
    },

    _sendInitialClientContent(initialTurn) {
      // Append a short Czech greeting cue so the model speaks immediately on
      // session start — useful for verifying voice output is wired before
      // the operator says anything. Set turnComplete: true so Gemini
      // responds right away instead of waiting for more user content.
      const parts = [...(initialTurn?.parts || [])];
      const frame = this._captureFrameJpegB64();
      if (frame) parts.push({ inlineData: { mimeType: "image/jpeg", data: frame } });
      parts.push({ text:
        'Pozdrav mě prosím krátce česky („Dobrý den") a v jedné větě popiš, '
        + 'co vidíš na aktuálním snímku. Pak počkej na moji další otázku.' });
      const msg = { clientContent: { turns: [{ role: "user", parts }], turnComplete: true } };
      try { this._ws.send(JSON.stringify(msg)); } catch {}
      this._frameCount += frame ? 1 : 0;
      if (frame) this._flashFrameSent(); // conversation start sent the current frame
    },

    _tickElapsed() {
      const s = Math.floor((Date.now() - this._startedAt) / 1000);
      this.elapsedFmt = window.fmtTimecode(s);
    },

    _onWsMessage(evt) {
      let msg;
      try { msg = JSON.parse(evt.data); } catch { return; }
      if (msg.setupComplete) this._onSetupComplete();
      if (msg.serverContent) this._handleServerContent(msg.serverContent);
      if (msg.toolCall) this._handleToolCall(msg.toolCall);
      if (msg.usageMetadata) this._addUsage(msg.usageMetadata);
      const upd = msg.sessionResumptionUpdate;
      if (upd?.resumable && upd.newHandle) this._resumeHandle = upd.newHandle;
      if (msg.goAway && this._resume()) return;
      this._resetInactivity();
    },

    // Sum the usageMetadata Gemini sends on Live messages so the session's
    // token spend can be recorded server-side (the audio never passes through
    // our server). Numeric counts add up; *TokensDetails lists add up per
    // modality. The last message's own copy is kept too, so the record stays
    // correct whichever way Gemini meant the counts (per turn or running).
    _addUsage(u) {
      const acc = this._usage.summed;
      for (const [k, v] of Object.entries(u)) {
        if (typeof v === "number") acc[k] = (acc[k] || 0) + v;
        else if (Array.isArray(v)) {
          const byMod = Object.fromEntries((acc[k] || []).map(d => [d.modality, d]));
          for (const d of v) {
            const cur = byMod[d.modality] || (byMod[d.modality] = { modality: d.modality, tokenCount: 0 });
            cur.tokenCount += d.tokenCount || 0;
          }
          acc[k] = Object.values(byMod);
        }
      }
      this._usage.last = u;
      this._usage.messages += 1;
    },

    _onSetupComplete() {
      // Server ACKed the setup frame — only NOW is it safe to send content and
      // let mic audio flow. Guard against duplicate ACKs / late arrival. A
      // resumed connection already has its context, so it only reopens the gate.
      if (this._setupComplete) return;
      if (this.state === "active") { this._setupComplete = true; return; }
      if (this.state !== "connecting") return;
      this._setupComplete = true;
      this._sendInitialClientContent(this._initialTurn);
      this.state = "active";
      this._startedAt = Date.now();
      this._elapsedTimer = setInterval(() => this._tickElapsed(), 1000);
      this._resetInactivity();
    },

    _handleServerContent(sc) {
      // Barge-in: when the operator interrupts, Gemini sends interrupted:true
      // and stops generating. Flush already-queued playback so the model goes
      // quiet immediately instead of talking over the operator.
      if (sc.interrupted) this._flushPlayback();
      // Gemini streams transcription as many tiny deltas. Appending consecutive
      // same-role speech into ONE bubble (rather than one push per delta) is what
      // turns "one word per line" into a readable dialog. A turn boundary closes
      // the open bubble so the next delta starts a fresh one.
      if (sc.outputTranscription?.text) this._appendSpeech("model", sc.outputTranscription.text);
      if (sc.inputTranscription?.text) this._appendSpeech("user", sc.inputTranscription.text);
      if (sc.turnComplete || sc.generationComplete) this._closeBubble();
      // One event can carry several parts (3.x models); play every audio part.
      const turns = sc.modelTurn?.parts || [];
      for (const part of turns) {
        if (part.inlineData && part.inlineData.mimeType?.startsWith("audio/pcm")) {
          this._enqueueAudio(part.inlineData.data);
        }
      }
    },

    // Append a transcription delta to the open same-role bubble, or start a new
    // one. Mutating last.text is reactive (Alpine proxies array elements), so
    // the on-screen bubble grows in place instead of spawning a line per word.
    _appendSpeech(role, text) {
      const last = this.transcript[this.transcript.length - 1];
      if (last && last.role === role && last.kind === "speech" && !last.done) {
        last.text += text;
      } else {
        this.transcript.push({ role, text, ts: Date.now(), kind: "speech", done: false });
      }
    },

    _closeBubble() {
      const last = this.transcript[this.transcript.length - 1];
      if (last && last.kind === "speech") last.done = true;
    },

    // The header bar disappears once the session ends, so errors go to a toast.
    _fail(message) {
      Alpine.store("toast").push(message, { level: "error" });
    },

    _note(text, kind = "note") {
      this.transcript.push({ role: "system", text, ts: Date.now(), kind });
    },

    _handleToolCall(tc) {
      const calls = tc.functionCalls || [];
      for (const c of calls) {
        if (c.name === "end_session") {
          this._note(`Ended: ${c.args?.reason || ""}`, "function_call");
          this.close("voice_stop");
        }
      }
    },

    _resetInactivity() {
      clearTimeout(this._inactivityTimer);
      const ms = (this.inactivityS || 60) * 1000;
      this._inactivityTimer = setTimeout(() => {
        if (this.state === "active") this.close("inactivity");
      }, ms);
    },

    async _persistAndSummarize() {
      if (!this.sessionId) return;
      const body = {
        end_reason: this._endReason || "user_stop",
        transcript: this.transcript,
        frame_count: this._frameCount,
        usage: this._usage.messages ? this._usage : null,
      };
      try {
        const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
        if (navigator.sendBeacon) {
          navigator.sendBeacon(`/api/live/sessions/${this.sessionId}/transcript`, blob);
        } else {
          await fetch(`/api/live/sessions/${this.sessionId}/transcript`,
                      { method: "POST", body: blob });
        }
        fetch(`/api/live/sessions/${this.sessionId}/summarize`,
              { method: "POST" }).catch(err => {
          Alpine.store('toast').push(
            `Session summary failed: ${err.message || String(err)}`,
            { level: 'error' },
          );
        });
      } catch {}
    },

    async _teardown() {
      await this._closeAudio();
      this._ws = null;
      this._setupComplete = false;
      this.micOn = false;
      clearInterval(this._elapsedTimer);
      clearTimeout(this._inactivityTimer);
    },
  });
}

window.liveSession = liveSession;
