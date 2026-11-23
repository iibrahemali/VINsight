import type { TraceEvent } from "@/lib/agent";

// A turn's event stream folded into what the UI draws: streamed thinking and text runs,
// tool steps paired with their results, and notices.
export type Block =
  | { kind: "thinking"; text: string; start: number; end?: number }
  | { kind: "text"; text: string }
  | { kind: "tool"; name: string; input?: Record<string, unknown>; result?: TraceEvent }
  | { kind: "notice" | "error"; text: string }
  | { kind: "done"; event: TraceEvent };

export function toBlocks(events: TraceEvent[]): Block[] {
  const blocks: Block[] = [];
  for (const e of events) {
    const last = blocks.at(-1);
    // Any new block closes a running thinking block, so "thought for Ns" can be measured.
    if (last?.kind === "thinking" && last.end === undefined && e.type !== "thinking") last.end = e.at;

    if (e.type === "thinking") {
      if (last?.kind === "thinking" && last.end === undefined) last.text += e.text ?? "";
      else blocks.push({ kind: "thinking", text: e.text ?? "", start: e.at ?? Date.now() });
    } else if (e.type === "text") {
      if (last?.kind === "text") last.text += e.text ?? "";
      else blocks.push({ kind: "text", text: e.text ?? "" });
    } else if (e.type === "tool_call") {
      blocks.push({ kind: "tool", name: e.name ?? "tool", input: e.input });
    } else if (e.type === "tool_result") {
      const open = blocks.find((b) => b.kind === "tool" && b.name === e.name && !b.result);
      if (open && open.kind === "tool") open.result = e;
    } else if (e.type === "notice" || e.type === "error") {
      blocks.push({ kind: e.type, text: e.text ?? "" });
    } else if (e.type === "profile" || e.type === "wait") {
      continue; // profile: shown by the call UI; wait: only in the live waiting indicator
    } else if (e.type === "done") {
      blocks.push({ kind: "done", event: e });
    }
  }
  return blocks;
}
