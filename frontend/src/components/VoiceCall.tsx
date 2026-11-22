"use client";

import { Keyboard, Mic as MicIcon, MicOff, PhoneOff, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { BriefCard, type Profile } from "@/components/BriefCard";
import { LogoMark } from "@/components/Logo";
import { stopSession, streamChat } from "@/lib/agent";
import { Mic, SentenceChunker, Speaker, transcribe, voiceStatus } from "@/lib/voice";

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

  const mic = useRef<Mic | null>(null);
  const speaker = useRef<Speaker | null>(null);
  const turn = useRef<AbortController | null>(null);
  const statusRef = useRef<Status>("connecting");
  const streaming = useRef(false);
  const heardAt = useRef(0);
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
      streaming.current = false;
      turn.current = null;
      if (reply.trim()) addLine({ who: "sam", text: reply.trim() });
      setLive(null);
      if (!speaker.current?.busy && statusRef.current !== "hearing") set("listening");
    },
    [addLine, interrupt, props.model, props.sessionId, set],
  );

  // Start the call: mic, voice, then have Sam say hello.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const vs = await voiceStatus().catch(() => ({ stt: false, tts: "browser" as const, tts_note: null }));
      if (vs.tts_note) setVoiceNote(vs.tts_note);
      speaker.current = new Speaker(vs.tts, {
        onStart: () => statusRef.current !== "hearing" && set("speaking"),
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
            // Barge-in: the caller started talking, so Sam stops.
            if (statusRef.current === "speaking" || statusRef.current === "thinking") interrupt();
            set("hearing");
          },
          onUtterance: async (wav) => {
            heardAt.current = Date.now();
            set("thinking");
            try {
              const text = await transcribe(wav);
              if (text) ask(text);
              else set("listening");
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
      if (!cancelled) ask("(The caller just connected. Greet them in one line and ask your first question.)", true);
    })();
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => {
      cancelled = true;
      clearInterval(timer);
      mic.current?.stop();
      speaker.current?.stop();
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
        <span className="ml-auto rounded-full border border-line bg-panel/60 px-2.5 py-1 text-[11px] text-muted" title={voiceNote ?? undefined}>
          {speaker.current?.mode === "groq" ? "Natural voice" : "Browser voice"}
        </span>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 pb-4 lg:flex-row lg:items-stretch">
        <section className="flex flex-1 flex-col items-center justify-center gap-6 text-center">
          <div className="relative grid size-48 place-items-center">
            <div
              className={`absolute inset-0 rounded-full bg-accent/15 transition-transform duration-150 ${status === "speaking" ? "animate-ping-slow" : ""}`}
              style={{ transform: `scale(${ringScale})` }}
            />
            <div className="absolute inset-6 rounded-full bg-accent/20 transition-transform duration-150" style={{ transform: `scale(${1 + level * 0.3})` }} />
            <div className={`relative grid size-28 place-items-center rounded-full bg-gradient-to-br from-[#2dd4bf] to-[#0f766e] shadow-2xl shadow-accent/30 ${status === "thinking" ? "animate-pulse" : ""}`}>
              <span className="text-3xl font-semibold text-white">S</span>
            </div>
          </div>
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
          {voiceNote && speaker.current?.mode === "browser" && <p className="max-w-md text-[11px] text-muted">{voiceNote}</p>}
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

      <footer className="flex items-center justify-center gap-4 px-5 pb-8 pt-2">
        <button
          onClick={() => {
            const next = !muted;
            setMuted(next);
            if (mic.current) mic.current.muted = next;
          }}
          aria-label={muted ? "Unmute" : "Mute"}
          className={`grid size-14 place-items-center rounded-full border ${muted ? "border-bad/50 bg-bad/10 text-bad" : "border-line bg-panel"}`}
        >
          {muted ? <MicOff size={20} /> : <MicIcon size={20} />}
        </button>
        <button onClick={hangUp} aria-label="End call" className="grid size-16 place-items-center rounded-full bg-[#dc2626] text-white shadow-lg transition-transform hover:scale-105">
          <PhoneOff size={22} />
        </button>
        <button
          onClick={() => setTyping((t) => !t)}
          aria-label="Type instead"
          aria-pressed={typing}
          className={`grid size-14 place-items-center rounded-full border ${typing ? "border-accent text-accent" : "border-line bg-panel"}`}
        >
          <Keyboard size={20} />
        </button>
      </footer>
      <p className="pb-4 text-center text-[11px] text-muted">Talk any time. You can interrupt Sam. Headphones work best.</p>
    </div>
  );
}
