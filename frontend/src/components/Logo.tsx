// VINsight mark: a magnifying lens over VIN barcode bars.
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="vinsight-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2dd4bf" />
          <stop offset="1" stopColor="#0f766e" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#vinsight-g)" />
      <circle cx="28" cy="28" r="14" fill="none" stroke="#fff" strokeWidth="5" />
      <path d="M38.5 38.5 49 49" stroke="#fff" strokeWidth="6" strokeLinecap="round" />
      <path d="M22 23v10M26 21v14M30 24v8M34 22v12" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2">
      <LogoMark size={size} />
      <span className="text-[17px] font-semibold tracking-tight">
        VIN<span className="text-accent">sight</span>
      </span>
    </span>
  );
}
