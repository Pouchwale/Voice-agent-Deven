/* global sampleRate, registerProcessor, AudioWorkletProcessor */
/**
 * Mic capture worklet for the Test Agent (browser call).
 * Downsamples the context-rate float input to 16 kHz mono PCM16 (box-filter average = cheap anti-alias)
 * and posts one frame every `frameMs` (default 40 ms) to the main thread as a transferable ArrayBuffer,
 * together with the RMS level of that frame.
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = (options && options.processorOptions) || {};
    this.targetRate = o.targetSampleRate || 16000;
    this.frameSamples = Math.max(160, Math.round((this.targetRate * (o.frameMs || 40)) / 1000));
    this.ratio = sampleRate / this.targetRate;
    this.buf = new Int16Array(this.frameSamples);
    this.len = 0;
    this.pos = 0;
    this.acc = 0;
    this.accCount = 0;
    this.levelSum = 0;
    this.levelN = 0;
    this.muted = false;
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === "mute") this.muted = !!e.data.muted;
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const left = input[0];
    const right = input.length > 1 ? input[1] : null;
    for (let i = 0; i < left.length; i++) {
      const s = right ? (left[i] + right[i]) * 0.5 : left[i];
      this.levelSum += s * s;
      this.levelN += 1;
      this.acc += s;
      this.accCount += 1;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        let v = this.muted ? 0 : this.acc / this.accCount;
        this.acc = 0;
        this.accCount = 0;
        if (v > 1) v = 1;
        else if (v < -1) v = -1;
        this.buf[this.len++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        if (this.len >= this.frameSamples) {
          const out = this.buf;
          this.buf = new Int16Array(this.frameSamples);
          this.len = 0;
          const rms = Math.sqrt(this.levelSum / Math.max(1, this.levelN));
          this.levelSum = 0;
          this.levelN = 0;
          this.port.postMessage({ pcm: out.buffer, level: this.muted ? 0 : rms }, [out.buffer]);
        }
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
