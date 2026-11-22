// The visitor's own API keys. Kept in this browser only and sent as headers with each request;
// the backend uses them for that request and never stores them.

export type KeyName = "groq" | "gemini";
const STORAGE = "vinsight.keys";
const HEADER: Record<KeyName, string> = { groq: "X-Groq-Key", gemini: "X-Gemini-Key" };

export function loadKeys(): Partial<Record<KeyName, string>> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE) ?? "{}");
  } catch {
    return {}; // private mode or blocked storage: fall back to the server's keys
  }
}

export function saveKeys(keys: Partial<Record<KeyName, string>>): void {
  const clean = Object.fromEntries(Object.entries(keys).filter(([, v]) => v && v.trim()).map(([k, v]) => [k, v!.trim()]));
  try {
    if (Object.keys(clean).length) localStorage.setItem(STORAGE, JSON.stringify(clean));
    else localStorage.removeItem(STORAGE);
  } catch {}
}

export function keyHeaders(): Record<string, string> {
  const keys = loadKeys();
  return Object.fromEntries((Object.keys(HEADER) as KeyName[]).filter((k) => keys[k]).map((k) => [HEADER[k], keys[k]!]));
}

/** fetch() with the visitor's keys attached. Use for every backend call. */
export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, headers: { ...(init.headers as Record<string, string>), ...keyHeaders() } });
}
