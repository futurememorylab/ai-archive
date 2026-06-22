// Alpine component for the Gemini Live clip assistant.
// Composes into clip_detail.html's existing x-data alongside player() and clipAnnotate().
// Audio bytes flow browser ↔ Google directly via WSS; this component only
// talks to our backend for token minting and post-session persistence.

function liveSession(clipId, config) {
  return {
    // ── state ────────────────────────────────────────────────────────────
    state: "idle",                  // idle | connecting | active | closing
    transcript: [],                 // [{role, text, ts, kind}]
    elapsedFmt: "0:00",
    expanded: false,
    error: null,
    sessionId: null,
    inactivityS: (config && config.inactivityS) || 60,

    // ── internals ────────────────────────────────────────────────────────
    _ws: null,
    _audioCtxIn: null,
    _audioCtxOut: null,
    _workletNode: null,
    _mediaStream: null,
    _frameCount: 0,
    _startedAt: 0,
    _elapsedTimer: null,
    _inactivityTimer: null,
    _endReason: null,
    _setupPayload: null,
    _initialTurn: null,
    _setupComplete: false,          // gate: no content/audio until server ACKs setup
    _playing: [],                   // scheduled output BufferSources (for barge-in flush)
    _nextPlayAt: 0,

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
      this.error = null;
      this.transcript = [];
      this._frameCount = 0;
      this._setupComplete = false;
      this._endReason = null;
      this.state = "connecting";
      try {
        const config = await this._fetchConfig();
        this.sessionId = config.session_id;
        this._setupPayload = config.setup_payload;
        this._initialTurn = config.initial_context_turn;
        await this._openMic();
        this._openWs(config.ws_url);
      } catch (e) {
        this.error = String(e);
        this.state = "idle";
        await this._teardown();
      }
    },

    async close(reason) {
      if (this.state === "idle" || this.state === "closing") return;
      this.state = "closing";
      this._endReason = reason || this._endReason || "user_stop";
      try { if (this._ws && this._ws.readyState === 1) this._ws.close(); } catch {}
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

    sendFrame() {
      if (this.state !== "active" || !this._ws) return;
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

    // ── helpers (stubs filled in later tasks) ────────────────────────────
    async _fetchConfig() {
      const r = await fetch(`/api/live/session-config?clip_id=${clipId}`);
      if (!r.ok) throw new Error(`session-config HTTP ${r.status}`);
      return r.json();
    },

    async _openMic() {
      this._mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      this._audioCtxIn = new AudioContext();
      await this._audioCtxIn.audioWorklet.addModule("/static/audio-worklet-recorder.js");
      const src = this._audioCtxIn.createMediaStreamSource(this._mediaStream);
      this._workletNode = new AudioWorkletNode(this._audioCtxIn, "recorder-processor");
      src.connect(this._workletNode);
      // Do NOT connect to destination — we don't want to hear ourselves.
      this._workletNode.port.onmessage = (e) => this._onCaptureChunk(e.data);
      this._workletNode.port.postMessage({ type: "start" });
    },

    _onCaptureChunk(arrayBuffer) {
      // Drop chunks until the WSS is open AND the server has ACKed `setup`.
      // Streaming realtimeInput before `setupComplete` is the race that made
      // sessions flaky (Gemini closes with 1007/1008). See docs/decisions.md.
      if (!this._ws || this._ws.readyState !== 1 || !this._setupComplete) return;
      // Live API current shape: realtimeInput.audio is a single Blob,
      // not the deprecated mediaChunks array. Same for .video / .text.
      const b64 = this._b64FromBuffer(arrayBuffer);
      this._ws.send(JSON.stringify({
        realtimeInput: {
          audio: { mimeType: "audio/pcm;rate=16000", data: b64 },
        },
      }));
      this._resetInactivity();
    },

    _b64FromBuffer(buf) {
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      return btoa(bin);
    },

    _b64ToBytes(b64) {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return bytes;
    },

    _openWs(url) {
      console.log("[live] WSS opening:", url.replace(/key=[^&]+/, "key=…"));
      const ws = new WebSocket(url);
      this._ws = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        // Send ONLY the setup frame and wait. The session does not become
        // active and no content/audio is sent until the server replies with
        // `setupComplete` (handled in _onSetupComplete). Sending content here
        // was the flakiness race. setup_payload is already the pure setup —
        // the initial turn arrives as its own field (config.initial_context_turn).
        console.log("[live] WSS open. Sending setup:", JSON.stringify(this._setupPayload, null, 2));
        ws.send(JSON.stringify({ setup: this._setupPayload }));
      };
      ws.onmessage = (evt) => {
        // Live API actually delivers JSON over BINARY WebSocket frames
        // (arrayBuffer because we set ws.binaryType="arraybuffer"). Decode
        // here so the rest of the pipeline sees text.
        const text = typeof evt.data === "string"
          ? evt.data
          : new TextDecoder("utf-8").decode(new Uint8Array(evt.data));
        console.log("[live] WSS msg:", text.length > 600
          ? text.slice(0, 600) + "…(+" + (text.length - 600) + " bytes)"
          : text);
        this._onWsMessage({ data: text });
      };
      ws.onerror = (e) => {
        console.warn("[live] WSS error event:", e);
        this._endReason = "error";
        this.error = "WebSocket error — viz konzole.";
      };
      ws.onclose = (e) => {
        console.warn("[live] WSS close:",
          "code=" + e.code,
          "wasClean=" + e.wasClean,
          "reason=" + JSON.stringify(e.reason || ""));
        if (!this.error && !e.wasClean) {
          this.error = `WSS uzavřeno (code=${e.code}${e.reason ? ", " + e.reason : ""})`;
        }
        if (this.state === "active") {
          this.close(this._endReason || "error");
        } else if (this.state === "connecting") {
          // Closed while still connecting (before onopen, or after setup but
          // before setupComplete — e.g. a rejected setup). Leave connecting so
          // the user sees the error rather than a stuck spinner.
          this.state = "idle";
          this._teardown();
        }
      };
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
      this._resetInactivity();
    },

    _onSetupComplete() {
      // Server ACKed the setup frame — only NOW is it safe to send content and
      // let mic audio flow. Guard against duplicate ACKs / late arrival.
      if (this._setupComplete || this.state !== "connecting") return;
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
      if (sc.turnComplete || sc.generationComplete) {
        const last = this.transcript[this.transcript.length - 1];
        if (last && last.kind === "speech") last.done = true;
      }
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

    // Create / resume / prime the OUTPUT AudioContext. Called from start()
    // inside the user gesture (the only context in which WebKit will actually
    // start it), and defensively from _enqueueAudio. The context is REUSED
    // across sessions — teardown suspends it, it is never closed — so a 2nd
    // session inherits an already-unlocked context instead of a fresh suspended
    // one. Gemini streams 24 kHz PCM, hence the fixed sampleRate.
    _ensureOutputAudio() {
      if (!this._audioCtxOut) {
        this._audioCtxOut = new AudioContext({ sampleRate: 24000 });
      }
      if (this._audioCtxOut.state === "suspended") this._audioCtxOut.resume();
      // Prime with a 1-sample silent buffer so older WebKit unlocks playback.
      try {
        const b = this._audioCtxOut.createBuffer(1, 1, 24000);
        const s = this._audioCtxOut.createBufferSource();
        s.buffer = b;
        s.connect(this._audioCtxOut.destination);
        s.start(0);
      } catch {}
      return this._audioCtxOut;
    },

    _enqueueAudio(b64) {
      // Defensive: start() already unlocked this within the gesture; resume
      // again in case the context auto-suspended between sessions.
      if (!this._audioCtxOut || this._audioCtxOut.state !== "running") {
        this._ensureOutputAudio();
      }
      const bytes = this._b64ToBytes(b64);
      const view = new DataView(bytes.buffer);
      const sampleCount = bytes.length / 2;
      const buf = this._audioCtxOut.createBuffer(1, sampleCount, 24000);
      const channel = buf.getChannelData(0);
      for (let i = 0; i < sampleCount; i++) {
        channel[i] = view.getInt16(i * 2, true) / 0x8000;
      }
      const node = this._audioCtxOut.createBufferSource();
      node.buffer = buf;
      node.connect(this._audioCtxOut.destination);
      const startAt = Math.max(this._audioCtxOut.currentTime, this._nextPlayAt);
      node.start(startAt);
      this._nextPlayAt = startAt + buf.duration;
      this._playing.push(node);
      node.onended = () => {
        const i = this._playing.indexOf(node);
        if (i >= 0) this._playing.splice(i, 1);
      };
    },

    _flushPlayback() {
      for (const node of this._playing) { try { node.stop(); } catch {} }
      this._playing = [];
      this._nextPlayAt = 0;
    },

    _handleToolCall(tc) {
      const calls = tc.functionCalls || [];
      for (const c of calls) {
        if (c.name === "end_session") {
          this.transcript.push({ role: "system", text: `Konec: ${c.args?.reason || ""}`, ts: Date.now(), kind: "function_call" });
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
      this._flushPlayback();
      try { this._workletNode?.disconnect(); } catch {}
      try { if (this._mediaStream) this._mediaStream.getTracks().forEach(t => t.stop()); } catch {}
      try { await this._audioCtxIn?.close(); } catch {}
      // Do NOT close the output context — a closed AudioContext can never be
      // reused, and re-creating one in the next session's WS callback is exactly
      // what left Safari silent. Suspend it (frees the audio device) and keep
      // the reference so the next start() can resume the already-unlocked one.
      try { await this._audioCtxOut?.suspend(); } catch {}
      this._workletNode = null;
      this._mediaStream = null;
      this._audioCtxIn = null;
      this._ws = null;
      this._setupComplete = false;
      clearInterval(this._elapsedTimer);
      clearTimeout(this._inactivityTimer);
    },
  };
}

window.liveSession = liveSession;
