import { apiFetch } from "@/lib/keys";

// The browser half of the advisor call: microphone capture with voice activity detection (VAD),
// WAV encoding for Whisper, and speech output (Groq Orpheus, with the in-browser Kokoro voice as fallback).
// Everything runs on free tiers; no LiveKit or WebRTC server is needed for a 1:1 call.

// Collects mic samples off the main thread and posts them in ~40 ms batches.
const TAP_WORKLET = `
class Tap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("tap", Tap);
`;

export type MicCallbacks = {
  onLevel: (level: number) => void; // 0..1, for the orb
  onSpeechStart: () => void; // used for barge-in
  onUtterance: (wav: Blob, ms: number) => void;
  onDiscard?: () => void; // speech started but was too short to be an utterance (a cough, a click)
};

/** Listens continuously and cuts the stream into utterances using an adaptive energy threshold. */
export class Mic {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private floor = 0.004; // running estimate of background noise
  private echoFloor = 0; // how loud the advisor's own voice is in the mic while it talks
  private speaking = false;
  private loud = 0;
  private quiet = 0;
  private preroll: Float32Array[] = [];
  private captured: Float32Array[] = [];
  muted = false;
  /** Set while the advisor is talking, so its own voice leaking into the mic isn't taken as speech. */
  strict = false;
  /** When false, nothing the mic hears while the advisor talks counts (speakers without headphones). */
  bargeIn = true;

  constructor(private cb: MicCallbacks) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([TAP_WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    this.node = new AudioWorkletNode(this.ctx, "tap");
    this.node.port.onmessage = (e) => this.frame(e.data as Float32Array);
    this.ctx.createMediaStreamSource(this.stream).connect(this.node);
    // An AudioContext created after an await can start "suspended" under autoplay rules, and then
    // the mic is silently never processed. Resume it explicitly.
    await this.ctx.resume().catch(() => {});
  }

  private frame(samples: Float32Array) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.cb.onLevel(this.muted ? 0 : Math.min(1, rms * 12));
    if (this.muted) return;

    // While the advisor talks, some of its voice can still come back through the mic. Learn how loud
    // that echo is and require speech clearly above it, held for longer, before treating it as the
    // caller interrupting.
    const startAt = this.strict
      ? Math.max(0.025, this.floor * 6, this.echoFloor * 2.5)
      : Math.max(0.012, this.floor * 3);
    const endAt = Math.max(0.008, this.floor * 2);
    const framesNeeded = this.strict ? 6 : 3; // ~250 ms vs ~130 ms

    if (!this.speaking) {
      if (this.strict) {
        if (!this.bargeIn) return;
        if (rms < startAt) this.echoFloor = Math.max(rms, 0.9 * this.echoFloor + 0.1 * rms);
      } else {
        this.floor = 0.97 * this.floor + 0.03 * rms;
        this.echoFloor *= 0.9; // forget the echo level once the advisor stops
      }
      this.preroll.push(samples);
      if (this.preroll.length > 8) this.preroll.shift(); // keep ~340 ms before speech starts
      this.loud = rms > startAt ? this.loud + 1 : 0;
      if (this.loud >= framesNeeded) {
        this.speaking = true;
        this.quiet = 0;
        this.captured = [...this.preroll];
        this.cb.onSpeechStart();
      }
      return;
    }

    this.captured.push(samples);
    this.quiet = rms < endAt ? this.quiet + 1 : 0;
    const seconds = (this.captured.length * samples.length) / (this.ctx?.sampleRate ?? 48000);
    if (this.quiet >= 18 || seconds > 30) {
      // ~0.75 s of silence ends the utterance.
      this.speaking = false;
      this.loud = 0;
      const wav = encodeWav(this.captured, this.ctx?.sampleRate ?? 48000);
      this.captured = [];
      this.preroll = [];
      if (seconds > 0.5) this.cb.onUtterance(wav, seconds * 1000);
      else this.cb.onDiscard?.();
    }
  }

  stop() {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close().catch(() => {});
  }
}

/** 16 kHz mono 16-bit WAV: small to upload, and what Whisper works at anyway. */
function encodeWav(chunks: Float32Array[], rate: number): Blob {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const ratio = rate / 16000;
  const out = new Int16Array(Math.floor(total / ratio));
  let pos = 0;
  let acc = 0;
  let count = 0;
  let next = ratio;
  let i = 0;
  for (const chunk of chunks) {
    for (let j = 0; j < chunk.length; j++, i++) {
      acc += chunk[j];
      count++;
      if (i + 1 >= next && pos < out.length) {
        const v = Math.max(-1, Math.min(1, acc / count));
        out[pos++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        acc = 0;
        count = 0;
        next += ratio;
      }
    }
  }
  const buf = new ArrayBuffer(44 + out.length * 2);
  const view = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((c, k) => view.setUint8(o + k, c.charCodeAt(0)));
  w(0, "RIFF");
  view.setUint32(4, 36 + out.length * 2, true);
  w(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true);
  view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  w(36, "data");
  view.setUint32(40, out.length * 2, true);
  new Int16Array(buf, 44).set(out);
  return new Blob([buf], { type: "audio/wav" });
}

/** Splits streamed text into sentences so speech can start before the reply is finished.
 *  A boundary is sentence punctuation followed by whitespace (so "2.5" never splits), or a newline. */
export class SentenceChunker {
  private buf = "";
  feed(delta: string): string[] {
    this.buf += delta;
    const out: string[] = [];
    const boundary = /[.!?]+["')\]]*\s+|\n+/g;
    let start = 0;
    for (let m = boundary.exec(this.buf); m; m = boundary.exec(this.buf)) {
      const sentence = this.buf.slice(start, m.index + m[0].length).trim();
      if (sentence) out.push(sentence);
      start = m.index + m[0].length;
    }
    this.buf = this.buf.slice(start);
    return out;
  }
  flush(): string[] {
    const rest = this.buf.trim();
    this.buf = "";
    return rest ? [rest] : [];
  }
}

/** Removes anything that sounds wrong read aloud: markdown, citation brackets, links. */
export function forSpeech(text: string): string {
  return text
    .replace(/\[[a-z]+:[^\]]+\]/gi, "")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[*_#>`~|]/g, "")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

// ---------------------------------------------------------------------------------------------
// Speech output
// ---------------------------------------------------------------------------------------------

export type VoiceEngine = "groq" | "kokoro";
export const VOICE_ENGINES: { id: VoiceEngine; label: string; detail: string }[] = [
  { id: "groq", label: "Groq voice", detail: "Orpheus on Groq. Most expressive; free tier is about 10 requests a minute, then the in-browser voice takes over." },
  { id: "kokoro", label: "In-browser voice", detail: "Kokoro open model on your device. Free and unlimited; one-time download (about 90–330 MB)." },
];
const KOKORO_VOICE = "am_michael"; // male, to match the Groq voice

// --- Kokoro: one shared worker for the whole page, loaded ahead of the call ---

export type KokoroState = { status: "idle" | "loading" | "ready" | "error"; progress: number; device?: string; error?: string };
let worker: Worker | null = null;
let kokoro: KokoroState = { status: "idle", progress: 0 };
const kokoroListeners = new Set<(s: KokoroState) => void>();
const pending = new Map<number, { resolve: (a: { audio: Float32Array; rate: number }) => void; reject: (e: Error) => void }>();
let nextId = 1;

function setKokoro(patch: Partial<KokoroState>) {
  kokoro = { ...kokoro, ...patch };
  kokoroListeners.forEach((l) => l(kokoro));
}

export function onKokoro(listener: (s: KokoroState) => void): () => void {
  kokoroListeners.add(listener);
  listener(kokoro);
  return () => kokoroListeners.delete(listener);
}

/** Resolves once the in-browser voice is ready, or after `timeoutMs` (whichever comes first). */
export function kokoroReady(timeoutMs: number): Promise<boolean> {
  return new Promise((done) => {
    let stop: (() => void) | null = null;
    const timer = setTimeout(() => finish(false), timeoutMs);
    function finish(ok: boolean) {
      clearTimeout(timer);
      queueMicrotask(() => stop?.());
      done(ok);
    }
    stop = onKokoro((k) => {
      if (k.status === "ready") finish(true);
      else if (k.status === "error") finish(false);
    });
  });
}

/** Start downloading and loading the voice model in the background. Safe to call repeatedly. */
export function loadKokoro(): void {
  if (typeof window === "undefined" || worker) return;
  worker = new Worker(new URL("./kokoro.worker.ts", import.meta.url), { type: "module" });
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "progress") setKokoro({ progress: Math.max(kokoro.progress, Math.round(m.progress)) });
    else if (m.type === "ready") setKokoro({ status: "ready", progress: 100, device: m.device ?? kokoro.device });
    else if (m.type === "audio") {
      pending.get(m.id)?.resolve({ audio: m.audio, rate: m.rate });
      pending.delete(m.id);
    } else if (m.type === "error") {
      if (m.id !== undefined) {
        pending.get(m.id)?.reject(new Error(m.error));
        pending.delete(m.id);
      } else setKokoro({ status: "error", error: m.error });
    }
  };
  worker.onerror = (e) => setKokoro({ status: "error", error: e.message || "voice worker failed" });
  setKokoro({ status: "loading" });
  worker.postMessage({ type: "load" });
}

function kokoroSpeak(text: string, voice = KOKORO_VOICE): Promise<{ audio: Float32Array; rate: number }> {
  if (!worker || kokoro.status !== "ready") return Promise.reject(new Error("in-browser voice not ready"));
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker!.postMessage({ type: "speak", id, text, voice });
  });
}

// --- Output path: Web Audio -> local WebRTC loopback -> <audio> ---
// Chrome only cancels echo for audio it plays through WebRTC. Routing the advisor's voice through a
// local peer connection (no server involved) lets the browser subtract it from the microphone, so
// the advisor stops hearing itself and interrupting itself.
class AudioOut {
  readonly ctx = new AudioContext();
  // Voice models output fairly quiet audio: boost it, with a compressor so peaks don't distort.
  private readonly gain = this.ctx.createGain();
  private readonly compressor = this.ctx.createDynamicsCompressor();
  readonly analyser = this.ctx.createAnalyser();
  private el: HTMLAudioElement | null = null;
  private peers: RTCPeerConnection[] = [];
  echoCancelled = false;

  constructor() {
    this.gain.gain.value = 2.2;
    this.compressor.threshold.value = -14;
    this.compressor.ratio.value = 6;
    this.compressor.attack.value = 0.003;
    this.compressor.release.value = 0.15;
    this.gain.connect(this.compressor).connect(this.analyser);
    this.analyser.fftSize = 512;
    this.loopback().catch(() => this.analyser.connect(this.ctx.destination));
  }

  private async loopback() {
    const dest = this.ctx.createMediaStreamDestination();
    this.analyser.connect(dest);
    const a = new RTCPeerConnection();
    const b = new RTCPeerConnection();
    this.peers = [a, b];
    a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate);
    b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
    const el = new Audio();
    el.autoplay = true;
    this.el = el;
    b.ontrack = (e) => {
      el.srcObject = e.streams[0];
      el.play().catch(() => {});
    };
    dest.stream.getTracks().forEach((t) => a.addTrack(t, dest.stream));
    const offer = await a.createOffer();
    await a.setLocalDescription(offer);
    await b.setRemoteDescription(offer);
    const answer = await b.createAnswer();
    await b.setLocalDescription(answer);
    await a.setRemoteDescription(answer);
    this.echoCancelled = true;
  }

  /** Plays one clip; resolves when it finishes or is stopped. */
  play(buffer: AudioBuffer, onSource: (s: AudioBufferSourceNode) => void): Promise<void> {
    this.ctx.resume().catch(() => {});
    return new Promise((done) => {
      const src = this.ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.gain);
      src.onended = () => done();
      onSource(src);
      src.start();
    });
  }

  level(): number {
    const data = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += ((v - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / data.length) * 4);
  }

  close() {
    this.peers.forEach((p) => p.close());
    this.el?.pause();
    this.ctx.close().catch(() => {});
  }
}

type Clip = { buffer: AudioBuffer; engine: VoiceEngine } | null;

/** Speaks a reply with no skipped lines. Groq is preferred; if it fails or hits the free-tier limit,
 *  the in-browser voice takes over for the rest of that reply and Groq is retried 30 s later.
 *  With Groq, the first sentence goes out alone (fast start) and the rest of the reply is sent as one
 *  request, which keeps a turn to about two requests under the 10-a-minute free limit. */
export class Speaker {
  preferred: VoiceEngine;
  lastEngine: VoiceEngine | null = null;
  private out = new AudioOut();
  private queue: Promise<Clip>[] = [];
  private current: AudioBufferSourceNode | null = null;
  private generation = 0;
  private running = false;
  private groqCooldownUntil = 0;
  private groqBlocked = false;
  private turnStarted = false;
  private held: string[] = []; // later sentences of this reply, sent to Groq together

  constructor(preferred: VoiceEngine, private cb: { onStart: () => void; onIdle: () => void; onFallback: (note: string) => void }) {
    this.preferred = preferred;
    loadKokoro(); // always ready as the fallback
  }

  get busy() {
    return this.running || this.queue.length > 0 || this.held.length > 0;
  }

  level(): number {
    return this.out.level();
  }

  private get groqUsable() {
    return this.preferred === "groq" && !this.groqBlocked && Date.now() >= this.groqCooldownUntil;
  }

  say(sentence: string) {
    const text = forSpeech(sentence);
    if (!text) return;
    if (this.groqUsable && this.turnStarted) {
      this.held.push(text);
      return;
    }
    this.turnStarted = true;
    this.enqueue(text);
  }

  /** The reply is complete: send whatever was held back. */
  endTurn() {
    if (this.held.length) this.enqueue(this.held.join(" "));
    this.held = [];
    this.turnStarted = false;
  }

  private enqueue(text: string) {
    const gen = this.generation;
    this.queue.push(this.synth(text, gen));
    if (!this.running) this.pump(gen);
  }

  private async synth(text: string, gen: number): Promise<Clip> {
    if (this.groqUsable) {
      try {
        const res = await apiFetch("/api/voice/speak", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text }),
        });
        if (res.ok) {
          const buffer = await this.out.ctx.decodeAudioData(await res.arrayBuffer());
          return gen === this.generation ? { buffer, engine: "groq" } : null;
        }
        const detail: string = (await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`;
        const limited = /rate limit|429/i.test(detail);
        if (limited) this.groqCooldownUntil = Date.now() + 30_000;
        else this.groqBlocked = true;
        this.cb.onFallback(limited ? "Groq voice hit its free-tier limit; using the in-browser voice for a moment." : `Groq voice unavailable (${detail}); using the in-browser voice.`);
      } catch {
        this.groqCooldownUntil = Date.now() + 30_000;
      }
    }
    // Fallback: the in-browser voice. Wait for it if it's still loading rather than skip the line.
    if (gen !== this.generation) return null;
    if (!(await kokoroReady(45_000))) {
      this.cb.onFallback("No voice available right now; follow along with the captions.");
      return null;
    }
    try {
      const { audio, rate } = await kokoroSpeak(text);
      const buffer = this.out.ctx.createBuffer(1, audio.length, rate);
      buffer.copyToChannel(audio as Float32Array<ArrayBuffer>, 0);
      return gen === this.generation ? { buffer, engine: "kokoro" } : null;
    } catch {
      return null;
    }
  }

  private async pump(gen: number) {
    this.running = true;
    while (this.queue.length && gen === this.generation) {
      const clip = await this.queue.shift()!;
      if (!clip || gen !== this.generation) continue;
      this.lastEngine = clip.engine;
      this.cb.onStart();
      await this.out.play(clip.buffer, (src) => (this.current = src));
      this.current = null;
    }
    this.running = false;
    if (gen === this.generation && !this.busy) this.cb.onIdle();
  }

  /** Hold speech while we find out whether the caller is really talking (or it was our own echo). */
  pause() {
    this.out.ctx.suspend().catch(() => {});
  }

  resume() {
    this.out.ctx.resume().catch(() => {});
  }

  /** Barge-in: drop everything queued and stop talking immediately. */
  stop() {
    this.generation++;
    this.queue = [];
    this.held = [];
    this.turnStarted = false;
    try {
      this.current?.stop();
    } catch {}
    this.current = null;
    this.running = false;
    this.out.ctx.resume().catch(() => {});
  }

  close() {
    this.stop();
    this.out.close();
  }
}

/** True when a transcript is mostly the advisor's own recent words: its voice leaking into the mic. */
export function isEcho(heard: string, said: string): boolean {
  const words = (t: string) => t.toLowerCase().replace(/[^a-z0-9\s']/g, " ").split(/\s+/).filter((w) => w.length > 2);
  const h = words(heard);
  if (h.length < 2) return false;
  const s = new Set(words(said));
  return h.filter((w) => s.has(w)).length / h.length >= 0.6;
}

export async function transcribe(wav: Blob): Promise<string> {
  const body = new FormData();
  body.append("audio", wav, "speech.wav");
  const res = await apiFetch("/api/voice/transcribe", { method: "POST", body });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
  return (await res.json()).text as string;
}

export async function voiceStatus(): Promise<{ stt: boolean; tts: "groq" | "fallback"; tts_note: string | null }> {
  const res = await apiFetch("/api/voice/status");
  return res.json();
}
