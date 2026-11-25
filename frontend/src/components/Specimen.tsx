"use client";

import type { ReactNode } from "react";

const W = 920;
const H = 340;
const M = { left: 8, right: 8, top: 28, bottom: 36 };

export type SpecimenSeries = {
  name: string;
  values: (number | null | undefined)[];
  color: string;
  dashed?: boolean;
};

function pathOf(
  values: (number | null | undefined)[],
  x: (i: number) => number,
  y: (v: number) => number,
): string {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (typeof v !== "number") {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

function shortDate(d: string): string {
  const [, m, day] = d.split("-");
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1];
  return `${mon} ${Number(day)}`;
}

export type SpecimenMark = { session: string; side: "buy" | "sell" };

/** Overlay of two equity paths with a pin on the fork. Motion is identity, not decoration. */
export function Specimen({
  dates,
  series,
  pinDate,
  pinHref,
  wellHref,
  caption,
  marks,
  children,
}: {
  dates: string[];
  series: SpecimenSeries[];
  pinDate?: string | null;
  pinHref?: string;
  wellHref?: string;
  caption?: string;
  marks?: SpecimenMark[];
  children?: ReactNode;
}) {
  const shown = series.filter((s) => s.values.some((v) => typeof v === "number"));
  const all = shown.flatMap((s) => s.values.filter((v): v is number => typeof v === "number"));
  if (!all.length || dates.length < 2) {
    return <p className="muted">Not enough data to draw a specimen.</p>;
  }

  let lo = Math.min(...all);
  let hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 1;
  lo -= pad;
  hi += pad;
  const x = (i: number) => M.left + (i / (dates.length - 1)) * (W - M.left - M.right);
  const y = (v: number) => M.top + (1 - (v - lo) / (hi - lo)) * (H - M.top - M.bottom);
  const pinIndex = pinDate ? dates.indexOf(pinDate) : -1;
  const step = Math.max(1, Math.round((dates.length - 1) / 4));
  const xTicks = dates.map((_, i) => i).filter((i) => i % step === 0 || i === dates.length - 1);

  const chart = (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={caption ?? "Equity specimen"}>
      {xTicks.map((i) => (
        <text key={i} x={x(i)} y={H - 10} textAnchor={i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"} className="tick">
          {shortDate(dates[i])}
        </text>
      ))}
      {shown.map((s, k) => (
        <path
          key={s.name}
          className={s.dashed ? "specimen-dashed" : `specimen-path specimen-path-${k}`}
          d={pathOf(s.values, x, y)}
          fill="none"
          stroke={s.color}
          strokeWidth={k === 0 ? 2.4 : 2.2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}
      {(marks ?? []).map((m, i) => {
        const idx = dates.indexOf(m.session);
        const v = shown[0]?.values[idx];
        if (idx < 0 || typeof v !== "number") return null;
        return (
          <circle
            key={`${m.session}-${m.side}-${i}`}
            cx={x(idx)}
            cy={y(v)}
            r="3.2"
            fill={m.side === "buy" ? "var(--good)" : "var(--bad)"}
            opacity="0.9"
          />
        );
      })}
      {pinIndex >= 0 ? (
        <g className="specimen-pin">
          <line x1={x(pinIndex)} x2={x(pinIndex)} y1={M.top} y2={H - M.bottom} className="pin-rule" />
          {shown.map((s) =>
            typeof s.values[pinIndex] === "number" ? (
              <circle
                key={s.name}
                cx={x(pinIndex)}
                cy={y(s.values[pinIndex] as number)}
                r="4.5"
                fill={s.color}
                stroke="var(--well)"
                strokeWidth="2"
              />
            ) : null,
          )}
        </g>
      ) : null}
    </svg>
  );

  return (
    <figure className="specimen">
      <div className="specimen-legend">
        {shown.map((s) => (
          <span key={s.name} className="legend-item">
            <i style={{ background: s.color, opacity: s.dashed ? 0.7 : 1 }} />
            {s.name}
          </span>
        ))}
      </div>
      <div className="specimen-well">
        {wellHref ? (
          <a href={wellHref} className="specimen-hit" aria-label={caption ?? "Open report"}>
            {chart}
          </a>
        ) : (
          chart
        )}
        {pinIndex >= 0 && pinHref ? (
          <a
            href={pinHref}
            className="specimen-pin-hit"
            style={{ left: `${(x(pinIndex) / W) * 100}%` }}
            aria-label={`First difference ${pinDate}`}
            title={pinDate ? `First difference · ${pinDate}` : "First difference"}
          />
        ) : null}
      </div>
      {caption ? <figcaption className="specimen-caption">{caption}</figcaption> : null}
      {children}
    </figure>
  );
}

export function Spark({ values, color }: { values: number[]; color?: string }) {
  if (values.length < 2) return <span className="spark empty" />;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const w = 120;
  const h = 36;
  const d = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = 2 + (1 - (v - lo) / span) * (h - 4);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={d} fill="none" stroke={color ?? "var(--series-1)"} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

/** Demo fork geometry (AAA 2023-04-04 / RSI 31.68) so the bench has identity before any import. */
export const DEMO_FORK = {
  pin: "2023-04-04",
  aName: "RSI < 30",
  bName: "RSI < 35",
  dates: [
    "2023-01-06",
    "2023-01-13",
    "2023-01-20",
    "2023-01-27",
    "2023-02-03",
    "2023-02-10",
    "2023-02-17",
    "2023-02-24",
    "2023-03-03",
    "2023-03-10",
    "2023-03-17",
    "2023-03-24",
    "2023-03-31",
    "2023-04-04",
    "2023-04-14",
    "2023-04-21",
    "2023-04-28",
    "2023-05-05",
    "2023-05-12",
    "2023-05-19",
    "2023-05-26",
    "2023-06-02",
    "2023-06-09",
    "2023-06-16",
    "2023-06-23",
    "2023-06-30",
  ],
  a: [
    100000, 100420, 100180, 100890, 101200, 100950, 101480, 101820, 102100, 101750, 102400, 102680, 102900, 103050,
    103200, 103480, 103100, 103620, 103900, 103550, 104100, 104380, 104200, 104650, 104900, 105120,
  ],
  b: [
    100000, 100420, 100180, 100890, 101200, 100950, 101480, 101820, 102100, 101750, 102400, 102680, 102900, 103050,
    103480, 103920, 103610, 104280, 104720, 104500, 105180, 105640, 105510, 106120, 106580, 107050,
  ],
};
