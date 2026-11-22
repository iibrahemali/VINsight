import { AlertTriangle, FileText, Globe, Loader2, ScanLine, X } from "lucide-react";

import type { AttachmentCard as Card } from "@/lib/agent";

export type PendingAttachment = { key: string; label: string; kind: "link" | "file"; card?: Card; error?: string };

// Evidence cards for links and files. Links get a preview (site, title, image) and a read status,
// so the user sees immediately whether VINsight could actually read the listing.
export function AttachmentCard({ item, onRemove, compact = false }: { item: PendingAttachment; onRemove?: () => void; compact?: boolean }) {
  const card = item.card;
  const loading = !card && !item.error;
  const partial = card?.status === "partial";
  const problem = item.error ?? (card && card.status !== "ok" && !partial ? card.note : null);
  const isLink = item.kind === "link";
  const title = card?.title || card?.name || item.label;
  const sub = card ? (isLink ? card.site : card.pages ? `PDF · ${card.pages} page${card.pages > 1 ? "s" : ""}` : "Text") : item.label;

  return (
    <div
      className={`group relative flex shrink-0 overflow-hidden rounded-xl border bg-panel ${
        problem ? "border-warn/50" : "border-line"
      } ${compact ? "w-56" : "w-72"}`}
    >
      {loading && <div className="scan-line" aria-hidden="true" />}
      <div className="grid w-14 shrink-0 place-items-center overflow-hidden border-r border-line bg-bg">
        {card?.image ? (
          // eslint-disable-next-line @next/next/no-img-element -- remote listing thumbnails of unknown origin
          <img src={card.image} alt="" className="h-full w-full object-cover" referrerPolicy="no-referrer" />
        ) : loading ? (
          <Loader2 size={16} className="animate-spin text-accent" />
        ) : isLink ? (
          <Globe size={18} className="text-muted" />
        ) : (
          <FileText size={18} className="text-muted" />
        )}
      </div>
      <div className="min-w-0 flex-1 px-3 py-2">
        <p className="truncate text-[13px] font-medium" title={title}>
          {loading ? (isLink ? "Reading page…" : "Reading file…") : title}
        </p>
        <p className="truncate text-[11px] text-muted">{loading ? item.label : sub}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {problem ? (
            <span className="inline-flex items-center gap-1 text-[11px] text-warn" title={problem}>
              <AlertTriangle size={11} /> {isLink ? "Couldn't read · paste text" : "Unreadable"}
            </span>
          ) : partial ? (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted" title={card?.note ?? undefined}>
              Preview read · paste text for more
            </span>
          ) : card ? (
            <span className="text-[11px] text-ok">Read · {(card.chars / 1000).toFixed(1)}k chars</span>
          ) : null}
          {card?.vins.map((v) => (
            <span key={v} className="inline-flex items-center gap-1 rounded bg-accent/10 px-1 font-mono text-[10px] text-accent">
              <ScanLine size={10} /> VIN {v.slice(-6)}
            </span>
          ))}
        </div>
      </div>
      {onRemove && (
        <button
          onClick={onRemove}
          aria-label="Remove attachment"
          className="absolute right-1 top-1 rounded-md p-0.5 text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100"
        >
          <X size={13} />
        </button>
      )}
    </div>
  );
}
