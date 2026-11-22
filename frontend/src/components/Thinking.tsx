"use client";

import { Brain, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

// Live reasoning: streams open while the model thinks, then folds to "Thought for Ns".
export function Thinking({ text, start, end, live }: { text: string; start: number; end?: number; live: boolean }) {
  const running = live && end === undefined;
  const [open, setOpen] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [running]);

  // Fold away once the model moves on, unless the user opened it themselves.
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) setOpen(false);
    wasRunning.current = running;
  }, [running]);

  // Keep the newest reasoning in view while it streams.
  useEffect(() => {
    if (running && body.current) body.current.scrollTop = body.current.scrollHeight;
  }, [text, running]);

  const seconds = Math.max(1, Math.round(((end ?? now) - start) / 1000));

  return (
    <div className="rounded-xl border border-line bg-panel/60">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] text-muted"
        aria-expanded={open}
      >
        <Brain size={14} className={running ? "animate-pulse text-accent" : ""} />
        {running ? (
          <span className="shimmer font-medium">Thinking… {seconds}s</span>
        ) : (
          <span>Thought for {seconds}s</span>
        )}
        <ChevronDown size={14} className={`ml-auto transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div
          ref={body}
          className="max-h-48 overflow-y-auto whitespace-pre-wrap border-t border-line px-3 py-2 text-[13px] leading-relaxed text-muted"
        >
          {text}
          {running && <span className="caret" />}
        </div>
      )}
    </div>
  );
}
