"use client";

import { int } from "@/lib/format";

export function BreakdownBars({
  rows,
  empty = "Nothing to show.",
}: {
  rows: { label: string; count: number; tone?: "good" | "bad" | "warn" | "accent" }[];
  empty?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  if (!rows.length) return <p className="muted">{empty}</p>;
  const color = (tone?: string) =>
    tone === "good"
      ? "var(--good)"
      : tone === "bad"
        ? "var(--bad)"
        : tone === "warn"
          ? "var(--warn)"
          : "var(--accent)";
  return (
    <div className="bars">
      {rows.map((r) => (
        <div key={r.label} className="bar-row">
          <span>{r.label}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(r.count / max) * 100}%`, background: color(r.tone) }} />
          </div>
          <span className="num">{int(r.count)}</span>
        </div>
      ))}
    </div>
  );
}

/** Dots of buy/sell along the session axis. */
export function TradeTimeline({
  dates,
  buys,
  sells,
}: {
  dates: string[];
  buys: Set<string>;
  sells: Set<string>;
}) {
  if (dates.length < 2) return <p className="muted">Not enough sessions to draw a timeline.</p>;
  const W = 760;
  const H = 72;
  const pad = 12;
  const x = (i: number) => pad + (i / (dates.length - 1)) * (W - pad * 2);
  return (
    <figure className="chart">
      <div className="legend">
        <span className="legend-item">
          <i style={{ background: "var(--good)", width: 8, height: 8, borderRadius: 99 }} /> Buys
        </span>
        <span className="legend-item">
          <i style={{ background: "var(--bad)", width: 8, height: 8, borderRadius: 99 }} /> Sells
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Trade timeline">
        <line x1={pad} x2={W - pad} y1={H / 2} y2={H / 2} className="grid" />
        {dates.map((d, i) => {
          const buy = buys.has(d);
          const sell = sells.has(d);
          if (!buy && !sell) return null;
          return (
            <g key={d}>
              {buy ? <circle cx={x(i)} cy={H / 2 - 10} r={4} fill="var(--good)" /> : null}
              {sell ? <circle cx={x(i)} cy={H / 2 + 10} r={4} fill="var(--bad)" /> : null}
            </g>
          );
        })}
        <text x={pad} y={H - 8} className="tick">
          {dates[0]}
        </text>
        <text x={W - pad} y={H - 8} textAnchor="end" className="tick">
          {dates[dates.length - 1]}
        </text>
      </svg>
    </figure>
  );
}
