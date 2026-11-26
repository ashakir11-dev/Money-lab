"use client";

import type { ConditionResult, DecisionRow, FillRow } from "@/lib/api";
import { ACTION_LABEL, int, money, num, plain } from "@/lib/format";

export function FillsTable({
  fills,
  empty = "No trades.",
  onOpen,
}: {
  fills: FillRow[];
  empty?: string;
  onOpen?: (fill: FillRow, opener: HTMLElement) => void;
}) {
  if (!fills.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Session</th>
            <th>Symbol</th>
            <th>Side</th>
            <th className="num">Shares</th>
            <th className="num">Price</th>
            <th className="num">Gross</th>
            <th className="num">Cost</th>
            <th className="num">Cash after</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f, i) => {
            const open = onOpen && typeof f.decision_id === "number" ? onOpen : null;
            return (
              <tr
                key={`${f.session}-${f.symbol}-${f.side}-${i}`}
                className={open ? "click-row" : undefined}
                role={open ? "button" : undefined}
                tabIndex={open ? 0 : undefined}
                aria-label={open ? `${f.side} ${f.symbol} ${f.session}` : undefined}
                onClick={open ? (e) => open(f, e.currentTarget) : undefined}
                onKeyDown={
                  open
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          open(f, e.currentTarget);
                        }
                      }
                    : undefined
                }
              >
                <td>{f.session}</td>
                <td>{f.symbol}</td>
                <td>
                  <span className={`side side-${f.side}`}>{f.side}</span>
                  {f.note ? <span className="muted small"> · {f.note}</span> : null}
                </td>
                <td className="num">{int(f.qty)}</td>
                <td className="num">{money(f.price)}</td>
                <td className="num">{money(f.gross)}</td>
                <td className="num">{money(f.cost)}</td>
                <td className="num">{money(f.cash_after)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ConditionsTable({ conditions }: { conditions?: ConditionResult[] | null }) {
  if (!conditions || !conditions.length) return null;
  return (
    <table className="compact">
      <thead>
        <tr>
          <th>Condition</th>
          <th className="num">Observed</th>
          <th className="num">Threshold</th>
          <th>Passed</th>
        </tr>
      </thead>
      <tbody>
        {conditions.map((c, i) => (
          <tr key={i}>
            <td>{plain(c.condition)}</td>
            <td className="num">{typeof c.observed === "number" ? num(c.observed, 2) : plain(c.observed)}</td>
            <td className="num">{typeof c.threshold === "number" ? num(c.threshold, 2) : plain(c.threshold)}</td>
            <td>
              {c.passed === true ? (
                <span className="pass">passed</span>
              ) : c.passed === false ? (
                <span className="fail">failed</span>
              ) : (
                <span className="muted">not yet defined</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ValuesList({ values }: { values?: Record<string, number | null> | null }) {
  if (!values || !Object.keys(values).length) return null;
  return (
    <dl className="values">
      {Object.entries(values).map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{typeof v === "number" ? num(v, 2) : plain(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

const DETAIL_KEYS: [keyof DecisionRow, string, (v: unknown) => string][] = [
  ["qty", "Shares", int],
  ["est_cost", "Estimated cost", money],
  ["available", "Cash available", money],
  ["order_qty", "Ordered shares", int],
  ["open", "Open", money],
];

/** One decision with everything behind it: reason, conditions, values, sizing, fill. */
export function DecisionCard({ row, title }: { row: DecisionRow; title: string }) {
  const action = row.action ?? "none";
  return (
    <div className={`decision decision-${action}`}>
      <div className="decision-title">
        <span className="muted small">{title}</span>
        <div>
          <span className={`action action-${action}`}>{ACTION_LABEL[action] ?? action}</span>{" "}
          <span>{row.reason}</span>
        </div>
      </div>
      <ConditionsTable conditions={row.conditions} />
      <ValuesList values={row.values} />
      <dl className="values">
        {DETAIL_KEYS.filter(([k]) => row[k] !== undefined && row[k] !== null).map(([k, label, fmt]) => (
          <div key={String(k)}>
            <dt>{label}</dt>
            <dd>{fmt(row[k])}</dd>
          </div>
        ))}
      </dl>
      {row.fill ? (
        <p className="fill-line">
          Filled {row.fill.session}: {row.fill.side} {int(row.fill.qty)} × {money(row.fill.price)}, cost {money(row.fill.cost)}, cash after{" "}
          {money(row.fill.cash_after)}
        </p>
      ) : "fill" in row && (action === "enter" || action === "exit") ? (
        <p className="muted small">No fill recorded (the order was waiting for a later open).</p>
      ) : null}
    </div>
  );
}

export function DecisionsTable({ rows }: { rows: DecisionRow[] }) {
  if (!rows.length) return <p className="muted">No decisions match.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Session</th>
            <th>Symbol</th>
            <th>Decision</th>
            <th>Reason</th>
            <th className="num">Shares</th>
            <th>Indicator values</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.decision_id ?? i}`}>
              <td>{r.session}</td>
              <td>{r.symbol}</td>
              <td>
                <span className={`action action-${r.action}`}>{ACTION_LABEL[r.action ?? ""] ?? r.action}</span>
              </td>
              <td>{r.reason}</td>
              <td className="num">{int(r.qty)}</td>
              <td className="small">
                {r.values
                  ? Object.entries(r.values)
                      .map(([k, v]) => `${k} ${typeof v === "number" ? num(v, 2) : plain(v)}`)
                      .join(" · ")
                  : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
