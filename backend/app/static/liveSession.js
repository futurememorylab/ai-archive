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

    sendFrame() { /* implemented in Task 20 */ },

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
      // Gemini Live expects { realtimeInput: { mediaChunks: [{ mimeType, data }] } }
      // mediaChunks is base64 PCM at the rate declared in setup (16000).
      const b64 = this._b64FromBuffer(arrayBuffer);
      this._ws.send(JSON.stringify({
        realtimeInput: {
          mediaChunks: [{ mimeType: "audio/pcm;rate=16000", data: b64 }],
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

    _openWs(url) { /* Task 19 */ },
    _onWsMessage(evt) { /* Task 19 + 21 */ },
    _resetInactivity() { /* Task 22 */ },

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
