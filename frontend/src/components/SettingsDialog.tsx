"use client";

import { Check, Eye, EyeOff, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { apiFetch, loadKeys, saveKeys, type KeyName } from "@/lib/keys";

const KEYS: { id: KeyName; label: string; help: string; url: string; placeholder: string }[] = [
  {
    id: "groq",
    label: "Groq API key",
    help: "Voice calls and the GPT-OSS model. Free.",
    url: "https://console.groq.com/keys",
    placeholder: "gsk_…",
  },
  {
    id: "gemini",
    label: "Gemini API key",
    help: "Optional. Gemini for text chat. Free tier.",
    url: "https://aistudio.google.com/apikey",
    placeholder: "AIza… or AQ.…",
  },
];

type Source = "yours" | "server" | "none";
type Check = { state: "idle" | "testing" | "ok" | "bad"; error?: string };

export function SettingsDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [values, setValues] = useState<Partial<Record<KeyName, string>>>({});
  const [show, setShow] = useState<Partial<Record<KeyName, boolean>>>({});
  const [sources, setSources] = useState<Partial<Record<KeyName, Source>>>({});
  const [checks, setChecks] = useState<Partial<Record<KeyName, Check>>>({});

  const refreshSources = () =>
    apiFetch("/api/keys")
      .then((r) => r.json())
      .then(setSources)
      .catch(() => {});

  useEffect(() => {
    setValues(loadKeys());
    refreshSources();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  async function test(id: KeyName) {
    setChecks((c) => ({ ...c, [id]: { state: "testing" } }));
    const header = id === "groq" ? "X-Groq-Key" : "X-Gemini-Key";
    try {
      // Test what's typed, even before saving.
      const res = await fetch("/api/keys/check", {
        method: "POST",
        headers: { "content-type": "application/json", ...(values[id]?.trim() ? { [header]: values[id]!.trim() } : {}) },
        body: JSON.stringify({ provider: id }),
      });
      const r = await res.json();
      setChecks((c) => ({ ...c, [id]: r.ok ? { state: "ok" } : { state: "bad", error: r.error } }));
    } catch (e) {
      setChecks((c) => ({ ...c, [id]: { state: "bad", error: (e as Error).message } }));
    }
  }

  function save() {
    saveKeys(values);
    refreshSources();
    onSaved();
    onClose();
  }

  return (
    <div className="fixed inset-0 z-[60] grid place-items-center bg-black/40 px-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-2xl border border-line bg-bg p-5 shadow-2xl"
      >
        <div className="mb-1 flex items-center">
          <h2 id="settings-title" className="text-[15px] font-semibold">
            API keys
          </h2>
          <button onClick={onClose} className="ml-auto rounded-md p-1 text-muted hover:text-ink" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <p className="mb-5 text-[12px] leading-relaxed text-muted">
          Use your own free keys. They stay in this browser and are sent with your requests only; the server never
          stores them. Leave a field empty to use the server&apos;s key, if it has one.
        </p>

        <div className="space-y-5">
          {KEYS.map((k) => {
            const check = checks[k.id];
            const source = sources[k.id];
            return (
              <div key={k.id}>
                <div className="mb-1.5 flex items-baseline gap-2">
                  <label htmlFor={`key-${k.id}`} className="text-[13px] font-medium">
                    {k.label}
                  </label>
                  <span className="text-[11px] text-muted">
                    {source === "yours" ? "using yours" : source === "server" ? "using the server's" : "not set"}
                  </span>
                  <a href={k.url} target="_blank" rel="noreferrer" className="ml-auto text-[11px] text-accent hover:underline">
                    Get a key
                  </a>
                </div>
                <div className="flex gap-2">
                  <div className="flex flex-1 items-center rounded-lg border border-line bg-panel focus-within:border-accent">
                    <input
                      id={`key-${k.id}`}
                      type={show[k.id] ? "text" : "password"}
                      autoComplete="off"
                      spellCheck={false}
                      value={values[k.id] ?? ""}
                      onChange={(e) => {
                        setValues((v) => ({ ...v, [k.id]: e.target.value }));
                        setChecks((c) => ({ ...c, [k.id]: { state: "idle" } }));
                      }}
                      placeholder={k.placeholder}
                      className="min-w-0 flex-1 bg-transparent px-3 py-2 font-mono text-[13px] outline-none placeholder:text-muted"
                    />
                    <button
                      type="button"
                      onClick={() => setShow((s) => ({ ...s, [k.id]: !s[k.id] }))}
                      className="px-2 text-muted hover:text-ink"
                      aria-label={show[k.id] ? "Hide key" : "Show key"}
                    >
                      {show[k.id] ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                  <button
                    type="button"
                    onClick={() => test(k.id)}
                    className="flex w-16 items-center justify-center rounded-lg border border-line text-[12px] text-muted hover:text-ink"
                  >
                    {check?.state === "testing" ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : check?.state === "ok" ? (
                      <Check size={14} className="text-ok" />
                    ) : (
                      "Test"
                    )}
                  </button>
                </div>
                <p className={`mt-1 text-[11px] ${check?.state === "bad" ? "text-bad" : "text-muted"}`}>
                  {check?.state === "bad" ? check.error : check?.state === "ok" ? "Works." : k.help}
                </p>
              </div>
            );
          })}
        </div>

        <div className="mt-6 flex items-center gap-2">
          <button
            onClick={() => setValues({})}
            className="rounded-lg px-3 py-2 text-[13px] text-muted hover:text-ink"
          >
            Clear
          </button>
          <button onClick={save} className="ml-auto rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-bg">
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
