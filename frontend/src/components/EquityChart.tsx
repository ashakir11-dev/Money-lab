"use client";

import { useMemo, useState } from "react";
import { money } from "@/lib/format";

export type Series = {
  name: string;
  /** One value per date; null where undefined. */
  values: (number | null | undefined)[];
  /** CSS colour, usually a var(--series-n) token. */
  color: string;
  dashed?: boolean;
};

const W = 760;
const H = 280;
const M = { left: 72, right: 16, top: 16, bottom: 32 };

function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}

function shortDate(d: string): string {
  const [y, m] = d.split("-");
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1];
  return `${mon} ${y}`;
}

type FormatFn = (v: unknown) => string;

/** Account value over time: one line per series on shared session dates. */
export function EquityChart({
  dates,
  series,
  label = "Account value",
  formatTick,
  formatValue,
}: {
  dates: string[];
  series: Series[];
  label?: string;
  formatTick?: FormatFn;
  formatValue?: FormatFn;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = series.filter((s) => s.values.some((v) => typeof v === "number"));

  const geo = useMemo(() => {
    const all = shown.flatMap((s) => s.values.filter((v): v is number => typeof v === "number"));
    if (!all.length || dates.length < 2) return null;
    let lo = Math.min(...all);
    let hi = Math.max(...all);
    const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.01 || 1;
    lo -= pad;
    hi += pad;
    const x = (i: number) => M.left + (i / (dates.length - 1)) * (W - M.left - M.right);
    const y = (v: number) => M.top + (1 - (v - lo) / (hi - lo)) * (H - M.top - M.bottom);
    const paths = shown.map((s) => {
      let d = "";
      let pen = false;
      s.values.forEach((v, i) => {
        if (typeof v !== "number") {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    });
    const yTicks = niceTicks(lo, hi);
    const step = Math.max(1, Math.round((dates.length - 1) / 5));
    const last = dates.length - 1;
    const xTicks = dates.map((_, i) => i).filter((i) => {
      if (i === 0 || i === last) return true;
      if (i % step !== 0) return false;
      return x(last) - x(i) > 64 && x(i) - x(0) > 64;
    });
    return { x, y, paths, yTicks, xTicks };
  }, [dates, shown]);

  if (!geo) return <p className="muted">Not enough data to draw a chart.</p>;

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - M.left) / (W - M.left - M.right)) * (dates.length - 1));
    setHover(Math.min(dates.length - 1, Math.max(0, i)));
  }

  return (
    <figure className="chart">
      <div className="legend">
        {shown.map((s) => (
          <span key={s.name} className="legend-item">
            <svg width="22" height="10" aria-hidden="true">
              <line x1="1" x2="21" y1="5" y2="5" stroke={s.color} strokeWidth="2" strokeDasharray={s.dashed ? "5 4" : undefined} />
            </svg>
            {s.name}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${label}: ${shown.map((s) => s.name).join(", ")}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {geo.yTicks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={geo.y(t)} y2={geo.y(t)} className="grid" />
            <text x={M.left - 8} y={geo.y(t) + 4} textAnchor="end" className="tick">
              {formatTick
                ? formatTick(t)
                : t >= 1000
                  ? `$${Math.round(t / 1000).toLocaleString("en-US")}k`
                  : `$${t.toFixed(0)}`}
            </text>
          </g>
        ))}
        {geo.xTicks.map((i) => (
          <text
            key={i}
            x={geo.x(i)}
            y={H - 10}
            textAnchor={i === 0 ? "start" : i === dates.length - 1 ? "end" : "middle"}
            className="tick"
          >
            {shortDate(dates[i])}
          </text>
        ))}
        {geo.paths.map((d, k) => (
          <path
            key={shown[k].name}
            d={d}
            fill="none"
            stroke={shown[k].color}
            strokeWidth={2}
            strokeDasharray={shown[k].dashed ? "5 4" : undefined}
            strokeLinejoin="round"
          />
        ))}
        {hover !== null ? <line x1={geo.x(hover)} x2={geo.x(hover)} y1={M.top} y2={H - M.bottom} className="crosshair" /> : null}
      </svg>
      {hover !== null ? (
        <figcaption className="chart-readout">
          <strong>{dates[hover]}</strong>
          {shown.map((s) => (
            <span key={s.name}>
              <i style={{ background: s.color }} /> {s.name}: {(formatValue ?? money)(s.values[hover])}
            </span>
          ))}
        </figcaption>
      ) : (
        <figcaption className="chart-readout muted">Hover the chart to read values by date.</figcaption>
      )}
    </figure>
  );
}
