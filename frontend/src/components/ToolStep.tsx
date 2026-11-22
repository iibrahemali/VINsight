import { CalendarRange, Check, FileWarning, Globe, Loader2, MessageSquareWarning, ScanLine, ScanSearch, X } from "lucide-react";

import type { TraceEvent } from "@/lib/agent";

type Out = Record<string, unknown>;

const TOOLS: Record<string, { running: string; done: string; icon: typeof ScanLine; summary: (o: Out) => string }> = {
  decode_vin: {
    running: "Decoding VIN",
    done: "Decoded VIN",
    icon: ScanLine,
    summary: (o) => [o.year, o.make, o.model, o.trim].filter(Boolean).join(" "),
  },
  get_recalls: {
    running: "Pulling NHTSA recalls",
    done: "Recalls",
    icon: FileWarning,
    summary: (o) => `${o.count ?? 0} on record`,
  },
  get_complaints: {
    running: "Reading owner complaints",
    done: "Complaints",
    icon: MessageSquareWarning,
    summary: (o) => `${Number(o.total ?? 0).toLocaleString()} filed`,
  },
  read_link: {
    running: "Reading the link",
    done: "Read link",
    icon: Globe,
    summary: (o) =>
      o.status === "ok"
        ? [o.site, o.title].filter(Boolean).join(" · ")
        : o.status === "partial"
          ? `preview only · ${o.title ?? o.site}`
          : `${o.site ?? "site"} blocked bots: paste the text instead`,
  },
  analyze_listing: {
    running: "X-raying the listing",
    done: "Listing X-ray",
    icon: ScanSearch,
    summary: (o) => {
      const n = ((o.flags as unknown[]) ?? []).length;
      return `${n} flag${n === 1 ? "" : "s"}`;
    },
  },
  compare_model_years: {
    running: "Comparing model years",
    done: "Model years",
    icon: CalendarRange,
    summary: (o) => {
      const years = (o.years as { year: number }[] | undefined) ?? [];
      return years.length ? `${years[0].year}–${years.at(-1)!.year}` : "";
    },
  },
};

export function ToolStep({ name, input, result, live }: { name: string; input?: Out; result?: TraceEvent; live: boolean }) {
  const spec = TOOLS[name] ?? { running: name, done: name, icon: ScanLine, summary: () => "" };
  const Icon = spec.icon;
  const pending = !result;
  const ok = result?.ok ?? true;
  const output = result?.output && typeof result.output === "object" ? (result.output as Out) : null;
  // Tool errors arrive as "Error executing tool <name>: <reason>"; the reason is what matters.
  const error = String(result?.output ?? "").replace(/^Error executing tool \w+:\s*/, "");
  const detail = pending ? Object.values(input ?? {}).join(" · ") : ok && output ? spec.summary(output) : error;

  return (
    <div className="flex items-center gap-2.5 text-[13px]">
      <span
        className={`grid size-6 shrink-0 place-items-center rounded-md border ${
          pending ? "border-accent/40 text-accent" : ok ? "border-line text-muted" : "border-bad/50 text-bad"
        }`}
      >
        <Icon size={13} />
      </span>
      <span className={pending && live ? "shimmer font-medium" : "font-medium"}>{pending ? spec.running : spec.done}</span>
      <span className="truncate text-muted">{detail}</span>
      <span className="ml-auto flex shrink-0 items-center gap-1 font-mono text-[11px] text-muted">
        {pending ? (
          live ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />
        ) : (
          <>
            {ok ? <Check size={12} className="text-ok" /> : <X size={12} className="text-bad" />}
            {Math.round(result?.ms ?? 0)} ms
          </>
        )}
      </span>
    </div>
  );
}
