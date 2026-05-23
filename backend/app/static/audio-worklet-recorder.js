// AudioWorkletProcessor: capture mono Float32 frames from the AudioContext,
// downsample to 16 kHz, convert to Int16 PCM, post 100 ms chunks back to
// the main thread.
//
// The main thread sends `{type:"start"}` and `{type:"stop"}` messages.

const TARGET_SR = 16000;
const CHUNK_MS = 100;

class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.running = false;
    this.acc = [];               // Float32 samples accumulated at native rate
    this.nativeSR = sampleRate;  // global in AudioWorkletGlobalScope
    this.samplesPerChunk = Math.round(this.nativeSR * (CHUNK_MS / 1000));
    this.port.onmessage = (e) => {
      const t = e.data?.type;
      if (t === "start") { this.running = true; }
      else if (t === "stop") { this.running = false; this.acc = []; }
    };
  }

  // Linear downsample to 16 kHz.
  _downsample(input) {
    if (this.nativeSR === TARGET_SR) return input;
    const ratio = this.nativeSR / TARGET_SR;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      out[i] = input[Math.floor(i * ratio)];
    }
    return out;
  }

  _floatToPCM16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return out;
  }

  process(inputs) {
    if (!this.running) return true;
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const mono = input[0];
    for (let i = 0; i < mono.length; i++) this.acc.push(mono[i]);
    while (this.acc.length >= this.samplesPerChunk) {
      const slice = this.acc.splice(0, this.samplesPerChunk);
      const down = this._downsample(Float32Array.from(slice));
      const pcm = this._floatToPCM16(down);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
