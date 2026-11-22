import { ClipboardList } from "lucide-react";

export type YearStat = { year: number; complaints: number; recalls: number };
export type ShortlistEntry = {
  make: string;
  model: string;
  years: string;
  why: string;
  checked: boolean;
  year_stats: YearStat[];
  avoid: number[];
  data_note: string | null;
};
export type Profile = {
  budget?: string | null;
  use?: string | null;
  commute?: string | null;
  passengers?: string | null;
  cargo?: string | null;
  climate?: string | null;
  fuel?: string | null;
  experience?: string | null;
  timeline?: string | null;
  must_haves?: string[];
  dealbreakers?: string[];
  shortlist?: ShortlistEntry[];
};

const FIELDS: [keyof Profile, string][] = [
  ["budget", "Budget"],
  ["use", "Use"],
  ["commute", "Driving"],
  ["passengers", "Passengers"],
  ["cargo", "Cargo"],
  ["climate", "Climate"],
  ["fuel", "Fuel"],
  ["experience", "Experience"],
  ["timeline", "Timeline"],
];

// Complaints per model year, with the years the data says to avoid in red.
function YearBars({ entry }: { entry: ShortlistEntry }) {
  const max = Math.max(1, ...entry.year_stats.map((y) => y.complaints));
  return (
    <div className="mt-2 flex items-end gap-1" role="img" aria-label={`NHTSA complaints by year for ${entry.make} ${entry.model}`}>
      {entry.year_stats.map((y) => {
        const bad = entry.avoid.includes(y.year);
        return (
          <div key={y.year} className="flex flex-1 flex-col items-center gap-1" title={`${y.year}: ${y.complaints.toLocaleString()} complaints, ${y.recalls} recalls`}>
            <div className="flex h-10 w-full items-end">
              <div className={`w-full rounded-sm ${bad ? "bg-bad" : "bg-accent/50"}`} style={{ height: `${Math.max(6, (y.complaints / max) * 100)}%` }} />
            </div>
            <span className={`font-mono text-[9px] ${bad ? "text-bad" : "text-muted"}`}>{String(y.year).slice(2)}</span>
          </div>
        );
      })}
    </div>
  );
}

export function BriefCard({ profile, live = false }: { profile: Profile; live?: boolean }) {
  const known = FIELDS.filter(([k]) => profile[k]);
  const tags = [...(profile.must_haves ?? []).map((t) => ["+", t]), ...(profile.dealbreakers ?? []).map((t) => ["−", t])];
  const shortlist = profile.shortlist ?? [];
  const empty = !known.length && !tags.length && !shortlist.length;

  return (
    <div className="rounded-2xl border border-line bg-panel p-4">
      <div className="mb-3 flex items-center gap-2 text-[13px] font-medium">
        <ClipboardList size={15} className="text-accent" /> Your car brief
        {live && <span className="ml-auto text-[11px] font-normal text-muted">fills in as you talk</span>}
      </div>
      {empty && <p className="text-[13px] text-muted">Nothing yet. Tell Sam about your budget and how you&apos;ll use the car.</p>}
      {known.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-[13px]">
          {known.map(([k, label]) => (
            <div key={k} className="contents animate-[fadein_0.4s_ease]">
              <dt className="text-muted">{label}</dt>
              <dd>{profile[k] as string}</dd>
            </div>
          ))}
        </dl>
      )}
      {tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {tags.map(([sign, t]) => (
            <span key={sign + t} className={`rounded-md px-1.5 py-0.5 text-[11px] ${sign === "+" ? "bg-accent/10 text-accent" : "bg-bad/10 text-bad"}`}>
              {sign} {t}
            </span>
          ))}
        </div>
      )}
      {shortlist.length > 0 && (
        <div className="mt-4 space-y-3 border-t border-line pt-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted">Shortlist</p>
          {shortlist.map((e) => (
            <div key={e.make + e.model} className="rounded-xl border border-line p-3">
              <div className="flex items-baseline gap-2">
                <span className="text-[14px] font-medium">
                  {e.make} {e.model}
                </span>
                <span className="font-mono text-[12px] text-muted">{e.years}</span>
              </div>
              {e.why && <p className="mt-0.5 text-[12px] text-muted">{e.why}</p>}
              {e.checked ? (
                <>
                  {e.year_stats.length > 0 && <YearBars entry={e} />}
                  <p className={`mt-1.5 text-[11px] ${e.avoid.length ? "text-bad" : "text-muted"}`}>
                    {e.data_note ?? (e.year_stats.length ? "No complaint spikes in these years (NHTSA)." : "")}
                  </p>
                </>
              ) : (
                <p className="shimmer mt-1.5 text-[11px]">Checking NHTSA complaint data…</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
