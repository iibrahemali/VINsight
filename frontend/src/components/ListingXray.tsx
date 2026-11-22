import { CircleCheck, CircleX, ScanSearch, ShieldAlert } from "lucide-react";

type Flag = { code: string; severity: "high" | "medium" | "low"; label: string; quote: string; why: string };
type VinCheck = { vin: string; decoded: string | null; matches: boolean | null; detail: string };
export type Xray = {
  price: number | null;
  mileage: number | null;
  year: number | null;
  make: string | null;
  model: string | null;
  flags: Flag[];
  vin_check: VinCheck | null;
  verdict_floor: "AVOID" | "CAUTION" | "NONE";
};

const SEVERITY = {
  high: { dot: "bg-bad", text: "text-bad", label: "Deal-breaker" },
  medium: { dot: "bg-warn", text: "text-warn", label: "Caution" },
  low: { dot: "bg-muted", text: "text-muted", label: "Note" },
} as const;

// The rule-based listing scan, drawn as an X-ray: what the listing claims, what the VIN says,
// and every red flag with the exact words that triggered it.
export function ListingXray({ x }: { x: Xray }) {
  const facts = [
    [x.year, x.make, x.model].filter(Boolean).join(" ") || null,
    x.price != null ? `$${x.price.toLocaleString()}` : null,
    x.mileage != null ? `${x.mileage.toLocaleString()} mi` : null,
  ].filter(Boolean);
  const flags = [...x.flags].sort((a, b) => "hml".indexOf(a.severity[0]) - "hml".indexOf(b.severity[0]));

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-panel">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <ScanSearch size={15} className="text-accent" />
        <span className="text-[13px] font-medium">Listing X-ray</span>
        <span className="truncate text-[12px] text-muted">{facts.join(" · ")}</span>
        {x.verdict_floor !== "NONE" && (
          <span
            className={`ml-auto shrink-0 rounded-md px-2 py-0.5 text-[11px] font-semibold ${
              x.verdict_floor === "AVOID" ? "bg-bad/15 text-bad" : "bg-warn/15 text-warn"
            }`}
            title="The strictest verdict the rules require"
          >
            at least {x.verdict_floor}
          </span>
        )}
      </div>

      {x.vin_check && (
        <div
          className={`flex items-start gap-2 border-b border-line px-4 py-2 text-[13px] ${
            x.vin_check.matches === false ? "bg-bad/10 text-bad" : ""
          }`}
        >
          {x.vin_check.matches === false ? (
            <CircleX size={15} className="mt-0.5 shrink-0" />
          ) : x.vin_check.matches ? (
            <CircleCheck size={15} className="mt-0.5 shrink-0 text-ok" />
          ) : (
            <ShieldAlert size={15} className="mt-0.5 shrink-0 text-muted" />
          )}
          <span>
            <span className="font-mono text-[12px]">{x.vin_check.vin}</span> · {x.vin_check.detail}
          </span>
        </div>
      )}

      {flags.length === 0 ? (
        <p className="px-4 py-3 text-[13px] text-muted">
          No red flags in the listing text. That&apos;s not proof the car is good, only that the listing doesn&apos;t admit to
          problems.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {flags.map((f) => (
            <li key={f.code} className="px-4 py-2.5">
              <div className="flex items-center gap-2">
                <span className={`size-2 shrink-0 rounded-full ${SEVERITY[f.severity].dot}`} />
                <span className="text-[13px] font-medium">{f.label}</span>
                <span className={`text-[11px] ${SEVERITY[f.severity].text}`}>{SEVERITY[f.severity].label}</span>
              </div>
              {f.quote && <p className="ml-4 mt-1 border-l-2 border-line pl-2 text-[12px] italic text-muted">“{f.quote}”</p>}
              <p className="ml-4 mt-1 text-[12px] text-muted">{f.why}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// "**Verdict: AVOID**" at the top of an answer becomes a badge.
const VERDICT_RE = /^\s*\*\*Verdict:\s*(BUY|CAUTION|AVOID)\*\*\s*\n?/;

export function splitVerdict(text: string): { verdict: "BUY" | "CAUTION" | "AVOID" | null; rest: string } {
  const m = VERDICT_RE.exec(text);
  return m ? { verdict: m[1] as "BUY" | "CAUTION" | "AVOID", rest: text.slice(m[0].length) } : { verdict: null, rest: text };
}

const VERDICT_STYLE = {
  BUY: { cls: "border-ok/40 bg-ok/10 text-ok", sub: "Worth pursuing, pending a mechanic's inspection" },
  CAUTION: { cls: "border-warn/40 bg-warn/10 text-warn", sub: "Only with answers to the questions below" },
  AVOID: { cls: "border-bad/40 bg-bad/10 text-bad", sub: "The risks outweigh the deal" },
} as const;

export function VerdictBadge({ verdict }: { verdict: "BUY" | "CAUTION" | "AVOID" }) {
  const s = VERDICT_STYLE[verdict];
  return (
    <div className={`flex items-baseline gap-3 rounded-2xl border px-4 py-3 ${s.cls}`}>
      <span className="text-xl font-bold tracking-tight">{verdict}</span>
      <span className="text-[13px] opacity-80">{s.sub}</span>
    </div>
  );
}
