import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { citationKey, citationUrl, linkCitations } from "@/lib/agent";

// Renders the agent's answer. Citations become chips: green when the cited id came back from a
// tool in this conversation, red when it didn't (a fabricated or mistyped source).
export function Answer({ text, verified, streaming = false }: { text: string; verified: Set<string>; streaming?: boolean }) {
  return (
    <div className={`answer text-[15px] ${streaming ? "streaming" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]} // tables, strikethrough, task lists: models use them
        urlTransform={(url) => url}
        components={{
          a({ href = "", children }) {
            if (!href.startsWith("cite:")) {
              return (
                <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
                  {children}
                </a>
              );
            }
            const [kind, encoded] = href.slice("cite:".length).split("/");
            const id = decodeURIComponent(encoded ?? "");
            const ok = verified.has(citationKey(kind, id));
            const label = kind === "doc" ? "attached" : kind === "link" ? "web page" : `${kind}:${id}`;
            const url = citationUrl(kind, id);
            const chip = (
              <span
                title={ok ? (kind === "doc" ? "From a document you attached" : kind === "link" ? "From a web page read in this conversation" : "Returned by a tool in this conversation") : "Not found in any tool result"}
                className={`mx-0.5 inline-flex items-center gap-1 rounded-md border px-1.5 py-px align-baseline font-mono text-[11px] ${
                  ok ? "border-ok/40 text-ok" : "border-bad/50 text-bad"
                }`}
              >
                {ok ? "✓" : "?"} {label}
              </span>
            );
            return url ? (
              <a href={url} target="_blank" rel="noreferrer">
                {chip}
              </a>
            ) : (
              chip
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
    </div>
  );
}
