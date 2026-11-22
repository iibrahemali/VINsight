// The browser half of the advisor call: microphone capture with voice activity detection (VAD),
// WAV encoding for Whisper, and speech output (Groq Orpheus, or the browser's own voice).
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
};

/** Listens continuously and cuts the stream into utterances using an adaptive energy threshold. */
export class Mic {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private floor = 0.004; // running estimate of background noise
  private speaking = false;
  private loud = 0;
  private quiet = 0;
  private preroll: Float32Array[] = [];
  private captured: Float32Array[] = [];
  muted = false;
  /** Raised while the advisor is talking, so its own voice leaking into the mic isn't taken as speech. */
  strict = false;

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
  }

  private frame(samples: Float32Array) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / samples.length);
    this.cb.onLevel(this.muted ? 0 : Math.min(1, rms * 12));
    if (this.muted) return;

    const startAt = Math.max(0.012, this.floor * (this.strict ? 6 : 3));
    const endAt = Math.max(0.008, this.floor * 2);

    if (!this.speaking) {
      this.floor = 0.97 * this.floor + 0.03 * rms;
      this.preroll.push(samples);
      if (this.preroll.length > 8) this.preroll.shift(); // keep ~340 ms before speech starts
      this.loud = rms > startAt ? this.loud + 1 : 0;
      if (this.loud >= 3) {
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

/** Plays sentences in order. Uses Groq's Orpheus voice when available, else the browser's voice. */
export class Speaker {
  mode: "groq" | "browser";
  private queue: Promise<HTMLAudioElement | null>[] = [];
  private playing: HTMLAudioElement | null = null;
  private browserPending = 0;
  private generation = 0;
  private running = false;

  constructor(mode: "groq" | "browser", private cb: { onStart: () => void; onIdle: () => void; onFallback: (note: string) => void }) {
    this.mode = mode;
  }

  get busy() {
    return this.running || this.queue.length > 0 || this.browserPending > 0;
  }

  say(sentence: string) {
    const text = forSpeech(sentence);
    if (!text) return;
    if (this.mode === "browser") return this.sayBrowser(text);
    // Fetch audio right away (in parallel with playback); play strictly in order.
    const gen = this.generation;
    this.queue.push(
      fetch("/api/voice/speak", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) }).then(
        async (res) => {
          if (gen !== this.generation) return null;
          if (res.status === 409) {
            const note = (await res.json().catch(() => ({}))).detail ?? "Using the browser voice.";
            this.mode = "browser";
            this.cb.onFallback(note);
            this.sayBrowser(text);
            return null;
          }
          if (!res.ok) return null;
          return new Audio(URL.createObjectURL(await res.blob()));
        },
      ),
    );
    if (!this.running) this.pump(gen);
  }

  private async pump(gen: number) {
    this.running = true;
    while (this.queue.length && gen === this.generation) {
      const audio = await this.queue.shift()!;
      if (!audio || gen !== this.generation) continue;
      this.playing = audio;
      this.cb.onStart();
      await new Promise<void>((done) => {
        audio.onended = audio.onerror = () => done();
        audio.play().catch(() => done());
      });
      this.playing = null;
    }
    this.running = false;
    if (gen === this.generation && !this.busy) this.cb.onIdle();
  }

  private sayBrowser(text: string) {
    const u = new SpeechSynthesisUtterance(text);
    u.voice = pickVoice();
    u.rate = 1.05;
    this.browserPending++;
    u.onstart = () => this.cb.onStart();
    u.onend = u.onerror = () => {
      this.browserPending = Math.max(0, this.browserPending - 1);
      if (!this.busy) this.cb.onIdle();
    };
    speechSynthesis.speak(u);
  }

  /** Barge-in: drop everything queued and stop talking immediately. */
  stop() {
    this.generation++;
    this.queue = [];
    this.playing?.pause();
    this.playing = null;
    this.running = false;
    this.browserPending = 0;
    if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  }
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  const prefer = [/natural/i, /samantha/i, /google us english/i, /aria|jenny|zira/i, /en-us/i];
  for (const re of prefer) {
    const v = voices.find((x) => re.test(x.name) || re.test(x.lang));
    if (v) return v;
  }
  return voices[0] ?? null;
}

export async function transcribe(wav: Blob): Promise<string> {
  const body = new FormData();
  body.append("audio", wav, "speech.wav");
  const res = await fetch("/api/voice/transcribe", { method: "POST", body });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`);
  return (await res.json()).text as string;
}

export async function voiceStatus(): Promise<{ stt: boolean; tts: "groq" | "browser"; tts_note: string | null }> {
  const res = await fetch("/api/voice/status");
  return res.json();
}
