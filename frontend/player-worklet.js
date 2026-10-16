// Plays queued Float32 sample chunks back-to-back. A "clear" message wipes
// everything queued instantly -- this is what makes caller interruption
// (barge-in) actually stop the agent's voice without delay.
class PlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._queue = [];
    this._readOffset = 0;
    this.port.onmessage = (event) => {
      if (event.data.type === 'clear') {
        this._queue = [];
        this._readOffset = 0;
      } else if (event.data.type === 'push') {
        this._queue.push(event.data.samples);
      }
    };
  }

  process(_inputs, outputs) {
    const output = outputs[0][0];
    for (let i = 0; i < output.length; i++) {
      if (this._queue.length === 0) {
        output[i] = 0;
        continue;
      }
      const current = this._queue[0];
      output[i] = current[this._readOffset++];
      if (this._readOffset >= current.length) {
        this._queue.shift();
        this._readOffset = 0;
      }
    }
    return true;
  }
}

registerProcessor('player-processor', PlayerProcessor);
