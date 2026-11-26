"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useRef, useState } from "react";
import { EquityChart } from "@/components/EquityChart";
import { Specimen } from "@/components/Specimen";
import { SettingsPanel } from "@/components/SettingsPanel";
import { PairedStats } from "@/components/StatBreakdown";
import { TradeChartDialog } from "@/components/TradeChartDialog";
import { DecisionsTable, FillsTable } from "@/components/tables";
import { RunTracker } from "@/components/RunTracker";
import { Badge, ErrorBox, Loading } from "@/components/ui";
import {
  drawdownSeries,
  fillBreakdown,
  ownFills,
  pnlSummary,
  reasonCounts,
  chartRequestForFill,
  chartRequestForTrip,
  roundTrips,
  timeInMarket,
  type RoundTrip,
} from "@/lib/analysis";
import {
  api,
  type DecisionRow,
  type EquityRow,
  type FillRow,
  type RunDetail,
  type RunMetrics,
  type RunSummary,
  type Watchlist,
} from "@/lib/api";
import { int, money, pct, versusSpy, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { pairedMetrics, type SideMetrics } from "@/lib/reportMetrics";

const PAGE = 50;
const ACTIONS = [
  { value: "", label: "Trading decisions (buy, sell, skip)" },
  { value: "enter", label: "Buy orders" },
  { value: "exit", label: "Sell orders" },
  { value: "skip", label: "Skips" },
  { value: "no_signal", label: "Near misses" },
  { value: "ineligible", label: "Not eligible" },
  { value: "all", label: "Everything" },
];

type ResolvedRun = {
  benchmark?: string;
  timeframe?: string;
  portfolio?: { starting_cash?: number };
  exit?: { kind?: string };
  watchlist?: Watchlist;
};

function defaultPair(runs: RunSummary[]): [string, string] | null {
  if (runs.length < 2) return null;
  const newest = runs[0];
  if (newest.parent_id && runs.some((r) => r.id === newest.parent_id)) return [newest.parent_id, newest.id];
  return [runs[1].id, newest.id];
}

function startCash(resolved: Record<string, unknown> | undefined): number {
  const pf = resolved?.portfolio as { starting_cash?: number } | undefined;
  return pf?.starting_cash ?? 100000;
}

function benchNameOf(metrics: RunMetrics, resolved: ResolvedRun | undefined): string {
  return String(metrics.benchmark?.symbol || resolved?.benchmark || "SPY").toUpperCase();
}

function sideOf(
  run: RunDetail,
  fills: FillRow[],
  equity: EquityRow[],
): SideMetrics {
  const resolved = run.resolved as ResolvedRun;
  const m = run.metrics as RunMetrics;
  return {
    benchName: benchNameOf(m, resolved),
    m,
    b: m.benchmark ?? {},
    pnl: pnlSummary(fills, equity, m, startCash(run.resolved as Record<string, unknown>)),
    inMkt: timeInMarket(equity),
    an: run.analysis ?? null,
  };
}

function valueOn(
  by: Map<string, EquityRow>,
  sessions: string[],
  pick: (row: EquityRow) => number | null | undefined,
): (number | null)[] {
  return sessions.map((session) => {
    const row = by.get(session);
    if (!row) return null;
    const value = pick(row);
    return typeof value === "number" ? value : null;
  });
}

/** Dashed benchmark paths. One line when both runs share it. */
function referenceSeries(
  sessions: string[],
  byA: Map<string, EquityRow>,
  byB: Map<string, EquityRow>,
  symbolA: string,
  symbolB: string,
  pick: (row: EquityRow) => number | null | undefined,
): { name: string; values: (number | null)[]; color: string; dashed: true }[] {
  const aVals = valueOn(byA, sessions, pick);
  const bVals = valueOn(byB, sessions, pick);
  const same = sessions.every((_, i) => {
    const x = aVals[i];
    const y = bVals[i];
    if (typeof x !== "number" || typeof y !== "number") return true;
    return Math.abs(x - y) < 0.05;
  });
  const nameA = symbolA === "SPY" ? "SPY" : symbolA;
  const nameB = symbolB === "SPY" ? "SPY" : symbolB;
  if (symbolA === symbolB && same) {
    return [
      {
        name: nameA,
        values: sessions.map((_, i) => (typeof aVals[i] === "number" ? aVals[i] : bVals[i])),
        color: "var(--series-bench)",
        dashed: true,
      },
    ];
  }
  return [
    { name: `${nameA} (A)`, values: aVals, color: "var(--series-bench)", dashed: true },
    { name: `${nameB} (B)`, values: bVals, color: "var(--ink-2)", dashed: true },
  ];
}

function exposureOn(by: Map<string, EquityRow>, sessions: string[]): (number | null)[] {
  return sessions.map((session) => {
    const row = by.get(session);
    if (!row?.equity) return null;
    return row.positions_value / row.equity;
  });
}

function tripKey(trip: RoundTrip): string {
  return [trip.symbol, trip.entrySession, trip.exitSession, trip.qty, trip.entryPrice.toFixed(4), trip.exitPrice.toFixed(4)].join("|");
}

function fillKey(fill: FillRow): string {
  return [fill.session, fill.symbol, fill.side, fill.qty, fill.price].join("|");
}

/** True when this row has no unused match on the other side. */
function onlyHere<T>(rows: T[], other: T[], key: (row: T) => string): boolean[] {
  const left = new Map<string, number>();
  for (const row of other) left.set(key(row), (left.get(key(row)) ?? 0) + 1);
  return rows.map((row) => {
    const id = key(row);
    const n = left.get(id) ?? 0;
    if (n > 0) {
      left.set(id, n - 1);
      return false;
    }
    return true;
  });
}

function RunPicker({
  id,
  value,
  runs,
  onChange,
  label,
}: {
  id: string;
  value: string;
  runs: RunSummary[];
  onChange: (v: string) => void;
  label: string;
}) {
  return (
    <div className="field picker">
      <label htmlFor={id}>{label}</label>
      <select id={id} data-testid={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name} (…{r.id.slice(-6)})
          </option>
        ))}
      </select>
    </div>
  );
}

function RunLead({ label, run }: { label: "A" | "B"; run: RunDetail }) {
  const m = run.metrics as RunMetrics;
  const b = m.benchmark ?? {};
  const resolved = run.resolved as ResolvedRun;
  const benchName = benchNameOf(m, resolved);
  return (
    <div>
      <h2>
        <span className={label === "A" ? "side-a" : "side-b"}>{label}</span>{" "}
        <Link href={`/report?id=${encodeURIComponent(run.id)}`}>{run.name}</Link>
      </h2>
      <p className="lead">
        {pct(m.total_return, true)} · {versusSpy(m.total_return, b.total_return, benchName)} · drawdown {pct(m.max_drawdown)}
      </p>
      <p className="caption">
        <Badge kind={run.source_kind}>{run.source_label}</Badge> · {run.snapshot_name} · {resolved.timeframe ?? "1d"} bars ·{" "}
        <code>{run.id}</code> · {run.engine_version} · {when(run.created_at)}
      </p>
      <p className="plain-summary">{run.summary}</p>
    </div>
  );
}

function BenchFacts({ label, run }: { label: string; run: RunDetail }) {
  const m = run.metrics as RunMetrics;
  const b = m.benchmark ?? {};
  const resolved = run.resolved as ResolvedRun;
  const benchName = benchNameOf(m, resolved);
  const wl = resolved.watchlist;
  return (
    <p className="caption">
      <strong>{label}.</strong> Open positions at the end: {int(m.open_positions ?? 0)}
      {b.invested_session ? (
        <>
          {" "}
          · {benchName === "SPY" ? "SPY" : benchName} bought {int(b.shares)} {b.shares === 1 ? "share" : "shares"} at the{" "}
          {b.invested_session} open for {money(b.price)} each ({money((b.shares ?? 0) * (b.price ?? 0))} invested, {money(b.cost)} in
          trading costs, {money(b.cash_left)} cash left). The dashed line is that buy-and-hold path.
          {benchName === "SPY" ? " Beating it means this run finished ahead of the S&P." : ` This run was not scored against SPY.`}
        </>
      ) : null}
      {wl?.created_at ? (
        <>
          {" "}
          · Watchlist {wl.name} v{wl.version} selected {wl.created_at.slice(0, 10)}: a list chosen today describes past performance of
          today&apos;s picks.
        </>
      ) : null}
    </p>
  );
}

function ReasonList({ rows, other, prefix }: { rows: [string, number][]; other: Map<string, number>; prefix?: string }) {
  if (!rows.length) return <p className="muted">None in this sample.</p>;
  return (
    <ul className="plain-list">
      {rows.slice(0, 6).map(([k, n]) => (
        <li key={`${prefix ?? ""}${k}`} className={other.get(k) === n ? undefined : "only-side"}>
          {prefix ? `${prefix} · ` : null}
          {k} <span className="num">{int(n)}</span>
        </li>
      ))}
    </ul>
  );
}

function TripTable({
  trips,
  only,
  onOpen,
}: {
  trips: RoundTrip[];
  only: boolean[];
  onOpen: (trip: RoundTrip, el: HTMLElement) => void;
}) {
  if (!trips.length) return <p className="muted">No closed round trips.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Entry</th>
            <th>Exit</th>
            <th className="num">Qty</th>
            <th className="num">P&L</th>
            <th className="num">Hold</th>
            <th className="num">Costs</th>
          </tr>
        </thead>
        <tbody>
          {trips.map((t, i) => (
            <tr key={`${t.symbol}-${t.entrySession}-${i}`} className={only[i] ? "only-side" : undefined}>
              <td>{t.symbol}</td>
              <td>
                {t.entryDecisionId != null || t.exitDecisionId != null ? (
                  <button
                    type="button"
                    className="linkish"
                    onClick={(e) => onOpen(t, e.currentTarget)}
                  >
                    {t.entrySession} @ {money(t.entryPrice)}
                  </button>
                ) : (
                  <>
                    {t.entrySession} @ {money(t.entryPrice)}
                  </>
                )}
              </td>
              <td>
                {t.entryDecisionId != null || t.exitDecisionId != null ? (
                  <button
                    type="button"
                    className="linkish"
                    onClick={(e) => onOpen(t, e.currentTarget)}
                  >
                    {t.exitSession} @ {money(t.exitPrice)}
                  </button>
                ) : (
                  <>
                    {t.exitSession} @ {money(t.exitPrice)}
                  </>
                )}
              </td>
              <td className="num">{int(t.qty)}</td>
              <td className={`num ${t.pnl >= 0 ? "pos" : "neg"}`}>{money(t.pnl)}</td>
              <td className="num">{t.holdSessions != null ? `${int(t.holdSessions)} sess` : "–"}</td>
              <td className="num">{money(t.costs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompareView() {
  const params = useSearchParams();
  const router = useRouter();
  const runs = useAsync(() => api.runs(), []);
  const fallback = runs.data ? defaultPair(runs.data) : null;
  const a = params.get("a") || fallback?.[0] || "";
  const b = params.get("b") || fallback?.[1] || "";
  const cmp = useAsync(() => api.compare(a, b), [a, b], !!a && !!b);
  const fillsA = useAsync(() => api.fills(a), [a], !!a);
  const fillsB = useAsync(() => api.fills(b), [b], !!b);
  const eqA = useAsync(() => api.equity(a), [a], !!a);
  const eqB = useAsync(() => api.equity(b), [b], !!b);
  const tradesA = useAsync(() => api.trades(a), [a], !!a);
  const tradesB = useAsync(() => api.trades(b), [b], !!b);
  const [detailed, setDetailed] = useState(false);
  const [action, setAction] = useState("");
  const [symbol, setSymbol] = useState("");
  const [offsetA, setOffsetA] = useState(0);
  const [offsetB, setOffsetB] = useState(0);
  const actionFilter = action === "all" ? undefined : action ? [action] : ["enter", "exit", "skip"];
  const skipsA = useAsync(() => api.decisions(a, { action: ["skip"], limit: 1000 }), [a], !!a && detailed);
  const skipsB = useAsync(() => api.decisions(b, { action: ["skip"], limit: 1000 }), [b], !!b && detailed);
  const nearA = useAsync(() => api.decisions(a, { action: ["no_signal"], limit: 500 }), [a], !!a && detailed);
  const nearB = useAsync(() => api.decisions(b, { action: ["no_signal"], limit: 500 }), [b], !!b && detailed);
  const decisionsA = useAsync(
    () => api.decisions(a, { offset: offsetA, limit: PAGE, action: actionFilter, symbol: symbol || undefined }),
    [a, action, symbol, offsetA],
    !!a && detailed,
  );
  const decisionsB = useAsync(
    () => api.decisions(b, { offset: offsetB, limit: PAGE, action: actionFilter, symbol: symbol || undefined }),
    [b, action, symbol, offsetB],
    !!b && detailed,
  );
  const opener = useRef<HTMLElement | null>(null);
  const [chartPick, setChartPick] = useState<{
    runId: string;
    decisionId: number;
    pairedDecisionId?: number;
    title: string;
  } | null>(null);
  const openChart = useCallback(
    (runId: string, decisionId: number, title: string, el: HTMLElement, pairedDecisionId?: number) => {
      opener.current = el;
      setChartPick({ runId, decisionId, pairedDecisionId, title });
    },
    [],
  );
  const closeChart = useCallback(() => {
    setChartPick(null);
    opener.current?.focus();
  }, []);

  function pick(next: { a?: string; b?: string }) {
    const na = next.a ?? a;
    const nb = next.b ?? b;
    setOffsetA(0);
    setOffsetB(0);
    router.replace(`/compare?a=${encodeURIComponent(na)}&b=${encodeURIComponent(nb)}`);
  }

  const skipReasonsA = useMemo(() => reasonCounts((skipsA.data?.rows ?? []) as DecisionRow[]), [skipsA.data]);
  const skipReasonsB = useMemo(() => reasonCounts((skipsB.data?.rows ?? []) as DecisionRow[]), [skipsB.data]);
  const nearReasonsA = useMemo(() => reasonCounts((nearA.data?.rows ?? []) as DecisionRow[]), [nearA.data]);
  const nearReasonsB = useMemo(() => reasonCounts((nearB.data?.rows ?? []) as DecisionRow[]), [nearB.data]);

  if (runs.error) return <ErrorBox error={runs.error} title="Could not load runs" />;
  if (!runs.data) return <Loading what="Loading runs" />;
  if (runs.data.length < 2 && !(params.get("a") && params.get("b")))
    return (
      <div className="bench">
        <h1>Compare</h1>
        {runs.data.length ? <RunTracker runs={runs.data} /> : null}
        <p className="muted">
          You need at least two runs. Open a report and use <em>Duplicate and edit</em>, or build one in the{" "}
          <Link href="/builder">Builder</Link>.
        </p>
      </div>
    );

  const c = cmp.data;
  const sessions = Array.from(new Set([...(eqA.data ?? []).map((e) => e.session), ...(eqB.data ?? []).map((e) => e.session)])).sort();
  const byA = new Map((eqA.data ?? []).map((e) => [e.session, e]));
  const byB = new Map((eqB.data ?? []).map((e) => [e.session, e]));
  const sideA = c ? sideOf(c.a, fillsA.data ?? [], eqA.data ?? []) : null;
  const sideB = c ? sideOf(c.b, fillsB.data ?? [], eqB.data ?? []) : null;
  const metrics = sideA && sideB ? pairedMetrics(sideA, sideB) : [];
  const bothSpy = sideA?.benchName === "SPY" && sideB?.benchName === "SPY";
  const tripsA = roundTrips(tradesA.data ?? []);
  const tripsB = roundTrips(tradesB.data ?? []);
  const onlyA = onlyHere(tripsA, tripsB, tripKey);
  const onlyB = onlyHere(tripsB, tripsA, tripKey);
  const fillsOnlyA = onlyHere(fillsA.data ?? [], fillsB.data ?? [], fillKey);
  const fillsOnlyB = onlyHere(fillsB.data ?? [], fillsA.data ?? [], fillKey);
  const brA = fillBreakdown(fillsA.data ?? []);
  const brB = fillBreakdown(fillsB.data ?? []);
  const signalA = (c?.a.resolved as ResolvedRun | undefined)?.exit?.kind === "signal";
  const signalB = (c?.b.resolved as ResolvedRun | undefined)?.exit?.kind === "signal";
  const buyCost = (fills: FillRow[]) => ownFills(fills).filter((f) => f.side === "buy").reduce((s, f) => s + f.cost, 0);

  return (
    <div className="bench">
      <RunTracker runs={runs.data} />
      <div className="picker-bar">
        <RunPicker id="compare-a" label="A" value={a} runs={runs.data} onChange={(v) => pick({ a: v })} />
        <RunPicker id="compare-b" label="B" value={b} runs={runs.data} onChange={(v) => pick({ b: v })} />
        <button onClick={() => pick({ a: b, b: a })} title="Swap A and B">
          Swap
        </button>
        <div className="mode-toggle" role="group" aria-label="Detail">
          <button className={detailed ? undefined : "on"} onClick={() => setDetailed(false)}>
            Basic
          </button>
          <button className={detailed ? "on" : undefined} onClick={() => setDetailed(true)}>
            Detailed
          </button>
        </div>
      </div>

      <ErrorBox error={cmp.error} title="Could not compare these runs" />
      {cmp.loading && !c ? <Loading what="Comparing" /> : null}

      {c && sideA && sideB ? (
        <>
          {c.data_differs ? (
            <p className="notice">
              Different price data. A: <Badge kind={c.a.source_kind}>{c.a.source_label}</Badge> {c.a.snapshot_name} B:{" "}
              <Badge kind={c.b.source_kind}>{c.b.source_label}</Badge> {c.b.snapshot_name}
            </p>
          ) : null}

          <SettingsPanel a={c.a.resolved} b={c.b.resolved} />

          <div className="compare-leads" data-testid="compare-leads">
            <RunLead label="A" run={c.a} />
            <RunLead label="B" run={c.b} />
          </div>

          {sessions.length >= 2 ? (
            <Specimen
              dates={sessions}
              series={[
                { name: c.a.name, values: valueOn(byA, sessions, (e) => e.equity), color: "var(--series-1)" },
                { name: c.b.name, values: valueOn(byB, sessions, (e) => e.equity), color: "var(--series-2)" },
                ...referenceSeries(sessions, byA, byB, sideA.benchName, sideB.benchName, (e) => e.benchmark_equity),
              ]}
              caption={[
                `A ${versusSpy(sideA.m.total_return, sideA.b.total_return, sideA.benchName)}`,
                `B ${versusSpy(sideB.m.total_return, sideB.b.total_return, sideB.benchName)}`,
                bothSpy ? "Dashed line is buy-and-hold SPY, the S&P" : "Dashed lines are each run's buy-and-hold benchmark",
              ].join(" · ")}
            />
          ) : eqA.loading || eqB.loading ? (
            <div className="specimen-well specimen-wait" />
          ) : (
            <p className="muted">Not enough equity points to draw a specimen.</p>
          )}

          <section className="stack-block">
            <h2>Metrics</h2>
            <PairedStats
              aLabel={<Link href={`/report?id=${encodeURIComponent(c.a.id)}`}>A</Link>}
              bLabel={<Link href={`/report?id=${encodeURIComponent(c.b.id)}`}>B</Link>}
              rows={metrics.map((row) => ({
                key: row.key,
                label: row.label,
                meaning: row.meaning,
                a: row.aText,
                b: row.bText,
                delta: row.delta,
                toneA: row.toneA,
                toneB: row.toneB,
                changed: row.changed,
              }))}
            />
            <BenchFacts label="A" run={c.a} />
            <BenchFacts label="B" run={c.b} />
            <p className="caption">
              SPY is buy-and-hold of the S&P 500 ETF on each run&apos;s own dates, starting cash, and trading costs. Beat SPY means that
              run finished ahead of the S&P. The B − A column is how B differs from A. A shaded metric row is one that differs.
            </p>
          </section>

          {detailed ? (
            <>
              <div className="grid-2">
                <section className="stack-block">
                  <h2>Entries and exits in A</h2>
                  <p>
                    {int(brA.buys)} buys · {int(brA.symbols.length)} symbols · notional {money(brA.notionalBuys)} · costs{" "}
                    {money(buyCost(fillsA.data ?? []))}
                  </p>
                  <p className="muted small">{brA.symbols.join(", ") || "None"}</p>
                  <p>
                    {int(brA.sells)} sells · notional {money(brA.notionalSells)} · still open {brA.held.length ? brA.held.join(", ") : "none"}
                  </p>
                </section>
                <section className="stack-block">
                  <h2>Entries and exits in B</h2>
                  <p>
                    {int(brB.buys)} buys · {int(brB.symbols.length)} symbols · notional {money(brB.notionalBuys)} · costs{" "}
                    {money(buyCost(fillsB.data ?? []))}
                  </p>
                  <p className="muted small">{brB.symbols.join(", ") || "None"}</p>
                  <p>
                    {int(brB.sells)} sells · notional {money(brB.notionalSells)} · still open {brB.held.length ? brB.held.join(", ") : "none"}
                  </p>
                </section>
              </div>

              <div className="grid-2">
                <section className="stack-block">
                  <h2>Missed entries in A</h2>
                  <p>
                    {int(nearA.data?.total ?? 0)} near-misses · {int(skipsA.data?.total ?? 0)} skips
                  </p>
                  <ReasonList rows={nearReasonsA} other={new Map(nearReasonsB)} />
                  <ReasonList rows={skipReasonsA} other={new Map(skipReasonsB)} prefix="skip" />
                  <h3 className="sub-head">Missed exits</h3>
                  {signalA ? (
                    <p className="muted">Signal exits: no separate missed-exit ledger beyond days the exit conditions did not fire while in a position.</p>
                  ) : (
                    <p className="muted">Holding-period exits are scheduled, not missed.</p>
                  )}
                </section>
                <section className="stack-block">
                  <h2>Missed entries in B</h2>
                  <p>
                    {int(nearB.data?.total ?? 0)} near-misses · {int(skipsB.data?.total ?? 0)} skips
                  </p>
                  <ReasonList rows={nearReasonsB} other={new Map(nearReasonsA)} />
                  <ReasonList rows={skipReasonsB} other={new Map(skipReasonsA)} prefix="skip" />
                  <h3 className="sub-head">Missed exits</h3>
                  {signalB ? (
                    <p className="muted">Signal exits: no separate missed-exit ledger beyond days the exit conditions did not fire while in a position.</p>
                  ) : (
                    <p className="muted">Holding-period exits are scheduled, not missed.</p>
                  )}
                </section>
              </div>

              <div className="grid-2" data-testid="round-trips">
                <section className="stack-block">
                  <h2>Round trips in A</h2>
                  <TripTable
                    trips={tripsA}
                    only={onlyA}
                    onOpen={(trip, el) => {
                      const req = chartRequestForTrip(trip);
                      if (req) openChart(c.a.id, req.decisionId, req.title, el, req.pairedDecisionId);
                    }}
                  />
                </section>
                <section className="stack-block">
                  <h2>Round trips in B</h2>
                  <TripTable
                    trips={tripsB}
                    only={onlyB}
                    onOpen={(trip, el) => {
                      const req = chartRequestForTrip(trip);
                      if (req) openChart(c.b.id, req.decisionId, req.title, el, req.pairedDecisionId);
                    }}
                  />
                </section>
              </div>
              <p className="caption">
                A shaded round trip is one the other run does not have. Click an entry or an exit to open that trade:
                the chart marks the buy and the sell, and the signals that decided each.
              </p>

              <div className="grid-2">
                <section className="stack-block">
                  <h2>Drawdown vs {bothSpy ? "the S&P (SPY)" : "benchmark"}</h2>
                  {sessions.length ? (
                    <EquityChart
                      label="Drawdown"
                      dates={sessions}
                      series={[
                        { name: c.a.name, values: drawdownSeries(valueOn(byA, sessions, (e) => e.equity)), color: "var(--series-1)" },
                        { name: c.b.name, values: drawdownSeries(valueOn(byB, sessions, (e) => e.equity)), color: "var(--series-2)" },
                        ...referenceSeries(sessions, byA, byB, sideA.benchName, sideB.benchName, (e) => e.benchmark_equity).map((line) => ({
                          ...line,
                          values: drawdownSeries(line.values),
                        })),
                      ]}
                      formatTick={(v) => pct(v)}
                      formatValue={(v) => pct(v)}
                    />
                  ) : (
                    <Loading />
                  )}
                </section>
                <section className="stack-block">
                  <h2>Exposure</h2>
                  {sessions.length ? (
                    <EquityChart
                      label="Exposure"
                      dates={sessions}
                      series={[
                        { name: c.a.name, values: exposureOn(byA, sessions), color: "var(--series-1)" },
                        { name: c.b.name, values: exposureOn(byB, sessions), color: "var(--series-2)" },
                      ]}
                      formatTick={(v) => pct(v)}
                      formatValue={(v) => pct(v)}
                    />
                  ) : (
                    <Loading />
                  )}
                  <p className="muted small">
                    Time in market {pct(sideA.inMkt)} for A and {pct(sideB.inMkt)} for B.{" "}
                    {bothSpy ? "SPY stays invested the whole time." : "Each benchmark stays invested the whole time."}
                  </p>
                </section>
              </div>

              <div className="grid-2">
                <section className="stack-block">
                  <h2>Trades in A</h2>
                  <ErrorBox error={fillsA.error} />
                  {fillsA.data ? (
                    <FillsTable
                      fills={fillsA.data}
                      rowClass={(_fill, index) => (fillsOnlyA[index] ? "only-side" : undefined)}
                      onOpen={(fill, el) => {
                        const req = chartRequestForFill(fill, tripsA);
                        if (req) openChart(c.a.id, req.decisionId, req.title, el, req.pairedDecisionId);
                      }}
                    />
                  ) : (
                    <Loading />
                  )}
                </section>
                <section className="stack-block">
                  <h2>Trades in B</h2>
                  <ErrorBox error={fillsB.error} />
                  {fillsB.data ? (
                    <FillsTable
                      fills={fillsB.data}
                      rowClass={(_fill, index) => (fillsOnlyB[index] ? "only-side" : undefined)}
                      onOpen={(fill, el) => {
                        const req = chartRequestForFill(fill, tripsB);
                        if (req) openChart(c.b.id, req.decisionId, req.title, el, req.pairedDecisionId);
                      }}
                    />
                  ) : (
                    <Loading />
                  )}
                </section>
              </div>
              <p className="caption">
                A shaded trade is one the other run does not have. Click a fill to open its chart. A fill that belongs
                to one closed trade shows that trade&apos;s buy and sell.
              </p>

              <section className="stack-block">
                <div className="section-head">
                  <h2>Decisions</h2>
                  <div className="row">
                    <select
                      aria-label="Which decisions"
                      value={action}
                      onChange={(e) => {
                        setAction(e.target.value);
                        setOffsetA(0);
                        setOffsetB(0);
                      }}
                      style={{ width: "auto" }}
                    >
                      {ACTIONS.map((item) => (
                        <option key={item.value} value={item.value}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label="Symbol"
                      placeholder="Symbol"
                      value={symbol}
                      onChange={(e) => {
                        setSymbol(e.target.value.toUpperCase());
                        setOffsetA(0);
                        setOffsetB(0);
                      }}
                      style={{ width: 110 }}
                    />
                  </div>
                </div>
                <div className="grid-2">
                  <div>
                    <h3 className="sub-head">A</h3>
                    <ErrorBox error={decisionsA.error} />
                    {decisionsA.data ? (
                      <>
                        <DecisionsTable rows={decisionsA.data.rows as DecisionRow[]} />
                        <div className="row" style={{ marginTop: 10 }}>
                          <button disabled={offsetA === 0} onClick={() => setOffsetA(Math.max(0, offsetA - PAGE))}>
                            Previous
                          </button>
                          <span className="muted small">
                            {decisionsA.data.total === 0
                              ? "0 rows"
                              : `${offsetA + 1}–${Math.min(offsetA + PAGE, decisionsA.data.total)} of ${decisionsA.data.total.toLocaleString("en-US")}`}
                          </span>
                          <button disabled={offsetA + PAGE >= decisionsA.data.total} onClick={() => setOffsetA(offsetA + PAGE)}>
                            Next
                          </button>
                        </div>
                      </>
                    ) : (
                      <Loading />
                    )}
                  </div>
                  <div>
                    <h3 className="sub-head">B</h3>
                    <ErrorBox error={decisionsB.error} />
                    {decisionsB.data ? (
                      <>
                        <DecisionsTable rows={decisionsB.data.rows as DecisionRow[]} />
                        <div className="row" style={{ marginTop: 10 }}>
                          <button disabled={offsetB === 0} onClick={() => setOffsetB(Math.max(0, offsetB - PAGE))}>
                            Previous
                          </button>
                          <span className="muted small">
                            {decisionsB.data.total === 0
                              ? "0 rows"
                              : `${offsetB + 1}–${Math.min(offsetB + PAGE, decisionsB.data.total)} of ${decisionsB.data.total.toLocaleString("en-US")}`}
                          </span>
                          <button disabled={offsetB + PAGE >= decisionsB.data.total} onClick={() => setOffsetB(offsetB + PAGE)}>
                            Next
                          </button>
                        </div>
                      </>
                    ) : (
                      <Loading />
                    )}
                  </div>
                </div>
              </section>

              <div className="grid-2">
                <section className="stack-block">
                  <details>
                    <summary>Settings as run, A</summary>
                    <pre className="json">{JSON.stringify(c.a.resolved, null, 2)}</pre>
                  </details>
                </section>
                <section className="stack-block">
                  <details>
                    <summary>Settings as run, B</summary>
                    <pre className="json">{JSON.stringify(c.b.resolved, null, 2)}</pre>
                  </details>
                </section>
              </div>
              {chartPick ? (
                <TradeChartDialog
                  runId={chartPick.runId}
                  decisionId={chartPick.decisionId}
                  pairedDecisionId={chartPick.pairedDecisionId}
                  title={chartPick.title}
                  onClose={closeChart}
                />
              ) : null}
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<Loading />}>
      <CompareView />
    </Suspense>
  );
}
