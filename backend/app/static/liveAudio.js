// Audio half of the Gemini Live clip assistant: mic capture (16 kHz PCM up)
// and playback of Gemini's 24 kHz PCM replies. Mixed into liveSession() so
// the session file stays about the conversation; see liveSession.js.

function liveAudio() {
  return {
    _audioCtxIn: null,
    _audioCtxOut: null,
    _workletNode: null,
    _mediaStream: null,
    _playing: [],                   // scheduled output BufferSources (for barge-in flush)
    _nextPlayAt: 0,

    // Opens the mic and streams it once the session is ready. Returns false
    // (instead of throwing) when there is no mic or the browser blocks it, so
    // the session carries on with typed questions only.
    async _openMic() {
      if (!navigator.mediaDevices?.getUserMedia) return false;
      try {
        this._mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
          video: false,
        });
      } catch (e) {
        console.warn("[live] no microphone, typing only:", e);
        return false;
      }
      this._audioCtxIn = new AudioContext();
      await this._audioCtxIn.audioWorklet.addModule("/static/audio-worklet-recorder.js");
      const src = this._audioCtxIn.createMediaStreamSource(this._mediaStream);
      this._workletNode = new AudioWorkletNode(this._audioCtxIn, "recorder-processor");
      src.connect(this._workletNode);
      // Do NOT connect to destination — we don't want to hear ourselves.
      this._workletNode.port.onmessage = (e) => this._onCaptureChunk(e.data);
      this._workletNode.port.postMessage({ type: "start" });
      return true;
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

    async _closeAudio() {
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
    },
  };
}

window.liveAudio = liveAudio;
