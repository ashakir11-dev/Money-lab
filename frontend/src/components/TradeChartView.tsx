"use client";

import { useState, type MouseEvent } from "react";
import type { TradeChart } from "@/lib/api";
import { money, num } from "@/lib/format";

const W = 760;
const CANDLE_H = 268;
const PANE_H = 104;
const M = { left: 72, right: 16, top: 14, bottom: 28 };
const PANE_HEAD = 10;

const TIMEFRAME: Record<string, string> = {
  "1min": "1 minute",
  "5min": "5 minutes",
  "15min": "15 minutes",
  "30min": "30 minutes",
  "1h": "1 hour",
  "1d": "1 day",
};

function timeframeLabel(tf: string): string {
  return TIMEFRAME[tf] ?? tf;
}

function sessionText(d: string): string {
  const date = d.slice(0, 10);
  return d.length > 10 ? `${date} ${d.slice(11, 16)}` : date;
}

function shortDate(d: string): string {
  const [, m, day] = d.slice(0, 10).split("-");
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1];
  const clock = d.length > 10 ? d.slice(11, 16) : "";
  return clock ? `${mon} ${Number(day)} ${clock}` : `${mon} ${Number(day)}`;
}

function extent(values: number[]): [number, number] {
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.08;
  return [lo - pad, hi + pad];
}

function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

function axisPrice(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1000) return `$${Math.round(v).toLocaleString("en-US")}`;
  const digits = abs >= 100 ? 0 : 2;
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function linePath(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
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

function Label({
  x,
  y,
  children,
  anchor = "start",
}: {
  x: number;
  y: number;
  children: string;
  anchor?: "start" | "middle" | "end";
}) {
  return (
    <text x={x} y={y} textAnchor={anchor} fill="var(--ink-3)" fontFamily="var(--sans)" fontSize={12}>
      {children}
    </text>
  );
}

export function TradeChartView({ chart }: { chart: TradeChart }) {
  const n = chart.sessions.length;
  const [hover, setHover] = useState<number | null>(null);
  if (n < 1) return null;

  const overlays = chart.series.filter((s) => s.price_scale);
  const panes = chart.series.filter((s) => !s.price_scale);
  const plotW = W - M.left - M.right;
  const slot = plotW / n;
  const x = (i: number) => M.left + slot * i + slot / 2;
  const priceTop = M.top;
  const priceBottom = CANDLE_H - 10;
  const priceValues: number[] = [chart.fill.price];
  chart.candles.forEach((c) => {
    if (!c) return;
    priceValues.push(c.high, c.low);
  });
  overlays.forEach((s) => s.values.forEach((v) => typeof v === "number" && priceValues.push(v)));
  const [lo, hi] = extent(priceValues);
  const yPrice = (v: number) => priceTop + (1 - (v - lo) / (hi - lo)) * (priceBottom - priceTop);
  const fillAt = chart.sessions.indexOf(chart.fill.session);
  const signalAt = chart.sessions.indexOf(chart.signal.session);
  const signalClose = signalAt >= 0 ? chart.candles[signalAt]?.close : undefined;
  const height = CANDLE_H + panes.length * PANE_H + M.bottom;
  const plotBottom = height - M.bottom;
  const ticks = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i);
  const priceTicks = niceTicks(lo, hi).filter((t) => t >= lo && t <= hi);
  const shown = hover ?? (fillAt >= 0 ? fillAt : n - 1);
  const bar = chart.candles[shown];

  function onMove(e: MouseEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round((px - M.left) / slot - 0.5);
    setHover(Math.min(n - 1, Math.max(0, i)));
  }

  return (
    <figure className="chart trade-chart">
      <div className="legend">
        <span className="legend-item">
          <i style={{ background: "var(--ink)" }} /> {chart.symbol}
          <span className="muted">{timeframeLabel(chart.timeframe)}</span>
        </span>
        {overlays.map((s, i) => (
          <span key={s.key} className="legend-item">
            <i style={{ background: i % 2 === 0 ? "var(--series-1)" : "var(--series-2)" }} /> {s.key}
          </span>
        ))}
        {panes.map((s) => (
          <span key={s.key} className="legend-item">
            <i style={{ background: "var(--series-1)" }} /> {s.key}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`${chart.symbol} ${timeframeLabel(chart.timeframe)} at ${sessionText(chart.fill.session)}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {priceTicks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={W - M.right} y1={yPrice(t)} y2={yPrice(t)} className="grid" />
            <Label x={M.left - 8} y={yPrice(t) + 4} anchor="end">
              {axisPrice(t)}
            </Label>
          </g>
        ))}
        {chart.candles.map((c, i) => {
          if (!c) return null;
          const up = c.close >= c.open;
          const color = up ? "var(--good)" : "var(--bad)";
          const bodyTop = yPrice(Math.max(c.open, c.close));
          const bodyH = Math.max(1, Math.abs(yPrice(c.open) - yPrice(c.close)));
          return (
            <g key={chart.sessions[i]}>
              <line x1={x(i)} x2={x(i)} y1={yPrice(c.high)} y2={yPrice(c.low)} stroke={color} strokeWidth="1" />
              <rect x={x(i) - Math.max(1.2, slot * 0.28)} y={bodyTop} width={Math.max(2.2, slot * 0.56)} height={bodyH} fill={color} />
            </g>
          );
        })}
        {overlays.map((s, k) => (
          <path
            key={s.key}
            d={linePath(s.values, x, yPrice)}
            fill="none"
            stroke={k % 2 === 0 ? "var(--series-1)" : "var(--series-2)"}
            strokeWidth="1.6"
          />
        ))}
        {fillAt >= 0 ? (
          <g>
            <line x1={x(fillAt)} x2={x(fillAt)} y1={priceTop} y2={plotBottom} stroke="var(--ink)" strokeDasharray="3 3" />
            <circle
              cx={x(fillAt)}
              cy={yPrice(chart.fill.price)}
              r="4.5"
              fill={chart.fill.side === "buy" ? "var(--good)" : "var(--bad)"}
              stroke="var(--well)"
              strokeWidth="1.5"
            />
          </g>
        ) : null}
        {signalAt >= 0 && typeof signalClose === "number" ? (
          <polygon
            points={`${x(signalAt)},${yPrice(signalClose) - 6} ${x(signalAt) + 6},${yPrice(signalClose)} ${x(signalAt)},${yPrice(signalClose) + 6} ${x(signalAt) - 6},${yPrice(signalClose)}`}
            fill="var(--accent)"
          />
        ) : null}
        {hover !== null ? <line x1={x(hover)} x2={x(hover)} y1={priceTop} y2={plotBottom} className="crosshair" /> : null}
        {panes.map((s, p) => {
          const top = CANDLE_H + p * PANE_H;
          const nums = s.values.filter((v): v is number => typeof v === "number");
          const [pLo, pHi] = extent(nums.length ? nums : [0, 1]);
          const plotTop = top + PANE_HEAD;
          const plotEnd = top + PANE_H - 8;
          const y = (v: number) => plotTop + (1 - (v - pLo) / (pHi - pLo)) * (plotEnd - plotTop);
          const scale = niceTicks(pLo, pHi, 2).filter((t) => t >= pLo && t <= pHi);
          return (
            <g key={s.key}>
              <line x1={M.left} x2={W - M.right} y1={top} y2={top} stroke="var(--line)" />
              {scale.map((t) => (
                <Label key={t} x={M.left - 8} y={y(t) + 4} anchor="end">
                  {num(t, Math.abs(pHi - pLo) >= 10 ? 0 : 1)}
                </Label>
              ))}
              <path d={linePath(s.values, x, y)} fill="none" stroke="var(--series-1)" strokeWidth="1.6" />
            </g>
          );
        })}
        {ticks.map((i) => (
          <Label
            key={i}
            x={i === 0 ? M.left : i === n - 1 ? W - M.right : x(i)}
            y={height - 8}
            anchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
          >
            {shortDate(chart.sessions[i])}
          </Label>
        ))}
      </svg>
      <figcaption className="chart-readout">
        <strong>{sessionText(chart.sessions[shown])}</strong>
        <span>{timeframeLabel(chart.timeframe)}</span>
        {bar ? (
          <>
            <span>Open {money(bar.open)}</span>
            <span>High {money(bar.high)}</span>
            <span>Low {money(bar.low)}</span>
            <span>Close {money(bar.close)}</span>
          </>
        ) : (
          <span>No bar</span>
        )}
        {chart.series.map((s) => (
          <span key={s.key}>
            {s.key} {typeof s.values[shown] === "number" ? num(s.values[shown], 2) : "–"}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
