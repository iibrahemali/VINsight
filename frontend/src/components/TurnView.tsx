import { AlertTriangle, ArrowRightLeft, OctagonX, PhoneCall } from "lucide-react";
import { useEffect, useState } from "react";

import { Answer } from "@/components/Answer";
import { AttachmentCard, type PendingAttachment } from "@/components/AttachmentCard";
import { BriefCard } from "@/components/BriefCard";
import { ListingXray, VerdictHeader, splitVerdict, type Xray } from "@/components/ListingXray";
import { PersonaIcon } from "@/components/PersonaIcon";
import { Thinking } from "@/components/Thinking";
import { ToolStep } from "@/components/ToolStep";
import type { CallSummary } from "@/components/VoiceCall";
import { PERSONAS, type PersonaId, type TraceEvent } from "@/lib/agent";
import { toBlocks } from "@/lib/blocks";

export type Turn = {
  user: string;
  persona: PersonaId;
  attachments: PendingAttachment[];
  events: TraceEvent[];
  pending: boolean;
  stopped?: boolean;
  call?: CallSummary; // an advisor voice call, summarized into the chat when it ends
  modelLabel?: string; // the model picked when this turn was sent
  startedAt?: number;
};

function CallView({ call }: { call: CallSummary }) {
  const avg = call.latencies.length ? call.latencies.reduce((a, b) => a + b, 0) / call.latencies.length / 1000 : null;
  return (
    <article className="space-y-3">
      <div className="flex items-center gap-2 text-[12px] font-medium text-accent">
        <span className="grid size-5 place-items-center rounded-md bg-accent/10">
          <PhoneCall size={12} />
        </span>
        Call with Sam
        <span className="font-normal text-muted">
          · {Math.floor(call.seconds / 60)}m {call.seconds % 60}s{avg !== null && ` · ~${avg.toFixed(1)}s average reply`}
        </span>
      </div>
      <BriefCard profile={call.profile} />
      {call.transcript.length > 0 && (
        <details className="rounded-xl border border-line bg-panel/60 px-3 py-2 text-[13px]">
          <summary className="cursor-pointer text-muted">Transcript ({call.transcript.length} lines)</summary>
          <div className="mt-2 space-y-1.5">
            {call.transcript.map((l, i) => (
              <p key={i}>
                <span className={`font-medium ${l.who === "sam" ? "text-accent" : ""}`}>{l.who === "sam" ? "Sam" : "You"}:</span> {l.text}
              </p>
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

// Shown only until the first reasoning token arrives, so it's never a dead "loading" state.
function Waiting({ label, since }: { label?: string; since?: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, []);
  const s = since ? Math.floor((now - since) / 1000) : 0;
  return (
    <div className="flex items-center gap-2 text-[13px] text-muted">
      <span className="dot-pulse" />
      <span className="shimmer">Waiting for {label ?? "the model"}…</span>
      {s > 0 && <span className="font-mono text-[11px]">{s}s</span>}
    </div>
  );
}

export function TurnView({ turn, verified }: { turn: Turn; verified: Set<string> }) {
  if (turn.call) return <CallView call={turn.call} />;
  const blocks = toBlocks(turn.events);
  const persona = PERSONAS.find((p) => p.id === turn.persona);
  const lastText = blocks.findLastIndex((b) => b.kind === "text");
  const done = blocks.find((b) => b.kind === "done");
  // Between steps (after a tool finishes, or a notice) the model is working but nothing streams yet:
  // show that, rather than a screen that looks frozen.
  const last = blocks.at(-1);
  const between = last && ((last.kind === "tool" && last.result) || last.kind === "notice");
  const waiting = turn.pending && (blocks.length === 0 || between);
  const lastAt = turn.events.at(-1)?.at;

  return (
    <article className="space-y-3">
      <div className="ml-auto flex max-w-[85%] flex-col items-end gap-2">
        {turn.attachments.length > 0 && (
          <div className="flex flex-wrap justify-end gap-2">
            {turn.attachments.map((a) => (
              <AttachmentCard key={a.key} item={a} compact />
            ))}
          </div>
        )}
        {turn.user && (
          <p className="whitespace-pre-wrap rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-[14px] text-bg">{turn.user}</p>
        )}
      </div>

      <div className="flex items-center gap-2 text-[12px] font-medium text-accent">
        <span className="grid size-5 place-items-center rounded-md bg-accent/10">
          <PersonaIcon id={turn.persona} size={12} />
        </span>
        {persona?.name}
        {done?.kind === "done" && done.event.model && <span className="font-normal text-muted">· {done.event.model}</span>}
      </div>



      <div className="space-y-2.5">
        {blocks.map((b, i) => {
          switch (b.kind) {
            case "thinking":
              return <Thinking key={i} text={b.text} start={b.start} end={b.end} live={turn.pending} />;
            case "tool":
              return (
                <div key={i} className="space-y-2">
                  <ToolStep name={b.name} input={b.input} result={b.result} live={turn.pending} />
                  {b.name === "analyze_listing" && b.result?.ok && b.result.output != null && (
                    <ListingXray x={b.result.output as Xray} />
                  )}
                  {b.name === "read_link" && b.result?.ok && (b.result.output as { listing?: Xray })?.listing && (
                    <ListingXray x={(b.result.output as { listing: Xray }).listing} />
                  )}
                </div>
              );
            case "text": {
              const { verdict, headline, rest } = splitVerdict(b.text);
              return (
                <div key={i} className="space-y-3">
                  {verdict && <VerdictHeader verdict={verdict} headline={headline} />}
                  <Answer text={rest} verified={verified} streaming={turn.pending && i === lastText && i === blocks.length - 1} />
                </div>
              );
            }
            case "notice":
              return (
                <p key={i} className="flex items-start gap-2 rounded-lg bg-accent/10 px-3 py-2 text-[12px] text-accent">
                  <ArrowRightLeft size={13} className="mt-0.5 shrink-0" /> {b.text}
                </p>
              );
            case "error":
              return b.text === "Stopped." ? null : (
                <p key={i} className="flex items-start gap-2 rounded-lg bg-bad/10 px-3 py-2 text-[12px] text-bad">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {b.text}
                </p>
              );
            case "done":
              return (
                <p key={i} className="font-mono text-[11px] text-muted">
                  {((b.event.ms ?? 0) / 1000).toFixed(1)}s · {(b.event.usage?.input_tokens ?? 0).toLocaleString()} in /{" "}
                  {(b.event.usage?.output_tokens ?? 0).toLocaleString()} out
                </p>
              );
          }
        })}
        {waiting && <Waiting label={turn.modelLabel} since={blocks.length ? lastAt : turn.startedAt} />}
        {turn.stopped && (
          <p className="flex items-center gap-1.5 text-[12px] text-muted">
            <OctagonX size={13} /> Stopped
          </p>
        )}
      </div>
    </article>
  );
}
