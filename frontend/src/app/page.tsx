"use client";

import { Headset, Phone, ShieldCheck, SquarePen } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { PendingAttachment } from "@/components/AttachmentCard";
import { Composer } from "@/components/Composer";
import { Logo, LogoMark } from "@/components/Logo";
import { PersonaIcon } from "@/components/PersonaIcon";
import { TurnView, type Turn } from "@/components/TurnView";
import { VoiceCall } from "@/components/VoiceCall";
import {
  PERSONAS,
  fetchModels,
  sourceIds,
  stopSession,
  streamChat,
  type ModelOption,
  type PersonaId,
} from "@/lib/agent";

export default function Home() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [persona, setPersona] = useState<PersonaId>("inspector");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const [inCall, setInCall] = useState(false);
  const sessionId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.at(-1)?.pending ?? false;
  const active = PERSONAS.find((p) => p.id === persona)!;

  useEffect(() => {
    fetchModels()
      .then((r) => {
        setModels(r.models);
        setModel((m) => m ?? r.default);
      })
      .catch(() => {});
  }, []);

  // Citations can point at tool results or attachments from any earlier turn.
  const verified = useMemo(
    () =>
      sourceIds(
        turns.flatMap((t) => t.events),
        turns.flatMap((t) => t.attachments.map((a) => a.card?.id ?? "")),
      ),
    [turns],
  );

  // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect may only return a cleanup.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const update = (fn: (turn: Turn) => Turn) => setTurns((t) => [...t.slice(0, -1), fn(t.at(-1)!)]);

  async function send(text: string, attachments: PendingAttachment[] = []) {
    if (busy || (!text && !attachments.length)) return;
    const message = text || "Here's a listing. Is it worth a look?";
    setTurns((t) => [...t, { user: text, persona, attachments, events: [], pending: true }]);
    const ctrl = new AbortController();
    controller.current = ctrl;
    try {
      await streamChat({
        message,
        persona,
        provider: model,
        attachments: attachments.map((a) => a.card!.id),
        sessionId: sessionId.current,
        signal: ctrl.signal,
        onSession: (id) => (sessionId.current = id),
        onEvent: (event) => update((turn) => ({ ...turn, events: [...turn.events, event] })),
      });
    } catch (err) {
      if (!ctrl.signal.aborted) {
        const text = `Could not reach the backend (${(err as Error).message}). Is it running on :8000?`;
        update((turn) => ({ ...turn, events: [...turn.events, { type: "error", turn: 0, ok: false, text }] }));
      }
    } finally {
      update((turn) => ({ ...turn, pending: false }));
      controller.current = null;
    }
  }

  function stop() {
    if (sessionId.current) stopSession(sessionId.current);
    controller.current?.abort();
    update((turn) => ({ ...turn, pending: false, stopped: true }));
  }

  function newChat() {
    if (busy) stop();
    sessionId.current = null;
    setTurns([]);
  }

  return (
    <div className="relative min-h-dvh">
      <div className="glow" aria-hidden="true" />
      <header className="sticky top-0 z-30 border-b border-line/60 bg-bg/70 backdrop-blur-md">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3">
          <button onClick={newChat} className="rounded-lg" aria-label="VINsight home">
            <Logo />
          </button>
          <nav className="ml-auto flex items-center gap-1 rounded-xl border border-line bg-panel p-1" aria-label="Persona">
            {PERSONAS.map((p) => (
              <button
                key={p.id}
                onClick={() => setPersona(p.id)}
                aria-pressed={p.id === persona}
                title={p.tagline}
                className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[13px] transition-colors ${
                  p.id === persona ? "bg-accent text-bg" : "text-muted hover:text-ink"
                }`}
              >
                <PersonaIcon id={p.id} size={14} />
                <span className="hidden sm:inline">{p.name}</span>
              </button>
            ))}
          </nav>
          <button
            onClick={newChat}
            disabled={!turns.length}
            title="New chat"
            aria-label="New chat"
            className="rounded-lg p-2 text-muted hover:bg-panel hover:text-ink disabled:opacity-30"
          >
            <SquarePen size={17} />
          </button>
        </div>
      </header>

      <main className="relative mx-auto flex max-w-3xl flex-col px-4">
        <section className="flex-1 space-y-10 pb-8 pt-8">
          {turns.length === 0 && (
            <div className="pt-6 text-center sm:pt-12">
              <div className="mx-auto mb-5 w-fit">
                <LogoMark size={56} />
              </div>
              <h1 className="text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
                Know the car before you buy it.
              </h1>
              <p className="mx-auto mt-3 max-w-lg text-balance text-muted">
                Paste a VIN, drop a listing link or PDF. VINsight pulls NHTSA recalls and owner complaints, reasons out
                loud, and cites every fact.
              </p>

              <div className="mt-8 grid grid-cols-2 gap-3 text-left sm:grid-cols-4">
                {PERSONAS.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => setPersona(p.id)}
                    className={`rounded-2xl border p-4 transition-all ${
                      p.id === persona ? "border-accent bg-panel shadow-lg shadow-accent/10" : "border-line bg-panel/50 hover:border-muted"
                    }`}
                  >
                    <span className={`mb-3 grid size-8 place-items-center rounded-lg ${p.id === persona ? "bg-accent text-bg" : "bg-bg text-muted"}`}>
                      <PersonaIcon id={p.id} />
                    </span>
                    <span className="block font-medium">{p.name}</span>
                    <span className="mt-1 block text-[13px] leading-snug text-muted">{p.tagline}</span>
                  </button>
                ))}
              </div>

              {active.voice && (
                <p className="mt-6 text-[13px] text-muted">Press the call button below. Sam will greet you.</p>
              )}
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {active.examples.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => send(ex)}
                    className="rounded-full border border-line bg-panel/60 px-3.5 py-1.5 text-[13px] text-muted transition-colors hover:border-accent hover:text-ink"
                  >
                    {ex}
                  </button>
                ))}
              </div>

              <p className="mt-8 flex items-center justify-center gap-1.5 text-[12px] text-muted">
                <ShieldCheck size={13} className="text-ok" /> Green citations were verified against tool results in your
                session.
              </p>
            </div>
          )}

          {turns.map((turn, i) => (
            <TurnView key={i} turn={turn} verified={verified} />
          ))}
          <div ref={bottom} />
        </section>

        <div className="sticky bottom-0 bg-gradient-to-t from-bg via-bg to-transparent pb-5 pt-6">
          {active.voice ? (
            <div className="flex items-center gap-4 rounded-2xl border border-line bg-panel p-4 shadow-[0_8px_30px_-12px_rgba(0,0,0,0.25)]">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent/10 text-accent">
                <Headset size={20} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[14px] font-medium">Talk to Sam, your car advisor</p>
                <p className="text-[12px] text-muted">A hands-free voice call. Free: Whisper and GPT-OSS on Groq, voice in your browser.</p>
              </div>
              <button
                onClick={() => setInCall(true)}
                disabled={busy}
                className="flex items-center gap-2 rounded-full bg-accent px-4 py-2.5 text-[14px] font-medium text-bg transition-transform hover:scale-105 disabled:opacity-40"
              >
                <Phone size={16} /> Start call
              </button>
            </div>
          ) : (
          <Composer
            busy={busy}
            persona={persona}
            placeholder={persona === "inspector" ? "Paste a VIN, a listing link, or drop a PDF…" : `Ask ${active.name}…`}
            models={models}
            model={model}
            onModel={setModel}
            onPersona={setPersona}
            onSend={send}
            onStop={stop}
          />
          )}
          <p className="mt-2 text-center text-[11px] text-muted">
            Recall data covers the model, not this exact car. Always get a pre-purchase inspection.
          </p>
        </div>
      </main>
      {inCall && (
        <VoiceCall
          sessionId={sessionId}
          model={models.some((m) => m.id === "groq") ? "groq" : model}
          onEnd={(summary) => {
            setInCall(false);
            if (summary.transcript.length) {
              setTurns((t) => [...t, { user: "", persona: "advisor", attachments: [], events: [], pending: false, call: summary }]);
            }
          }}
        />
      )}
    </div>
  );
}
