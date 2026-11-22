"use client";

import { ArrowUp, Link2, Paperclip, ScanLine, Square, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { AttachmentCard, type PendingAttachment } from "@/components/AttachmentCard";
import { ModelMenu } from "@/components/ModelMenu";
import { addLink, findVin, isUrl, uploadFile, type ModelOption, type PersonaId } from "@/lib/agent";

const ACCEPT = ".pdf,.txt,.md,.csv,.html,.htm";
let counter = 0;

export function Composer(props: {
  busy: boolean;
  persona: PersonaId;
  placeholder: string;
  models: ModelOption[];
  model: string | null;
  onModel: (id: string) => void;
  onPersona: (p: PersonaId) => void;
  onSend: (text: string, attachments: PendingAttachment[]) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<PendingAttachment[]>([]);
  const [linkMode, setLinkMode] = useState(false);
  const [link, setLink] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const textArea = useRef<HTMLTextAreaElement>(null);

  const vin = findVin(text);
  const loading = items.some((i) => !i.card && !i.error);
  const canSend = !props.busy && !loading && (text.trim() || items.some((i) => i.card));

  function track(kind: PendingAttachment["kind"], label: string, work: Promise<PendingAttachment["card"]>) {
    const key = `a${++counter}`;
    setItems((xs) => [...xs, { key, kind, label }]);
    work
      .then((card) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, card } : x))))
      .catch((e: Error) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, error: e.message } : x))));
  }

  const attachLink = (url: string) => track("link", url.replace(/^https?:\/\//, ""), addLink(url));
  const attachFiles = (files: FileList | File[]) => Array.from(files).forEach((f) => track("file", f.name, uploadFile(f)));

  function submit() {
    if (!canSend) return;
    props.onSend(text.trim(), items.filter((i) => i.card));
    setText("");
    setItems([]);
    textArea.current?.focus();
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) attachFiles(e.dataTransfer.files);
      }}
      className={`relative rounded-2xl border bg-panel transition-colors ${
        dragging ? "border-accent" : "border-line focus-within:border-accent/60"
      }`}
    >
      {dragging && (
        <div className="absolute inset-0 z-10 grid place-items-center rounded-2xl bg-panel/90 text-sm text-accent">
          <span className="flex items-center gap-2">
            <Upload size={16} /> Drop PDFs, listings or notes
          </span>
        </div>
      )}

      {linkMode && (
        <div className="link-field m-2 mb-0 flex items-center gap-2 rounded-xl px-3 py-2">
          <Link2 size={15} className="shrink-0 text-accent" />
          <input
            autoFocus
            value={link}
            onChange={(e) => setLink(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (link.trim()) attachLink(link.trim());
                setLink("");
                setLinkMode(false);
              } else if (e.key === "Escape") setLinkMode(false);
            }}
            placeholder="Paste a listing link: Craigslist, Cars.com, a dealer page, a PDF…"
            className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-muted"
          />
          <button
            type="button"
            disabled={!link.trim()}
            onClick={() => {
              attachLink(link.trim());
              setLink("");
              setLinkMode(false);
            }}
            className="rounded-lg bg-accent px-2.5 py-1 text-[12px] font-medium text-bg disabled:opacity-40"
          >
            Read it
          </button>
        </div>
      )}

      {items.length > 0 && (
        <div className="flex gap-2 overflow-x-auto px-2 pt-2">
          {items.map((item) => (
            <AttachmentCard key={item.key} item={item} onRemove={() => setItems((xs) => xs.filter((x) => x.key !== item.key))} />
          ))}
        </div>
      )}

      <textarea
        ref={textArea}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onPaste={(e) => {
          const pasted = e.clipboardData.getData("text");
          // A bare link turns into a link card instead of text.
          if (isUrl(pasted) && !text.trim()) {
            e.preventDefault();
            attachLink(pasted.trim());
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
        rows={Math.min(8, Math.max(2, text.split("\n").length))}
        placeholder={props.placeholder}
        className="block w-full resize-none bg-transparent px-4 pb-1 pt-3 text-[15px] outline-none placeholder:text-muted"
      />

      {vin && (
        <div className="mx-3 mb-1 flex items-center gap-2 text-[12px]">
          <span className="inline-flex items-center gap-1 rounded-md bg-accent/10 px-1.5 py-0.5 font-mono text-accent">
            <ScanLine size={12} /> {vin}
          </span>
          {props.persona === "inspector" ? (
            <span className="text-muted">VIN detected · Inspector will decode it</span>
          ) : (
            <button type="button" onClick={() => props.onPersona("inspector")} className="text-accent underline-offset-2 hover:underline">
              Check it with Inspector
            </button>
          )}
        </div>
      )}

      <div className="flex items-center gap-1 px-2 pb-2">
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) attachFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          title="Attach PDF or text file"
          aria-label="Attach file"
          className="rounded-lg p-1.5 text-muted hover:bg-bg hover:text-ink"
        >
          <Paperclip size={16} />
        </button>
        <button
          type="button"
          onClick={() => setLinkMode((m) => !m)}
          title="Add a listing link"
          aria-label="Add link"
          aria-pressed={linkMode}
          className={`rounded-lg p-1.5 hover:bg-bg ${linkMode ? "text-accent" : "text-muted hover:text-ink"}`}
        >
          <Link2 size={16} />
        </button>
        <ModelMenu models={props.models} value={props.model} onChange={props.onModel} />
        <span className="ml-auto" />
        {props.busy ? (
          <button
            type="button"
            onClick={props.onStop}
            aria-label="Stop"
            title="Stop"
            className="grid size-8 place-items-center rounded-full bg-ink text-bg transition-transform hover:scale-105"
          >
            <Square size={12} fill="currentColor" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!canSend}
            aria-label="Send"
            className="grid size-8 place-items-center rounded-full bg-accent text-bg transition-transform enabled:hover:scale-105 disabled:opacity-30"
          >
            <ArrowUp size={16} />
          </button>
        )}
      </div>
    </form>
  );
}
