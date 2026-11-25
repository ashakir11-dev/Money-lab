/** Fork mark: one stem that splits into A (teal) and B (copper). The pin is the first difference. */

type MarkProps = {
  size?: number;
  draw?: boolean;
  className?: string;
  title?: string;
};

export function Mark({ size = 28, draw = false, className, title = "Money-lab" }: MarkProps) {
  return (
    <svg
      className={`mark${draw ? " mark-draw" : ""}${className ? ` ${className}` : ""}`}
      viewBox="0 0 40 40"
      width={size}
      height={size}
      role="img"
      aria-label={title}
    >
      <path className="mark-stem" d="M20 36 V20" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <path className="mark-arm-a" d="M20 20 L8 8" fill="none" stroke="var(--series-1)" strokeWidth="2.4" strokeLinecap="round" />
      <path className="mark-arm-b" d="M20 20 L32 8" fill="none" stroke="var(--series-2)" strokeWidth="2.4" strokeLinecap="round" />
      <circle className="mark-pin" cx="20" cy="20" r="2.4" fill="var(--ink)" />
    </svg>
  );
}

export function RailIcon({ name }: { name: "data" | "builder" | "reports" | "compare" }) {
  if (name === "data") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <rect x="4" y="14" width="4" height="6" fill="currentColor" opacity="0.45" />
        <rect x="10" y="9" width="4" height="11" fill="currentColor" opacity="0.7" />
        <rect x="16" y="5" width="4" height="15" fill="currentColor" />
      </svg>
    );
  }
  if (name === "builder") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path d="M12 20 V12 L6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        <path d="M12 12 L18 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        <circle cx="12" cy="12" r="1.6" fill="currentColor" />
      </svg>
    );
  }
  if (name === "reports") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
        <path d="M4 16 L9 11 L13 14 L20 7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M4 19 H20" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.4" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d="M4 16 C8 16 8 8 12 8" fill="none" stroke="var(--series-1)" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M12 8 C16 8 16 16 20 16" fill="none" stroke="var(--series-2)" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="8" r="1.6" fill="var(--ink)" />
    </svg>
  );
}
