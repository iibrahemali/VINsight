// VINsight mark: a car seen head-on, with teal headlights. Strokes follow the theme colour.
export function LogoMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="shrink-0 text-ink">
      <path
        d="M4 18v-5.5L6.5 7h11l2.5 5.5V18H4ZM5.5 18v2M18.5 18v2M7.6 11.5 9 8.6h6l1.4 2.9Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
        strokeLinecap="round"
        fill="none"
      />
      <circle cx="7.5" cy="14.8" r="1.3" fill="var(--accent)" />
      <circle cx="16.5" cy="14.8" r="1.3" fill="var(--accent)" />
    </svg>
  );
}

export function Logo({ size = 24 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2">
      <LogoMark size={size} />
      <span className="text-[16px] font-semibold tracking-tight">VINsight</span>
    </span>
  );
}
