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

export type PairedStat = {
  key: string;
  label: string;
  meaning: string;
  a: ReactNode;
  b: ReactNode;
  delta: ReactNode;
  toneA?: "pos" | "neg";
  toneB?: "pos" | "neg";
  changed?: boolean;
};

function toneClass(tone?: "pos" | "neg"): string {
  return tone ? `num ${tone}` : "num";
}

/** A and B for each report statistic, plus how B differs from A. */
export function PairedStats({
  rows,
  aLabel,
  bLabel,
}: {
  rows: PairedStat[];
  aLabel: ReactNode;
  bLabel: ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table className="pair" data-testid="compare-metrics">
        <thead>
          <tr>
            <th></th>
            <th className="num side-a">{aLabel}</th>
            <th className="num side-b">{bLabel}</th>
            <th className="num">B − A</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className={row.changed ? "changed" : undefined}>
              <td>
                <span className="pair-label">
                  {row.label}
                  <StatHelp label={row.label} meaning={row.meaning} />
                </span>
              </td>
              <td className={toneClass(row.toneA)}>{row.a}</td>
              <td className={toneClass(row.toneB)}>{row.b}</td>
              <td className="num">{row.delta}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
