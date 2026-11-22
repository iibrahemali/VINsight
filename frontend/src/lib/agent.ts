import { apiFetch } from "@/lib/keys";

// Client for the backend: the /api/chat SSE stream, attachments, models, stop.
// Plus citation checking against tool results and attachments.

export type TraceEvent = {
  type: "notice" | "thinking" | "tool_call" | "tool_result" | "text" | "error" | "done" | "profile";
  turn: number;
  name?: string;
  input?: Record<string, unknown>;
  output?: unknown;
  text?: string;
  ok: boolean;
  ms?: number;
  usage?: Record<string, number>;
  model?: string;
  persona?: string;
  at?: number; // client receive time (ms), for "thought for 12s"
};

export type PersonaId = "inspector" | "scout" | "coach" | "advisor";

export const PERSONAS: { id: PersonaId; name: string; tagline: string; examples: string[]; voice?: boolean }[] = [
  {
    id: "inspector",
    name: "Inspector",
    tagline: "Check a specific car from its VIN, listing link or PDF.",
    examples: [
      "Is this car worth a look? VIN 1HGCM82633A004352",
      "Check a 2016 Honda Civic for recalls and common problems.",
    ],
  },
  {
    id: "scout",
    name: "Scout",
    tagline: "Figure out which car and which years to look for.",
    examples: [
      "I have about $12k, commute 30 miles a day, and want something reliable.",
      "Which years of the Honda Civic should I avoid?",
    ],
  },
  {
    id: "coach",
    name: "Coach",
    tagline: "New to this? Learn how buying a used car works.",
    examples: [
      "I've never bought a car. Where do I even start?",
      "What does a car really cost per month beyond the price?",
    ],
  },
  {
    id: "advisor",
    name: "Advisor",
    tagline: "Talk it through on a voice call. Get a shortlist that fits you.",
    examples: [],
    voice: true,
  },
];

export type ModelOption = { id: string; label: string; detail: string };

export type AttachmentCard = {
  id: string;
  kind: "link" | "pdf" | "text";
  name: string;
  url?: string | null;
  title?: string | null;
  site?: string | null;
  description?: string | null;
  image?: string | null;
  pages?: number | null;
  status: "ok" | "blocked" | "empty";
  note?: string | null;
  chars: number;
  vins: string[];
  excerpt: string;
};

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchModels(): Promise<{ default: string | null; models: ModelOption[] }> {
  return json(await apiFetch("/api/models"));
}

export async function addLink(url: string): Promise<AttachmentCard> {
  return json(
    await apiFetch("/api/links", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url }) }),
  );
}

export async function uploadFile(file: File): Promise<AttachmentCard> {
  const body = new FormData();
  body.append("file", file);
  return json(await apiFetch("/api/files", { method: "POST", body }));
}

export async function stopSession(sessionId: string): Promise<void> {
  await apiFetch("/api/stop", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  }).catch(() => {});
}

export async function streamChat(opts: {
  message: string;
  persona: PersonaId;
  provider: string | null;
  attachments: string[];
  sessionId: string | null;
  signal: AbortSignal;
  onSession: (id: string) => void;
  onEvent: (e: TraceEvent) => void;
}): Promise<void> {
  const res = await apiFetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      message: opts.message,
      persona: opts.persona,
      provider: opts.provider,
      attachments: opts.attachments,
      session_id: opts.sessionId,
    }),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) throw new Error(`backend returned ${res.status}`);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const event = /^event: (.*)$/m.exec(frame)?.[1];
      const data = /^data: (.*)$/m.exec(frame)?.[1];
      if (data === undefined) continue;
      if (event === "session") opts.onSession(data);
      else opts.onEvent({ ...(JSON.parse(data) as TraceEvent), at: Date.now() });
    }
  }
}

// Collect every source id the tools actually returned (plus attached documents), so the UI can
// flag any citation the model made up. This is the same check the citation-validity eval runs.
export function sourceIds(events: TraceEvent[], attachmentIds: string[] = []): Set<string> {
  const ids = new Set<string>(attachmentIds.map((id) => `doc:${id}`));
  const walk = (v: unknown) => {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      if (o.source && typeof o.source === "object") {
        const s = o.source as { type: string; id: string };
        ids.add(`${s.type}:${s.id}`.toLowerCase());
      }
      // Year comparisons list recall campaigns without a source object each.
      if (Array.isArray(o.recall_campaigns)) {
        o.recall_campaigns.forEach((c) => ids.add(`nhtsa_recall:${c}`.toLowerCase()));
      }
      Object.values(o).forEach(walk);
    }
  };
  events.filter((e) => e.type === "tool_result" && e.ok).forEach((e) => walk(e.output));
  return ids;
}

const CITE_TYPES: Record<string, string> = {
  vpic: "nhtsa_vpic",
  recall: "nhtsa_recall",
  complaint: "nhtsa_complaint",
  complaints: "nhtsa_complaint",
  years: "nhtsa_complaint",
  doc: "doc",
};

export function citationKey(kind: string, id: string): string {
  // [complaints:honda:accord:2003] maps to the summary source id "complaints:honda:accord:2003".
  const sourceId = kind === "complaints" || kind === "years" ? `${kind}:${id}` : id;
  return `${CITE_TYPES[kind] ?? kind}:${sourceId}`.toLowerCase();
}

export function citationUrl(kind: string, id: string): string | undefined {
  if (kind === "recall") return `https://www.nhtsa.gov/recalls?nhtsaId=${id}`;
  if (kind === "vpic") return `https://vpic.nhtsa.dot.gov/decoder/Decoder?VIN=${id}`;
  if (kind === "complaints" || kind === "years") {
    const [make, model, last] = id.split(":");
    const year = last?.split("-").at(-1);
    return `https://www.nhtsa.gov/vehicle/${year}/${make}/${encodeURIComponent(model ?? "")}`;
  }
  return undefined;
}

// Turn "[recall:19V182000]" into a markdown link with a cite: scheme the renderer styles as a chip.
export function linkCitations(markdown: string): string {
  // Model names can contain spaces ("Grand Cherokee"), so the id is URI-encoded inside the link.
  return markdown.replace(/\[(vpic|recalls?|complaints?|years|doc):([^\]\n]+)\]/g, (_, kind: string, id: string) => {
    const k = kind === "recalls" ? "recall" : kind;
    return `[${k}:${id}](cite:${k}/${encodeURIComponent(id.trim())})`;
  });
}

const VIN_RE = /\b[A-HJ-NPR-Z0-9]{17}\b/;
export const findVin = (text: string) => VIN_RE.exec(text.toUpperCase())?.[0] ?? null;

const URL_RE = /^(https?:\/\/)?([a-z0-9-]+\.)+[a-z]{2,}(\/\S*)?$/i;
export const isUrl = (text: string) => URL_RE.test(text.trim()) && !/\s/.test(text.trim());
