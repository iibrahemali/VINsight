"use client";

import { Menu, Phone } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PendingAttachment } from "@/components/AttachmentCard";
import { Composer } from "@/components/Composer";
import { PersonaIcon } from "@/components/PersonaIcon";
import { SettingsDialog } from "@/components/SettingsDialog";
import { Sidebar } from "@/components/Sidebar";
import { TurnView, type Turn } from "@/components/TurnView";
import { VoiceCall } from "@/components/VoiceCall";
import { loadKokoro } from "@/lib/voice";
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const sessionId = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const busy = turns.at(-1)?.pending ?? false;
  const active = PERSONAS.find((p) => p.id === persona)!;

  // Which models are usable depends on the keys available (the visitor's or the server's).
  const loadModels = useCallback(() => {
    fetchModels()
      .then((r) => {
        setModels(r.models);
        setModel((m) => (m && r.models.some((x) => x.id === m) ? m : r.default));
      })
      .catch(() => {});
  }, []);
  useEffect(loadModels, [loadModels]);

  // Start fetching the in-browser fallback voice as soon as the Advisor is picked, so it's ready if needed.
  useEffect(() => {
    if (persona === "advisor") loadKokoro();
  }, [persona]);

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
    const modelLabel = models.find((m) => m.id === model)?.label;
    setTurns((t) => [...t, { user: text, persona, attachments, events: [], pending: true, modelLabel, startedAt: Date.now() }]);
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
        onEvent: (event) => {
          // "done" means the answer is complete: finish the turn now rather than waiting for the
          // connection to close, so a proxy that holds it open can never freeze the UI.
          const finished = event.type === "done";
          update((turn) => ({ ...turn, events: [...turn.events, event], pending: finished ? false : turn.pending }));
          if (finished) setTimeout(() => ctrl.abort(), 0);
        },
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
    <div className="flex min-h-dvh">
      <Sidebar
        persona={persona}
        onPersona={setPersona}
        onNewChat={newChat}
        canReset={turns.length > 0}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        onSettings={() => setSettingsOpen(true)}
      />
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} onSaved={loadModels} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 bg-bg/80 px-4 py-3 backdrop-blur md:hidden">
          <button onClick={() => setMenuOpen(true)} className="rounded-md p-1 text-muted" aria-label="Open menu">
            <Menu size={18} />
          </button>
          <span className="flex items-center gap-1.5 text-[14px] font-medium">
            <PersonaIcon id={persona} size={15} className="text-accent" /> {active.name}
          </span>
        </header>

        <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col px-4">
          <section className="flex-1 space-y-10 pb-8 pt-8 md:pt-14">
            {turns.length === 0 && (
              <div className="pt-[12vh]">
                <p className="flex items-center gap-2 text-[13px] text-muted">
                  <PersonaIcon id={persona} size={14} className="text-accent" /> {active.name}
                </p>
                <h1 className="mt-2 text-2xl font-semibold tracking-tight">{active.tagline}</h1>
                {active.examples.length > 0 && (
                  <div className="mt-6 flex flex-col items-start gap-1">
                    {active.examples.map((ex) => (
                      <button
                        key={ex}
                        onClick={() => send(ex)}
                        className="text-left text-[14px] text-muted underline-offset-4 transition-colors hover:text-ink hover:underline"
                      >
                        {ex} →
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {turns.map((turn, i) => (
              <TurnView key={i} turn={turn} verified={verified} />
            ))}
            <div ref={bottom} />
          </section>

          <div className="sticky bottom-0 bg-bg pb-5 pt-3">
            {active.voice ? (
              <div className="flex items-center gap-4 rounded-2xl border border-line p-4">
                <div className="min-w-0 flex-1">
                  <p className="text-[14px] font-medium">Talk to Sam, your car advisor</p>
                  <p className="text-[12px] text-muted">Hands-free voice call. Interrupt any time.</p>
                </div>
                <button
                  onClick={() => setInCall(true)}
                  disabled={busy}
                  className="flex items-center gap-2 rounded-full bg-accent px-4 py-2 text-[14px] font-medium text-bg disabled:opacity-40"
                >
                  <Phone size={15} /> Start call
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
      </div>
      {inCall && (
        <VoiceCall
          sessionId={sessionId}
          model={models.some((m) => m.id === "gpt-oss-120b") ? "gpt-oss-120b" : model}
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
