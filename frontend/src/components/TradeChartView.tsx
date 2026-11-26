"use client";

import type { TradeChart } from "@/lib/api";

const W = 760;
const CANDLE_H = 280;
const PANE_H = 96;
const PAD_X = 12;
const TOP = 16;

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

export function TradeChartView({ chart }: { chart: TradeChart }) {
  const n = chart.sessions.length;
  if (n < 1) return null;
  const overlays = chart.series.filter((s) => s.price_scale);
  const panes = chart.series.filter((s) => !s.price_scale);
  const plotW = W - PAD_X * 2;
  const slot = plotW / n;
  const x = (i: number) => PAD_X + slot * i + slot / 2;
  const priceValues: number[] = [chart.fill.price];
  chart.candles.forEach((c) => {
    if (!c) return;
    priceValues.push(c.high, c.low);
  });
  overlays.forEach((s) => s.values.forEach((v) => typeof v === "number" && priceValues.push(v)));
  const [lo, hi] = extent(priceValues);
  const yPrice = (v: number) => TOP + (1 - (v - lo) / (hi - lo)) * (CANDLE_H - TOP - 28);
  const fillAt = chart.sessions.indexOf(chart.fill.session);
  const signalAt = chart.sessions.indexOf(chart.signal.session);
  const signalClose = signalAt >= 0 ? chart.candles[signalAt]?.close : undefined;
  const height = CANDLE_H + panes.length * PANE_H;
  const ticks = [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i);

  return (
    <figure className="chart trade-chart">
      <div className="legend">
        <span className="legend-item">
          <i style={{ background: "var(--ink)" }} /> {chart.symbol}
        </span>
        {overlays.map((s, i) => (
          <span key={s.key} className="legend-item">
            <i style={{ background: i % 2 === 0 ? "var(--series-1)" : "var(--series-2)" }} /> {s.key}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${chart.symbol} at ${chart.fill.session}`}>
        {chart.candles.map((c, i) => {
          if (!c) return null;
          const up = c.close >= c.open;
          const color = up ? "var(--good)" : "var(--bad)";
          const bodyTop = yPrice(Math.max(c.open, c.close));
          const bodyH = Math.max(1, Math.abs(yPrice(c.open) - yPrice(c.close)));
          return (
            <g key={chart.sessions[i]}>
              <line x1={x(i)} x2={x(i)} y1={yPrice(c.high)} y2={yPrice(c.low)} stroke={color} strokeWidth="1" />
              <rect x={x(i) - slot * 0.28} y={bodyTop} width={Math.max(1, slot * 0.56)} height={bodyH} fill={color} />
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
            <line x1={x(fillAt)} x2={x(fillAt)} y1={TOP} y2={height - 8} stroke="var(--ink)" strokeDasharray="3 3" />
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
        {panes.map((s, p) => {
          const top = CANDLE_H + p * PANE_H;
          const nums = s.values.filter((v): v is number => typeof v === "number");
          const [pLo, pHi] = extent(nums.length ? nums : [0, 1]);
          const y = (v: number) => top + 16 + (1 - (v - pLo) / (pHi - pLo)) * (PANE_H - 36);
          return (
            <g key={s.key}>
              <line x1={PAD_X} x2={W - PAD_X} y1={top} y2={top} stroke="var(--line)" />
              <text x={PAD_X} y={top + 14} className="tick">
                {s.key}
              </text>
              <path d={linePath(s.values, x, y)} fill="none" stroke="var(--series-1)" strokeWidth="1.6" />
              {fillAt >= 0 ? (
                <line
                  x1={x(fillAt)}
                  x2={x(fillAt)}
                  y1={top}
                  y2={top + PANE_H - 8}
                  stroke="var(--ink)"
                  strokeDasharray="3 3"
                />
              ) : null}
            </g>
          );
        })}
        {ticks.map((i) => (
          <text
            key={i}
            x={x(i)}
            y={height - 6}
            textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
            className="tick"
          >
            {shortDate(chart.sessions[i])}
          </text>
        ))}
      </svg>
    </figure>
  );
}
