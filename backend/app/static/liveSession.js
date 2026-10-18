// Alpine component for the clip chat with Gemini.
// Composes into clip_detail.html's existing x-data alongside player() and clipAnnotate().
// Every question goes to our backend, which asks Gemini on Vertex and keeps
// the transcript; the browser holds no Google credentials. See ADR 0130.

function liveSession(clipId) {
  return {
    // ── state ────────────────────────────────────────────────────────────
    state: "idle",                  // idle | starting | active | closing
    busy: false,                    // a question is waiting for its answer
    transcript: [],                 // this chat: [{role, text, ts, kind}]
    earlier: [],                    // earlier chats on this page, kept on screen
    draftText: "",                  // the question being typed
    confirmEnd: false,              // the "End chat?" dialog is open
    sessionId: null,

    // ── public API ───────────────────────────────────────────────────────
    async start() {
      if (this.state !== "idle") return;
      if (this.transcript.length) {
        this.earlier.push(...this.transcript, { role: "system", text: "New chat", kind: "divider" });
      }
      this.transcript = [];
      this.draftText = "";
      this.sessionId = null;
      this.state = "starting";
      this.busy = true;
      const reply = await this._post("/api/live/sessions",
                                     { clip_id: clipId, frame_b64: this._frame() });
      this.busy = false;
      if (!reply) { this.state = "idle"; return; }
      this.sessionId = reply.session_id;
      this.transcript.push({ role: "model", text: reply.reply, ts: Date.now() });
      this.state = "active";
    },

    async close(reason) {
      this.confirmEnd = false;
      if (this.state !== "active") return;
      this.state = "closing";
      await this._post(`/api/live/sessions/${this.sessionId}/end`,
                       { end_reason: reason || "user_stop" });
      this._summarize();
      this.state = "idle";
    },

    init() {
      // End the chat when the page goes away mid-chat. A beacon survives the
      // unload; the transcript is already saved turn by turn.
      window.addEventListener("beforeunload", () => {
        if (this.state !== "active") return;
        const blob = new Blob([JSON.stringify({ end_reason: "navigate" })],
                              { type: "application/json" });
        navigator.sendBeacon(`/api/live/sessions/${this.sessionId}/end`, blob);
      });
    },

    canSend() {
      return this.state === "active" && !this.busy;
    },

    // A typed question goes with the frame on screen, so "what is this?"
    // means what the operator is looking at.
    sendText() {
      const text = this.draftText.trim();
      if (!text || !this.canSend()) return;
      this.draftText = "";
      this._ask({ kind: "typed", text });
    },

    sendFrame() {
      if (!this.canSend()) return;
      const v = document.querySelector("video.video");
      const at = window.fmtTimecode(Math.floor(v ? v.currentTime : 0));
      this._ask({ kind: "frame", text: `Frame at ${at}` });
    },

    // ── internals ────────────────────────────────────────────────────────
    async _ask({ kind, text }) {
      const frame = this._frame();
      this.transcript.push({ role: "user", text, ts: Date.now(), kind });
      this.busy = true;
      const reply = await this._post(`/api/live/sessions/${this.sessionId}/turns`,
                                     { kind, text, frame_b64: frame });
      this.busy = false;
      if (reply) this.transcript.push({ role: "model", text: reply.reply, ts: Date.now() });
    },

    // POST JSON; on failure, toast the server's reason and return null.
    async _post(url, body) {
      try {
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || `The server answered ${r.status}.`);
        return data;
      } catch (e) {
        Alpine.store("toast").push(`Chat: ${e.message || e}`, { level: "error" });
        return null;
      }
    },

    _summarize() {
      fetch(`/api/live/sessions/${this.sessionId}/summarize`, { method: "POST" }).catch(err => {
        Alpine.store("toast").push(
          `Chat summary failed: ${err.message || String(err)}`,
          { level: "error" },
        );
      });
    },

    // The frame on screen as base64 JPEG (null when the video has no picture
    // yet), with the player's camera-flash to show it was sent.
    _frame() {
      const b64 = this._captureFrameJpegB64();
      if (b64) this._flashFrameSent();
      return b64;
    },

    // Pulse the player's camera-flash overlay. Re-trigger by removing the
    // class, forcing a reflow, then re-adding, so rapid sends each flash.
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
  };
}

window.liveSession = liveSession;
