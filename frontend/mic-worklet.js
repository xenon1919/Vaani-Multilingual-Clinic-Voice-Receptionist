// Captures mic audio (Float32, native AudioWorklet rate) and posts it to the
// main thread as Int16 PCM, batched into ~32ms chunks to limit message
// overhead.
class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._chunks = [];
    this._bufferedSamples = 0;
    this._chunkSamples = 512; // ~32ms at 16kHz
  }

  process(inputs) {
    const input = inputs[0];
    if (input.length === 0) return true;

    const channelData = input[0];
    this._chunks.push(channelData.slice());
    this._bufferedSamples += channelData.length;

    if (this._bufferedSamples >= this._chunkSamples) {
      const merged = new Float32Array(this._bufferedSamples);
      let offset = 0;
      for (const chunk of this._chunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
      }
      this.port.postMessage(floatTo16BitPCM(merged));
      this._chunks = [];
      this._bufferedSamples = 0;
    }
    return true;
  }
}

function floatTo16BitPCM(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return int16.buffer;
}

registerProcessor('mic-capture-processor', MicCaptureProcessor);
