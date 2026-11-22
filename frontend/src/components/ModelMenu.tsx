"use client";

import { Check, ChevronDown, Cpu } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ModelOption } from "@/lib/agent";

export function ModelMenu({ models, value, onChange }: { models: ModelOption[]; value: string | null; onChange: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const current = models.find((m) => m.id === value);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (!models.length) return null;
  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-[13px] text-muted hover:bg-bg hover:text-ink"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <Cpu size={14} />
        {current?.label ?? "Model"}
        <ChevronDown size={13} />
      </button>
      {open && (
        <ul role="listbox" className="absolute bottom-full left-0 z-20 mb-2 w-64 rounded-xl border border-line bg-panel p-1 shadow-xl">
          {models.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                role="option"
                aria-selected={m.id === value}
                onClick={() => {
                  onChange(m.id);
                  setOpen(false);
                }}
                className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-bg"
              >
                <span className="flex-1">
                  <span className="block text-[13px] font-medium">{m.label}</span>
                  <span className="block text-[11px] text-muted">{m.detail}</span>
                </span>
                {m.id === value && <Check size={14} className="mt-0.5 text-accent" />}
              </button>
            </li>
          ))}
          <li className="px-2.5 pb-1.5 pt-1 text-[11px] text-muted">Switching keeps the conversation. If one is down, the other takes over.</li>
        </ul>
      )}
    </div>
  );
}
