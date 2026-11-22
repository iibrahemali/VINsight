// Runs Kokoro TTS (an 82M-parameter open voice model) off the main thread, so speech generation
// never stalls the page or the microphone's voice detection. The model downloads once, then the
// browser caches it.
import { KokoroTTS } from "kokoro-js";

const MODEL = "onnx-community/Kokoro-82M-v1.0-ONNX";
let tts: KokoroTTS | null = null;

type In = { type: "load" } | { type: "speak"; id: number; text: string; voice: string };

async function pickDevice(): Promise<{ device: "webgpu" | "wasm"; dtype: "fp32" | "q8" }> {
  // WebGPU (the Mac's GPU) is much faster; fall back to WebAssembly on the CPU.
  // On WebGPU only fp32 sounds right: fp16 produces garbled, buzzing audio (kokoro-js recommends fp32).
  const gpu = (self.navigator as Navigator & { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
  const adapter = gpu ? await gpu.requestAdapter().catch(() => null) : null;
  if (adapter) return { device: "webgpu", dtype: "fp32" };
  return { device: "wasm", dtype: "q8" };
}

async function load() {
  const progress = (p: { status: string; progress?: number; file?: string }) => {
    if (p.status === "progress" && p.file?.endsWith(".onnx")) self.postMessage({ type: "progress", progress: p.progress ?? 0 });
  };
  const first = await pickDevice();
  try {
    tts = await KokoroTTS.from_pretrained(MODEL, { ...first, progress_callback: progress });
    self.postMessage({ type: "ready", device: first.device });
  } catch (err) {
    if (first.device === "wasm") throw err;
    // Some GPUs/drivers fail to run the model; the CPU path always works.
    tts = await KokoroTTS.from_pretrained(MODEL, { device: "wasm", dtype: "q8", progress_callback: progress });
    self.postMessage({ type: "ready", device: "wasm" });
  }
}

self.onmessage = async (e: MessageEvent<In>) => {
  const msg = e.data;
  try {
    if (msg.type === "load") {
      if (!tts) await load();
      else self.postMessage({ type: "ready" });
    } else if (msg.type === "speak") {
      if (!tts) throw new Error("voice model not loaded");
      const out = await tts.generate(msg.text, { voice: msg.voice as "af_heart" });
      const audio = out.audio as Float32Array;
      self.postMessage({ type: "audio", id: msg.id, audio, rate: out.sampling_rate }, { transfer: [audio.buffer] });
    }
  } catch (err) {
    self.postMessage({ type: "error", id: msg.type === "speak" ? msg.id : undefined, error: String(err) });
  }
};
