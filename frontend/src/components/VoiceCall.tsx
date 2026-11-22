"use client";

import { Cloud, Ear, EarOff, Keyboard, Laptop, Mic as MicIcon, MicOff, PhoneOff, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { BriefCard, type Profile } from "@/components/BriefCard";
import { LogoMark } from "@/components/Logo";
import { stopSession, streamChat } from "@/lib/agent";
import {
  Mic,
  SentenceChunker,
  Speaker,
  VOICE_ENGINES,
  isEcho,
  kokoroReady,
  loadKokoro,
  onKokoro,
  transcribe,
  voiceStatus,
  type KokoroState,
  type VoiceEngine,
} from "@/lib/voice";

const VOICE_KEY = "vinsight.voice";

function savedEngine(): VoiceEngine {
  try {
    const v = localStorage.getItem(VOICE_KEY);
    if (v === "kokoro" || v === "groq") return v;
  } catch {}
  return "groq";
}

type Status = "connecting" | "listening" | "hearing" | "thinking" | "speaking" | "error";
export type CallLine = { who: "you" | "sam"; text: string };
export type CallSummary = { profile: Profile; transcript: CallLine[]; seconds: number; latencies: number[] };

const STATUS_LABEL: Record<Status, string> = {
  connecting: "Connecting…",
  listening: "Listening",
  hearing: "Hearing you…",
  thinking: "Thinking…",
  speaking: "Sam is speaking",
  error: "Something went wrong",
};

// A round call control with a label underneath, like a phone's in-call screen.
function Control(props: {
  label: string;
  sub?: string;
  active?: boolean;
  tone?: "bad" | "warn" | "accent";
  title?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const on = { bad: "border-bad/50 bg-bad/10 text-bad", warn: "border-warn/50 bg-warn/10 text-warn", accent: "border-accent bg-accent/10 text-accent" };
  return (
    <div className="flex w-14 flex-col items-center gap-1.5 sm:w-16">
      <button
        onClick={props.onClick}
        aria-label={props.label}
        aria-pressed={props.active}
        title={props.title}
        className={`grid size-14 place-items-center rounded-full border transition-colors ${
          props.active && props.tone ? on[props.tone] : "border-line bg-panel hover:bg-bg"
        }`}
      >
        {props.children}
      </button>
      <span className="text-center text-[11px] leading-tight text-muted">
        {props.label}
        {props.sub && <span className="block text-[10px] opacity-80">{props.sub}</span>}
      </span>
    </div>
  );
}

// A full-screen voice call with the advisor. Hands-free: it listens all the time, and talking over
// Sam interrupts him (barge-in). The car brief fills in on the side as the note-taker works.
export function VoiceCall(props: {
  sessionId: React.RefObject<string | null>;
  model: string | null;
  onEnd: (summary: CallSummary) => void;
}) {
  const [status, setStatus] = useState<Status>("connecting");
  const [level, setLevel] = useState(0);
  const [lines, setLines] = useState<CallLine[]>([]);
  const [live, setLive] = useState<CallLine | null>(null); // Sam's reply as it streams
  const [profile, setProfile] = useState<Profile>({});
  const [muted, setMuted] = useState(false);
  const [voiceNote, setVoiceNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [bargeIn, setBargeIn] = useState(true);
  const [engine, setEngine] = useState<VoiceEngine>(savedEngine);
  const [kokoro, setKokoro] = useState<KokoroState>({ status: "idle", progress: 0 });
  const [usedEngine, setUsedEngine] = useState<VoiceEngine | null>(null);

  const mic = useRef<Mic | null>(null);
  const speaker = useRef<Speaker | null>(null);
  const turn = useRef<AbortController | null>(null);
  const statusRef = useRef<Status>("connecting");
  const streaming = useRef(false);
  const heardAt = useRef(0);
  const lastSaid = useRef(""); // Sam's latest reply, to recognise his own voice echoing back
  const held = useRef(false); // Sam paused because the mic heard something while he talked
  const latencies = useRef<number[]>([]);
  const state = useRef({ lines: [] as CallLine[], profile: {} as Profile });

  const set = useCallback((s: Status) => {
    statusRef.current = s;
    setStatus(s);
    if (mic.current) mic.current.strict = s === "speaking";
  }, []);

  const addLine = useCallback((line: CallLine) => {
    state.current.lines = [...state.current.lines, line];
    setLines(state.current.lines);
  }, []);

  /** Stop whatever Sam is doing: queued speech and the in-flight reply. */
  const interrupt = useCallback(() => {
    speaker.current?.stop();
    if (turn.current) {
      turn.current.abort();
      turn.current = null;
      if (props.sessionId.current) stopSession(props.sessionId.current);
    }
    streaming.current = false;
  }, [props.sessionId]);

  const ask = useCallback(
    async (text: string, hidden = false) => {
      interrupt();
      if (!hidden) addLine({ who: "you", text });
      set("thinking");
      const ctrl = new AbortController();
      turn.current = ctrl;
      streaming.current = true;
      const chunker = new SentenceChunker();
      let reply = "";
      let firstAudio = true;
      try {
        await streamChat({
          message: text,
          persona: "advisor",
          provider: props.model,
          attachments: [],
          sessionId: props.sessionId.current,
          signal: ctrl.signal,
          onSession: (id) => (props.sessionId.current = id),
          onEvent: (e) => {
            if (e.type === "text" && e.text) {
              reply += e.text;
              lastSaid.current = reply;
              setLive({ who: "sam", text: reply });
              for (const sentence of chunker.feed(e.text)) {
                if (firstAudio && heardAt.current) {
                  latencies.current.push(Date.now() - heardAt.current);
                  firstAudio = false;
                }
                speaker.current?.say(sentence);
              }
            } else if (e.type === "profile" && e.output) {
              state.current.profile = e.output as Profile;
              setProfile(state.current.profile);
            } else if (e.type === "error" && e.text !== "Stopped.") {
              setError(e.text ?? "error");
            }
          },
        });
      } catch {
        if (ctrl.signal.aborted) return; // barge-in or hang-up
        setError("Lost connection to the backend.");
      }
      if (turn.current !== ctrl) return;
      for (const sentence of chunker.flush()) speaker.current?.say(sentence);
      speaker.current?.endTurn();
      streaming.current = false;
      turn.current = null;
      if (reply.trim()) addLine({ who: "sam", text: reply.trim() });
      setLive(null);
      if (!speaker.current?.busy && statusRef.current !== "hearing") set("listening");
    },
    [addLine, interrupt, props.model, props.sessionId, set],
  );

  useEffect(() => onKokoro(setKokoro), []);

  // Switching voices mid-call applies from the next sentence.
  useEffect(() => {
    if (speaker.current) speaker.current.preferred = engine;
    loadKokoro(); // the fallback voice, so it's ready if Groq can't speak
    try {
      localStorage.setItem(VOICE_KEY, engine);
    } catch {}
  }, [engine]);

  // Start the call: mic, voice, then have Sam say hello.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const vs = await voiceStatus().catch(() => ({ stt: false, tts: "fallback" as const, tts_note: null }));
      speaker.current = new Speaker(engine, {
        onStart: () => {
          setUsedEngine(speaker.current?.lastEngine ?? null);
          if (statusRef.current !== "hearing") set("speaking");
        },
        onIdle: () => !streaming.current && statusRef.current === "speaking" && set("listening"),
        onFallback: (note) => setVoiceNote(note),
      });
      if (!vs.stt) {
        setError("Voice needs a GROQ_API_KEY on the backend. You can still type below.");
        setTyping(true);
      } else {
        const m = new Mic({
          onLevel: setLevel,
          onSpeechStart: () => {
            if (statusRef.current === "speaking") {
              // Could be the caller, or Sam's own voice leaking into the mic: pause, don't stop yet.
              held.current = true;
              speaker.current?.pause();
            } else if (statusRef.current === "thinking") {
              interrupt(); // nothing is playing, so this is the caller
            }
            set("hearing");
          },
          onDiscard: () => {
            // Too short to be speech (a cough, a click): carry on where Sam left off.
            if (held.current) {
              held.current = false;
              speaker.current?.resume();
              set("speaking");
            } else set("listening");
          },
          onUtterance: async (wav) => {
            heardAt.current = Date.now();
            set("thinking");
            try {
              const text = await transcribe(wav);
              const wasHeld = held.current;
              held.current = false;
              if (!text || isEcho(text, lastSaid.current)) {
                // Silence, or Sam's own voice through the speakers: resume as if nothing happened.
                if (wasHeld) {
                  speaker.current?.resume();
                  set("speaking");
                } else set(speaker.current?.busy ? "speaking" : "listening");
              } else ask(text); // the caller really spoke: ask() stops Sam and answers
            } catch (e) {
              setError(`Couldn't transcribe: ${(e as Error).message}`);
              set("listening");
            }
          },
        });
        try {
          await m.start();
          if (cancelled) return m.stop();
          mic.current = m;
        } catch {
          setError("Microphone access was blocked. Allow it in the address bar, or type instead.");
          setTyping(true);
        }
      }
      // Give the in-browser voice a moment to finish loading so the greeting uses it.
      if (engine === "kokoro") await kokoroReady(20_000);
      if (!cancelled) ask("(The caller just connected. Greet them in one line and ask your first question.)", true);
    })();
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
      mic.current?.stop();
      speaker.current?.close();
      turn.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per call
  }, []);

  function hangUp() {
    interrupt();
    mic.current?.stop();
    props.onEnd({ profile: state.current.profile, transcript: state.current.lines, seconds, latencies: latencies.current });
  }

  const recent = [...lines.slice(-2), ...(live ? [live] : [])].slice(-2);
  const avg = latencies.current.length ? Math.round(latencies.current.reduce((a, b) => a + b, 0) / latencies.current.length) : null;
  const ringScale = status === "hearing" || status === "listening" ? 1 + level * 0.6 : status === "speaking" ? 1.15 : 1;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[radial-gradient(80%_60%_at_50%_0%,color-mix(in_oklab,var(--accent)_22%,var(--bg)),var(--bg))]">
      <header className="flex items-center gap-3 px-5 py-4">
        <LogoMark size={26} />
        <div className="leading-tight">
          <p className="text-[14px] font-medium">Sam · VINsight Advisor</p>
          <p className="font-mono text-[12px] text-muted">
            {String(Math.floor(seconds / 60)).padStart(2, "0")}:{String(seconds % 60).padStart(2, "0")}
            {avg !== null && <span> · ~{(avg / 1000).toFixed(1)}s reply</span>}
          </p>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 pb-4 lg:flex-row lg:items-stretch">
        <section className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
          <button
            type="button"
            onClick={() => {
              if (statusRef.current === "speaking" || statusRef.current === "thinking") {
                interrupt();
                set("listening");
              }
            }}
            aria-label="Tap to interrupt"
            title={status === "speaking" ? "Tap to interrupt" : undefined}
            className="relative grid size-48 place-items-center"
          >
            <div
              className={`absolute inset-0 rounded-full bg-accent/15 transition-transform duration-150 ${status === "speaking" ? "animate-ping-slow" : ""}`}
              style={{ transform: `scale(${ringScale})` }}
            />
            <div className="absolute inset-6 rounded-full bg-accent/20 transition-transform duration-150" style={{ transform: `scale(${1 + level * 0.3})` }} />
            <div className={`relative grid size-28 place-items-center rounded-full bg-gradient-to-br from-[#2dd4bf] to-[#0f766e] shadow-2xl shadow-accent/30 ${status === "thinking" ? "animate-pulse" : ""}`}>
              <span className="text-3xl font-semibold text-white">S</span>
            </div>
          </button>
          <p className={`text-[15px] font-medium ${status === "thinking" ? "shimmer" : status === "error" ? "text-bad" : ""}`}>
            {muted ? "Muted" : STATUS_LABEL[status]}
          </p>
          <div className="min-h-24 max-w-xl space-y-2">
            {recent.map((l, i) => (
              <p key={i} className={l.who === "you" ? "text-[14px] text-muted" : "text-[17px] leading-snug"}>
                {l.who === "you" ? `You: ${l.text}` : l.text}
              </p>
            ))}
          </div>
          {error && <p className="max-w-md rounded-lg bg-bad/10 px-3 py-2 text-[12px] text-bad">{error}</p>}
          {voiceNote && <p className="max-w-md text-[11px] text-muted">{voiceNote}</p>}
        </section>

        <aside className="w-full shrink-0 lg:w-96">
          <BriefCard profile={profile} live />
        </aside>
      </div>

      {typing && (
        <form
          className="mx-auto mb-2 flex w-full max-w-xl gap-2 px-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.trim()) ask(draft.trim());
            setDraft("");
          }}
        >
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type to Sam…"
            className="flex-1 rounded-xl border border-line bg-panel px-4 py-2.5 text-[14px] outline-none focus:border-accent"
          />
          <button className="rounded-xl bg-accent px-3 text-bg" aria-label="Send">
            <Send size={16} />
          </button>
        </form>
      )}

      <footer className="flex items-start justify-center gap-2 px-4 pb-8 pt-2 sm:gap-5">
        <Control
          label={muted ? "Unmute" : "Mute"}
          active={muted}
          tone="bad"
          onClick={() => {
            const next = !muted;
            setMuted(next);
            if (mic.current) mic.current.muted = next;
          }}
        >
          {muted ? <MicOff size={20} /> : <MicIcon size={20} />}
        </Control>
        <Control
          label="Interrupt"
          sub={bargeIn ? "by voice" : "tap orb"}
          active={!bargeIn}
          tone="warn"
          title={bargeIn ? "Talk over Sam to interrupt. Turn off if he keeps cutting himself off on speakers." : "Voice interrupt is off: tap the orb to interrupt."}
          onClick={() => {
            const next = !bargeIn;
            setBargeIn(next);
            if (mic.current) mic.current.bargeIn = next;
          }}
        >
          {bargeIn ? <Ear size={20} /> : <EarOff size={20} />}
        </Control>

        <div className="flex flex-col items-center gap-1.5">
          <button
            onClick={hangUp}
            aria-label="End call"
            className="grid size-16 place-items-center rounded-full bg-[#dc2626] text-white shadow-lg transition-transform hover:scale-105"
          >
            <PhoneOff size={22} />
          </button>
          <span className="text-[11px] text-muted">End</span>
        </div>

        <Control
          label="Voice"
          sub={
            engine === "groq"
              ? usedEngine === "kokoro"
                ? "Groq · on backup"
                : "Groq"
              : kokoro.status === "loading"
                ? `Browser · ${kokoro.progress}%`
                : "Browser"
          }
          title={`${VOICE_ENGINES.find((v) => v.id === engine)?.detail} Click to switch.`}
          onClick={() => setEngine(engine === "groq" ? "kokoro" : "groq")}
        >
          {engine === "groq" ? <Cloud size={20} /> : <Laptop size={20} />}
        </Control>
        <Control label="Type" active={typing} tone="accent" onClick={() => setTyping((t) => !t)}>
          <Keyboard size={20} />
        </Control>
      </footer>
      <p className="pb-4 text-center text-[11px] text-muted">
        {bargeIn ? "Talk any time to interrupt Sam. On speakers? Use headphones, or turn off voice interrupt." : "Voice interrupt is off. Tap the orb to interrupt Sam."}
      </p>
    </div>
  );
}
