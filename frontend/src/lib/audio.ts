/** Browser audio helpers shared by the Test Agent panel and Listen-in. */

export function createAudioContext(options?: AudioContextOptions): AudioContext {
  const Ctor =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) throw new Error("This browser doesn't support Web Audio.");
  return new Ctor(options);
}

/** PCM16 little-endian mono -> Float32 [-1, 1]. Handles odd byte offsets (listen-in frames start at byte 1). */
export function pcm16ToFloat32(data: ArrayBuffer, byteOffset = 0): Float32Array {
  const n = Math.max(0, Math.floor((data.byteLength - byteOffset) / 2));
  const out = new Float32Array(n);
  if (byteOffset % 2 === 0) {
    const view = new Int16Array(data, byteOffset, n);
    for (let i = 0; i < n; i++) out[i] = view[i] / 32768;
  } else {
    const dv = new DataView(data);
    for (let i = 0; i < n; i++) out[i] = dv.getInt16(byteOffset + i * 2, true) / 32768;
  }
  return out;
}

/** RMS of an analyser's current time-domain window, scaled to a 0..1 "level". */
export function analyserLevel(analyser: AnalyserNode, scratch: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(scratch);
  let sum = 0;
  for (let i = 0; i < scratch.length; i++) sum += scratch[i] * scratch[i];
  const rms = Math.sqrt(sum / scratch.length);
  return Math.min(1, rms * 4);
}

/**
 * Sequential PCM player: each chunk is scheduled right after the previous one on its own playhead.
 * `clear()` stops everything queued immediately (barge-in).
 */
export class PcmPlayer {
  private ctx: AudioContext;
  private gain: GainNode;
  readonly analyser: AnalyserNode;
  private playhead = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private scratch: Float32Array<ArrayBuffer>;
  private leadTime: number;

  constructor(ctx: AudioContext, opts: { leadTime?: number; destination?: AudioNode; volume?: number } = {}) {
    this.ctx = ctx;
    this.leadTime = opts.leadTime ?? 0.06;
    this.gain = ctx.createGain();
    this.gain.gain.value = opts.volume ?? 1;
    this.analyser = ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.scratch = new Float32Array(this.analyser.fftSize);
    this.gain.connect(this.analyser);
    this.analyser.connect(opts.destination ?? ctx.destination);
  }

  enqueue(samples: Float32Array, sampleRate: number) {
    if (!samples.length || this.ctx.state === "closed") return;
    const buffer = this.ctx.createBuffer(1, samples.length, sampleRate);
    buffer.getChannelData(0).set(samples);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.gain);
    const now = this.ctx.currentTime;
    if (this.playhead < now + 0.01) this.playhead = now + this.leadTime;
    src.start(this.playhead);
    this.playhead += buffer.duration;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      src.disconnect();
    };
  }

  clear() {
    for (const s of this.sources) {
      try {
        s.onended = null;
        s.stop();
        s.disconnect();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.playhead = 0;
  }

  setVolume(v: number) {
    this.gain.gain.setTargetAtTime(v, this.ctx.currentTime, 0.02);
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  level(): number {
    if (!this.playing) return 0;
    return analyserLevel(this.analyser, this.scratch);
  }

  dispose() {
    this.clear();
    try {
      this.gain.disconnect();
      this.analyser.disconnect();
    } catch {
      /* ignore */
    }
  }
}
