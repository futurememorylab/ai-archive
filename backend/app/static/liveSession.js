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
    draftText: "",                  // the question being typed
    confirmEnd: false,              // the "End chat?" dialog is open
    sessionId: null,

    // ── public API ───────────────────────────────────────────────────────
    async start() {
      if (this.state !== "idle") return;
      this.transcript = [];
      this.draftText = "";
      this.sessionId = null;
      this.state = "starting";
      const done = await this._answer("/api/live/sessions",
                                      { clip_id: clipId, frame_b64: this._frame() });
      if (!done) { this.state = "idle"; return; }
      this.sessionId = done.session_id;
      this.state = "active";
    },

    // Ends the chat and closes the dialog; the chat stays in the clip's
    // Chats tab (the page reloads that list when this resolves).
    async close(reason) {
      this.confirmEnd = false;
      if (this.state !== "active") return;
      this.state = "closing";
      const ended = await this._post(`/api/live/sessions/${this.sessionId}/end`,
                                     { end_reason: reason || "user_stop" });
      this._post(`/api/live/sessions/${this.sessionId}/summarize`, {});
      this.transcript = [];
      this.state = "idle";
      if (ended) {
        Alpine.store("toast").push("Chat saved. Find it in the Chats tab on the right.",
                                   { level: "success" });
      }
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
    // A question whose answer failed isn't saved on the server, so it leaves
    // the transcript too; a typed one goes back in the box to send again.
    async _ask({ kind, text }) {
      const frame = this._frame();
      this.transcript.push({ role: "user", text, ts: Date.now(), kind });
      const question = this.transcript[this.transcript.length - 1];
      const done = await this._answer(`/api/live/sessions/${this.sessionId}/turns`,
                                      { kind, text, frame_b64: frame });
      if (done) return;
      this._drop(question);
      if (kind === "typed" && !this.draftText) this.draftText = text;
    },

    // Remove a line if it is still in this chat's transcript (a new chat may
    // have replaced the list meanwhile).
    _drop(line) {
      const i = this.transcript.indexOf(line);
      if (i >= 0) this.transcript.splice(i, 1);
    },

    // POST a question and show Gemini's answer as it streams in: a status
    // line while it thinks, then the text, then the searches it ran. Returns
    // the server's "done" event, or null (toasted) when there's no answer.
    async _answer(url, body) {
      this.transcript.push({ role: "model", text: "", status: "Thinking…", pending: true });
      const line = this.transcript[this.transcript.length - 1];  // Alpine's reactive copy
      this.busy = true;
      try {
        const done = await this._stream(url, body, (event) => {
          if (event.type === "thinking") line.status = `Thinking: ${event.text}…`;
          if (event.type === "text") line.text += event.text;
        });
        Object.assign(line, done.reply, { pending: false });
        return done;
      } catch (e) {
        this._drop(line);
        Alpine.store("toast").push(`Chat: ${e.message || e}`, { level: "error" });
        return null;
      } finally {
        this.busy = false;
      }
    },

    // Read the server's NDJSON lines, passing each progress event to
    // onEvent; resolves with the "done" event, throws on "error".
    async _stream(url, body, onEvent) {
      const r = await this._fetch(url, body);
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffered = "";
      for (;;) {
        const { value, done } = await reader.read();
        buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = buffered.split("\n");
        buffered = done ? "" : lines.pop();
        for (const raw of lines) {
          if (!raw.trim()) continue;
          const event = JSON.parse(raw);
          if (event.type === "error") throw new Error(event.detail);
          if (event.type === "done") return event;
          onEvent(event);
        }
        if (done) throw new Error("The answer was cut off. Try asking again.");
      }
    },

    // POST JSON; resolves with the ok response, throws the server's reason.
    async _fetch(url, body) {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.ok) return r;
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || `The server answered ${r.status}.`);
    },

    // POST JSON; on failure, toast the server's reason and return null.
    async _post(url, body) {
      try {
        return await (await this._fetch(url, body)).json().catch(() => ({}));
      } catch (e) {
        Alpine.store("toast").push(`Chat: ${e.message || e}`, { level: "error" });
        return null;
      }
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
