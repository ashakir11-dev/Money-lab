"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export type StatItem = {
  label: string;
  value: ReactNode;
  meaning: string;
  tone?: "pos" | "neg";
};

function StatHelp({ label, meaning }: { label: string; meaning: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const noteId = useId();

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="stat-help" ref={root}>
      <button
        type="button"
        className="stat-help-btn"
        aria-expanded={open}
        aria-controls={noteId}
        aria-label={`What ${label} means`}
        onClick={() => setOpen((value) => !value)}
      >
        ?
      </button>
      {open ? (
        <span className="stat-help-pop" id={noteId} role="note">
          {meaning}
        </span>
      ) : null}
    </span>
  );
}

/** One result per statistic. The meaning stays behind the question mark. */
export function StatBreakdown({ stats }: { stats: StatItem[] }) {
  return (
    <dl className="stat-grid">
      {stats.map((stat) => (
        <div className="stat" key={stat.label}>
          <div className="stat-top">
            <dt>
              {stat.label}
              <StatHelp label={stat.label} meaning={stat.meaning} />
            </dt>
            <dd className={stat.tone ? `num ${stat.tone}` : "num"}>{stat.value}</dd>
          </div>
        </div>
      ))}
    </dl>
  );
}
