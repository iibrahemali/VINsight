import { Check, CircleHelp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { citationKey, citationUrl, linkCitations } from "@/lib/agent";

type Cite = { kind: string; id: string; n: number };

const title = (s: string) => s.replace(/\b\w/g, (c) => c.toUpperCase());

// What a source is, in words, for the Sources list.
function describe(kind: string, id: string): string {
  const parts = id.split(":");
  switch (kind) {
    case "recall":
      return `NHTSA recall ${id}`;
    case "vpic":
      return `VIN decode · ${id}`;
    case "complaint":
      return `Owner complaint #${id}`;
    case "complaints":
      return `Owner complaints · ${title(parts.slice(0, -1).join(" ").toLowerCase())} ${parts.at(-1)}`;
    case "years":
      return `Complaints by year · ${title(parts.slice(0, -1).join(" ").toLowerCase())} ${parts.at(-1)}`;
    case "epa":
      return `EPA fuel economy · ${title(parts.slice(0, -1).join(" ").toLowerCase())} ${parts.at(-1)}`;
    case "doc":
      return "Your attachment (file or page)";
    case "link":
      return "Web page";
    default:
      return `${kind}: ${id}`;
  }
}

/** Citations in order of first appearance, numbered like footnotes. */
function collect(markdown: string): Map<string, Cite> {
  const cites = new Map<string, Cite>();
  for (const m of markdown.matchAll(/\]\(cite:([a-z]+)\/([^)\s]+)\)/g)) {
    const kind = m[1];
    const id = decodeURIComponent(m[2]);
    const key = `${kind}/${id}`;
    if (!cites.has(key)) cites.set(key, { kind, id, n: cites.size + 1 });
  }
  return cites;
}

// Renders an answer with numbered citations and a Sources list. A source is marked verified when the
// cited id came back from a tool (or attachment) in this conversation; "?" flags a made-up or
// mistyped source.
export function Answer({ text, verified, streaming = false }: { text: string; verified: Set<string>; streaming?: boolean }) {
  const markdown = linkCitations(text);
  const cites = collect(markdown);
  const isVerified = (c: Cite) => verified.has(citationKey(c.kind, c.id));

  return (
    <div>
      <div className={`answer text-[15px] ${streaming ? "streaming" : ""}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]} // tables, strikethrough, task lists: models use them
          urlTransform={(url) => url}
          components={{
            a({ href = "", children }) {
              if (!href.startsWith("cite:")) {
                return (
                  <a href={href} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2">
                    {children}
                  </a>
                );
              }
              const [kind, encoded] = href.slice("cite:".length).split("/");
              const cite = cites.get(`${kind}/${decodeURIComponent(encoded ?? "")}`);
              if (!cite) return null;
              const ok = isVerified(cite);
              return (
                <a
                  href={`#src-${cite.n}`}
                  title={`${describe(cite.kind, cite.id)}${ok ? "" : " (not found in any tool result)"}`}
                  className={`cite ${ok ? "" : "cite-bad"}`}
                >
                  {cite.n}
                </a>
              );
            },
          }}
        >
          {markdown}
        </ReactMarkdown>
      </div>

      {cites.size > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">Sources</p>
          <ol className="space-y-1">
            {[...cites.values()].map((c) => {
              const ok = isVerified(c);
              const url = citationUrl(c.kind, c.id);
              const label = describe(c.kind, c.id);
              return (
                <li key={c.n} id={`src-${c.n}`} className="flex items-center gap-2 text-[12px] text-muted">
                  <span className="w-4 shrink-0 text-right font-mono text-[11px]">{c.n}</span>
                  {ok ? (
                    <Check size={12} className="shrink-0 text-ok" aria-label="verified" />
                  ) : (
                    <CircleHelp size={12} className="shrink-0 text-bad" aria-label="not verified" />
                  )}
                  {url ? (
                    <a href={url} target="_blank" rel="noreferrer" className="truncate hover:text-ink hover:underline">
                      {label}
                    </a>
                  ) : (
                    <span className="truncate">{label}</span>
                  )}
                  {!ok && <span className="shrink-0 text-bad">not in tool results</span>}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
