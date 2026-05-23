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
    _searchCalls: 0,
    _startedAt: 0,
    _elapsedTimer: null,
    _inactivityTimer: null,
    _endReason: null,
    _setupPayload: null,

    // ── public API ───────────────────────────────────────────────────────
    async start() {
      if (this.state !== "idle") return;
      this.error = null;
      this.transcript = [];
      this._frameCount = 0;
      this._searchCalls = 0;
      this._endReason = null;
      this.state = "connecting";
      try {
        const config = await this._fetchConfig();
        this.sessionId = config.session_id;
        this._setupPayload = config.setup_payload;
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
      this._resetInactivity();
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
      if (!this._ws || this._ws.readyState !== 1) return;
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

    _openWs(url) {
      console.log("[live] WSS opening:", url.replace(/key=[^&]+/, "key=…"));
      const ws = new WebSocket(url);
      this._ws = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        const setup = { ...this._setupPayload };
        const initial = setup.initial_context_turn;
        delete setup.initial_context_turn;
        console.log("[live] WSS open. Sending setup:", JSON.stringify(setup, null, 2));
        ws.send(JSON.stringify({ setup }));
        this._sendInitialClientContent(initial);
        this.state = "active";
        this._startedAt = Date.now();
        this._elapsedTimer = setInterval(() => this._tickElapsed(), 1000);
        this._resetInactivity();
      };
      ws.onmessage = (evt) => {
        // Log all inbound frames so we can see error messages Google sends
        // before closing the socket. Binary frames are rare here (audio
        // arrives as base64 inside JSON), but log size if so.
        if (typeof evt.data === "string") {
          console.log("[live] WSS msg:", evt.data.length > 800
            ? evt.data.slice(0, 800) + "…(+" + (evt.data.length - 800) + " bytes)"
            : evt.data);
        } else {
          console.log("[live] WSS msg (binary):", evt.data.byteLength, "bytes");
        }
        this._onWsMessage(evt);
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
          // Failure before onopen — make sure we leave connecting state so
          // the user sees the error rather than a stuck spinner.
          this.state = "idle";
          this._teardown();
        }
      };
    },

    _sendInitialClientContent(initialTurn) {
      const parts = [...(initialTurn?.parts || [])];
      const frame = this._captureFrameJpegB64();
      if (frame) parts.push({ inlineData: { mimeType: "image/jpeg", data: frame } });
      const msg = { clientContent: { turns: [{ role: "user", parts }], turnComplete: false } };
      try { this._ws.send(JSON.stringify(msg)); } catch {}
      this._frameCount += frame ? 1 : 0;
    },

    _tickElapsed() {
      const s = Math.floor((Date.now() - this._startedAt) / 1000);
      const mm = Math.floor(s / 60);
      const ss = s % 60;
      this.elapsedFmt = `${mm}:${ss.toString().padStart(2, "0")}`;
    },

    _onWsMessage(evt) {
      let msg;
      try { msg = JSON.parse(evt.data); } catch { return; }
      if (msg.serverContent) this._handleServerContent(msg.serverContent);
      if (msg.toolCall) this._handleToolCall(msg.toolCall);
      this._resetInactivity();
    },

    _handleServerContent(sc) {
      if (sc.outputTranscription?.text) {
        this.transcript.push({ role: "model", text: sc.outputTranscription.text, ts: Date.now(), kind: "speech" });
      }
      if (sc.inputTranscription?.text) {
        this.transcript.push({ role: "user", text: sc.inputTranscription.text, ts: Date.now(), kind: "speech" });
      }
      const turns = sc.modelTurn?.parts || [];
      for (const part of turns) {
        if (part.inlineData && part.inlineData.mimeType?.startsWith("audio/pcm")) {
          this._enqueueAudio(part.inlineData.data);
        }
      }
    },

    _enqueueAudio(b64) {
      if (!this._audioCtxOut) {
        this._audioCtxOut = new AudioContext({ sampleRate: 24000 });
      }
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
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
      const startAt = Math.max(this._audioCtxOut.currentTime, (this._nextPlayAt || 0));
      node.start(startAt);
      this._nextPlayAt = startAt + buf.duration;
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
        search_calls: this._searchCalls,
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
              { method: "POST" }).catch(() => {});
      } catch {}
    },

    async _teardown() {
      try { this._workletNode?.disconnect(); } catch {}
      try { if (this._mediaStream) this._mediaStream.getTracks().forEach(t => t.stop()); } catch {}
      try { await this._audioCtxIn?.close(); } catch {}
      try { await this._audioCtxOut?.close(); } catch {}
      this._workletNode = null;
      this._mediaStream = null;
      this._audioCtxIn = null;
      this._audioCtxOut = null;
      this._ws = null;
      clearInterval(this._elapsedTimer);
      clearTimeout(this._inactivityTimer);
    },
  };
}

window.liveSession = liveSession;
