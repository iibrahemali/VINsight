"use client";

import { Settings, SquarePen, X } from "lucide-react";

import { Logo } from "@/components/Logo";
import { PersonaIcon } from "@/components/PersonaIcon";
import { PERSONAS, type PersonaId } from "@/lib/agent";

export function Sidebar(props: {
  persona: PersonaId;
  onPersona: (p: PersonaId) => void;
  onNewChat: () => void;
  canReset: boolean;
  open: boolean; // mobile drawer
  onClose: () => void;
  onSettings: () => void;
}) {
  return (
    <>
      {props.open && <div className="fixed inset-0 z-40 bg-black/30 md:hidden" onClick={props.onClose} aria-hidden="true" />}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-60 flex-col border-r border-line bg-bg px-3 py-4 transition-transform md:sticky md:top-0 md:h-dvh md:translate-x-0 ${
          props.open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="mb-6 flex items-center px-2">
          <Logo size={24} />
          <button onClick={props.onClose} className="ml-auto rounded-md p-1 text-muted md:hidden" aria-label="Close menu">
            <X size={16} />
          </button>
        </div>

        <button
          onClick={props.onNewChat}
          disabled={!props.canReset}
          className="mb-6 flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-muted transition-colors hover:bg-panel hover:text-ink disabled:opacity-40 disabled:hover:bg-transparent"
        >
          <SquarePen size={15} /> New chat
        </button>

        <p className="mb-1.5 px-2 text-[11px] uppercase tracking-wider text-muted">Modes</p>
        <nav className="space-y-0.5" aria-label="Modes">
          {PERSONAS.map((p) => {
            const active = p.id === props.persona;
            return (
              <button
                key={p.id}
                onClick={() => {
                  props.onPersona(p.id);
                  props.onClose();
                }}
                aria-current={active ? "page" : undefined}
                title={p.tagline}
                className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[14px] transition-colors ${
                  active ? "bg-panel font-medium text-ink" : "text-muted hover:bg-panel/60 hover:text-ink"
                }`}
              >
                <PersonaIcon id={p.id} size={15} className={active ? "text-accent" : ""} />
                {p.name}
              </button>
            );
          })}
        </nav>

        <button
          onClick={() => {
            props.onSettings();
            props.onClose();
          }}
          className="mt-auto flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-muted transition-colors hover:bg-panel hover:text-ink"
        >
          <Settings size={15} /> API keys
        </button>
        <p className="mt-2 px-2 text-[11px] leading-relaxed text-muted">Data from NHTSA. Open source and free to run.</p>
      </aside>
    </>
  );
}
