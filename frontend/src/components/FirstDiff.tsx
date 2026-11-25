"use client";

import { useState } from "react";
import { DecisionCard } from "@/components/tables";
import { ErrorBox, Loading } from "@/components/ui";
import { api, type DecisionRow, type FirstDifference, type RunDetail } from "@/lib/api";
import { int, money } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

function storyFor(fd: FirstDifference, aName: string, bName: string): string {
  const aAct = fd.a.action ?? "nothing";
  const bAct = fd.b.action ?? "nothing";
  if (aAct !== bAct) {
    return `On ${fd.session}, ${fd.symbol} ${aAct === "enter" ? "was bought in" : aAct === "none" || aAct === "no_signal" || aAct === "ineligible" ? "did not trade in" : `did “${aAct}” in`} ${aName}, and ${bAct === "enter" ? "was bought in" : bAct === "none" || bAct === "no_signal" || bAct === "ineligible" ? "did not trade in" : `did “${bAct}” in`} ${bName}.`;
  }
  return `On ${fd.session}, ${fd.symbol} is the first place the two ledgers disagree.`;
}

function Inspector({ a, b, fd }: { a: RunDetail; b: RunDetail; fd: FirstDifference }) {
  const q = { session: fd.session, symbol: fd.symbol, action: undefined, limit: 50 };
  const da = useAsync(() => api.decisions(a.id, q), [a.id, fd.session, fd.symbol]);
  const db = useAsync(() => api.decisions(b.id, q), [b.id, fd.session, fd.symbol]);
  const ea = useAsync(() => api.equity(a.id), [a.id]);
  const eb = useAsync(() => api.equity(b.id), [b.id]);
  const dayA = (ea.data ?? []).find((e) => e.session === fd.session);
  const dayB = (eb.data ?? []).find((e) => e.session === fd.session);

  const col = (run: RunDetail, label: string, rows: DecisionRow[] | undefined, err: unknown) => (
    <div>
      <h4>
        {label}: {run.name}
      </h4>
      <ErrorBox error={err} />
      {rows ? (
        rows.length ? (
          rows.map((r, i) => <DecisionCard key={r.decision_id ?? i} row={r} title={`${r.session} ${r.symbol}`} />)
        ) : (
          <p className="muted">No decision recorded for {fd.symbol} on this session.</p>
        )
      ) : (
        <Loading />
      )}
    </div>
  );

  return (
    <div className="inspector" data-testid="inspector">
      <h3>
        Everything behind {fd.symbol} on {fd.session}
      </h3>
      <div className="grid-2">
        {col(a, "A", da.data?.rows as DecisionRow[] | undefined, da.error)}
        {col(b, "B", db.data?.rows as DecisionRow[] | undefined, db.error)}
      </div>
      <h4>Account at the close of {fd.session}</h4>
      <div className="table-wrap">
        <table className="compact">
          <thead>
            <tr>
              <th>Run</th>
              <th className="num">Cash</th>
              <th className="num">Positions</th>
              <th className="num">Positions value</th>
              <th className="num">Equity</th>
            </tr>
          </thead>
          <tbody>
            {[
              ["A", dayA],
              ["B", dayB],
            ].map(([k, d]) => {
              const row = d as typeof dayA;
              return (
                <tr key={k as string}>
                  <td>{k as string}</td>
                  <td className="num">{money(row?.cash)}</td>
                  <td className="num">{int(row?.positions)}</td>
                  <td className="num">{money(row?.positions_value)}</td>
                  <td className="num">{money(row?.equity)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Pin readout under the well. Home's pin already links here. */
export function FirstDifferenceReadout({
  fd,
  a,
  b,
}: {
  fd: FirstDifference | null;
  a: RunDetail;
  b: RunDetail;
}) {
  const fdKey = fd ? `${fd.session}|${fd.symbol}` : "";
  const [inspecting, setInspecting] = useState("");

  if (!fd) {
    return (
      <p className="muted readout" data-testid="first-difference" data-session="" data-symbol="">
        Every decision is the same in both runs.
      </p>
    );
  }

  return (
    <div className="first-diff readout" data-testid="first-difference" data-session={fd.session} data-symbol={fd.symbol}>
      <div className="diff-head">
        <div>
          <div className="big">
            {fd.session} · {fd.symbol}
          </div>
          <p className="story" style={{ marginTop: 8 }}>
            {storyFor(fd, a.name, b.name)}
          </p>
        </div>
        <button
          className="primary"
          data-testid="inspect-first-difference"
          onClick={() => setInspecting(inspecting === fdKey ? "" : fdKey)}
        >
          {inspecting === fdKey ? "Hide" : "Inspect"}
        </button>
      </div>
      <div className="grid-2">
        <DecisionCard row={fd.a} title={`A: ${a.name}`} />
        <DecisionCard row={fd.b} title={`B: ${b.name}`} />
      </div>
      {inspecting === fdKey ? <Inspector a={a} b={b} fd={fd} /> : null}
    </div>
  );
}
