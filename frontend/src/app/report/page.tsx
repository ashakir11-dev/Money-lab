"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useRef, useState } from "react";
import { EquityChart } from "@/components/EquityChart";
import { Specimen } from "@/components/Specimen";
import { StatBreakdown } from "@/components/StatBreakdown";
import { TradeChartDialog } from "@/components/TradeChartDialog";
import { DecisionsTable, FillsTable } from "@/components/tables";
import { Badge, ErrorBox, JobStatus, Loading } from "@/components/ui";
import {
  drawdownSeries,
  exposureSeries,
  fillBreakdown,
  marksFromFills,
  pnlSummary,
  reasonCounts,
  roundTrips,
  timeInMarket,
} from "@/lib/analysis";
import { api, waitForJob, type DecisionRow, type Job, type RunMetrics, type RunSummary, type Watchlist } from "@/lib/api";
import { int, money, pct, versusSpy, when } from "@/lib/format";
import { rootId, versionNumber } from "@/lib/lineage";
import { metricStats } from "@/lib/reportMetrics";
import { useAsync } from "@/lib/hooks";

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

function startCashOf(resolved: Record<string, unknown> | undefined): number {
  const pf = resolved?.portfolio as { starting_cash?: number } | undefined;
  return pf?.starting_cash ?? 100000;
}

function RunPicker({ runs, id }: { runs: RunSummary[]; id: string }) {
  const router = useRouter();
  return (
    <div className="field picker">
      <label htmlFor="report-run">Saved run</label>
      <select id="report-run" value={id} onChange={(e) => router.push(`/report?id=${encodeURIComponent(e.target.value)}`)}>
        {!id ? <option value="">Choose a run…</option> : null}
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name} (…{r.id.slice(-6)})
          </option>
        ))}
      </select>
    </div>
  );
}

function FamilyCaption({ runs, id }: { runs: RunSummary[]; id: string }) {
  const root = rootId(runs, id);
  if (root === id) return null;
  const name = runs.find((run) => run.id === root)?.name ?? root;
  return (
    <>
      {" "}
      · v{versionNumber(runs, id)} of <Link href={`/report?id=${encodeURIComponent(root)}`}>{name}</Link>
    </>
  );
}

function ReportView() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const list = useAsync(() => api.runs(), []);
  const run = useAsync(() => api.run(id), [id], !!id);
  const equity = useAsync(() => api.equity(id), [id], !!id);
  const fills = useAsync(() => api.fills(id), [id], !!id);
  const skips = useAsync(() => api.decisions(id, { action: ["skip"], limit: 1000 }), [id], !!id);
  const near = useAsync(() => api.decisions(id, { action: ["no_signal"], limit: 500 }), [id], !!id);
  const parent = useAsync(() => api.run(run.data?.parent_id ?? ""), [run.data?.parent_id], !!run.data?.parent_id);
  const parentEq = useAsync(
    () => api.equity(run.data?.parent_id ?? ""),
    [run.data?.parent_id],
    !!run.data?.parent_id,
  );
  const [action, setAction] = useState("");
  const [symbol, setSymbol] = useState("");
  const [offset, setOffset] = useState(0);
  const [detailed, setDetailed] = useState(false);
  const actionFilter = action === "all" ? undefined : action ? [action] : ["enter", "exit", "skip"];
  const decisions = useAsync(
    () => api.decisions(id, { offset, limit: PAGE, action: actionFilter, symbol: symbol || undefined }),
    [id, action, symbol, offset],
    !!id && detailed,
  );
  const [job, setJob] = useState<Job>();
  const [rerunError, setRerunError] = useState<unknown>();
  const opener = useRef<HTMLElement | null>(null);
  const [chartPick, setChartPick] = useState<{ decisionId: number; title: string } | null>(null);
  const openChart = useCallback((decisionId: number, title: string, el: HTMLElement) => {
    opener.current = el;
    setChartPick({ decisionId, title });
  }, []);
  const closeChart = useCallback(() => {
    setChartPick(null);
    opener.current?.focus();
  }, []);

  const skipRows = (skips.data?.rows ?? []) as DecisionRow[];
  const nearRows = (near.data?.rows ?? []) as DecisionRow[];
  const skipReasons = useMemo(() => reasonCounts(skipRows), [skipRows]);
  const nearReasons = useMemo(() => reasonCounts(nearRows), [nearRows]);

  if (!id) {
    if (list.error) return <ErrorBox error={list.error} title="Could not load runs" />;
    if (!list.data) return <Loading what="Loading reports" />;
    return (
      <div className="bench">
        <h1>Reports</h1>
        {list.data.length ? (
          <RunPicker runs={list.data} id="" />
        ) : (
          <p className="muted">
            No runs yet. Build one in the <Link href="/builder">Builder</Link>.
          </p>
        )}
        <figure className="specimen">
          <div className="specimen-well specimen-wait" />
          <figcaption className="specimen-caption">Choose a run.</figcaption>
        </figure>
      </div>
    );
  }
  if (run.error) return <ErrorBox error={run.error} title="Could not load this run" />;
  if (!run.data) return <Loading what="Loading report" />;

  const r = run.data;
  const m = r.metrics as RunMetrics;
  const b = m.benchmark ?? {};
  const resolved = r.resolved as {
    benchmark?: string;
    portfolio?: { starting_cash?: number };
    exit?: { kind?: string };
    watchlist?: Watchlist;
  };
  const benchName = (b.symbol || resolved.benchmark || "SPY").toUpperCase();
  const wl = resolved.watchlist;
  const eq = equity.data ?? [];
  const fillRows = fills.data ?? [];
  const br = fillBreakdown(fillRows);
  const pnl = pnlSummary(fillRows, eq, m, startCashOf(r.resolved as Record<string, unknown>));
  const trips = roundTrips(fillRows);
  const inMkt = timeInMarket(eq);
  const dates = eq.map((e) => e.session);
  const dd = drawdownSeries(eq.map((e) => e.equity));
  const benchDd = drawdownSeries(eq.map((e) => e.benchmark_equity));
  const expos = exposureSeries(eq);
  const signalExit = resolved.exit?.kind === "signal";

  const market = {
    name: benchName,
    values: eq.map((e) => e.benchmark_equity),
    color: "var(--series-bench)",
    dashed: true as const,
  };
  const series = r.parent_id
    ? [
        { name: parent.data?.name ?? "Parent", values: dates.map((d) => parentEq.data?.find((e) => e.session === d)?.equity ?? null), color: "var(--series-1)" },
        { name: r.name, values: eq.map((e) => e.equity), color: "var(--series-2)" },
        market,
      ]
    : [
        { name: r.name, values: eq.map((e) => e.equity), color: "var(--series-1)" },
        market,
      ];

  async function rerun() {
    setRerunError(undefined);
    try {
      const j = await api.duplicate(id);
      setJob(j);
      const done = await waitForJob(j.id, setJob);
      if (done.status === "complete" && done.run_ids[0]) router.push(`/report?id=${encodeURIComponent(done.run_ids[0])}`);
    } catch (e) {
      setRerunError(e);
    }
  }

  return (
    <div className="bench">
      <header className="page-header">
        <div>
          <h1>{r.name}</h1>
          <p className="lead">
            {pct(m.total_return, true)} · {versusSpy(m.total_return, b.total_return, benchName)} · drawdown {pct(m.max_drawdown)}
          </p>
        </div>
        <div className="page-actions">
          <Link href={`/builder?from=${encodeURIComponent(id)}`} className="button primary">
            Duplicate and edit
          </Link>
          {r.parent_id ? (
            <Link href={`/compare?a=${encodeURIComponent(r.parent_id)}&b=${encodeURIComponent(id)}`} className="button">
              Compare with parent
            </Link>
          ) : (
            <Link href="/compare" className="button">
              Compare
            </Link>
          )}
          <button onClick={rerun} disabled={job?.status === "queued" || job?.status === "running"}>
            Re-run exactly
          </button>
          <JobStatus status={job?.status} error={job?.error} />
          <div className="mode-toggle" role="group" aria-label="Detail">
            <button className={detailed ? undefined : "on"} onClick={() => setDetailed(false)}>
              Basic
            </button>
            <button className={detailed ? "on" : undefined} onClick={() => setDetailed(true)}>
              Detailed
            </button>
          </div>
        </div>
      </header>
      <ErrorBox error={rerunError} title="Could not re-run" />

      {list.data ? <RunPicker runs={list.data} id={id} /> : null}
      <p className="caption">
        <Badge kind={r.source_kind}>{r.source_label}</Badge> · {r.snapshot_name} ·{" "}
        <span data-testid="run-timeframe">{String((r.resolved as { timeframe?: string }).timeframe ?? "1d")} bars</span> · <code>{r.id}</code> · {r.engine_version} · {when(r.created_at)}
        {r.parent_id && list.data ? <FamilyCaption runs={list.data} id={id} /> : null}
      </p>
      <p className="plain-summary" data-testid="run-summary">
        {r.summary}
      </p>

      {eq.length >= 2 ? (
        <Specimen
          dates={dates}
          series={series}
          marks={marksFromFills(fillRows)}
          caption={`${money(pnl.total)} P&L · ${versusSpy(m.total_return, b.total_return, benchName)} · ${pct(inMkt)} of sessions in market`}
        />
      ) : equity.loading ? (
        <div className="specimen-well specimen-wait" />
      ) : (
        <p className="muted">Not enough sessions to draw a specimen.</p>
      )}

      <section className="stack-block">
        <h2>Metrics</h2>
        <StatBreakdown stats={metricStats({ benchName, m, b, pnl, inMkt })} />
        <p className="caption" data-testid="run-facts">
          Open positions at the end: {int(m.open_positions ?? 0)}
          {b.invested_session ? (
            <>
              {" "}
              · {benchName === "SPY" ? "SPY" : benchName} bought {int(b.shares)}{" "}
              {b.shares === 1 ? "share" : "shares"} at the {b.invested_session} open for {money(b.price)} each (
              {money((b.shares ?? 0) * (b.price ?? 0))} invested, {money(b.cost)} in trading costs, {money(b.cash_left)} cash left).
              The dashed line is that buy-and-hold path.
              {benchName === "SPY" ? " Beating it means this run finished ahead of the S&P." : ""}
            </>
          ) : null}
          {wl?.created_at ? (
            <>
              {" "}
              · Watchlist {wl.name} v{wl.version} selected {wl.created_at.slice(0, 10)}: a list chosen today describes past
              performance of today&apos;s picks.
            </>
          ) : null}
        </p>
      </section>

      {detailed ? (
        <>
          <div className="grid-2">
            <section className="stack-block">
              <h2>Entries</h2>
              <p>
                {int(br.buys)} buys · {int(br.symbols.length)} symbols · notional {money(br.notionalBuys)} · costs{" "}
                {money(fillRows.filter((f) => f.side === "buy").reduce((s, f) => s + f.cost, 0))}
              </p>
              <p className="muted small">{br.symbols.join(", ") || "None"}</p>
            </section>
            <section className="stack-block">
              <h2>Exits</h2>
              <p>
                {int(br.sells)} sells · notional {money(br.notionalSells)} · still open {br.held.length ? br.held.join(", ") : "none"}
              </p>
            </section>
          </div>

          <div className="grid-2">
            <section className="stack-block">
              <h2>Missed entries</h2>
              <p>
                {int(near.data?.total ?? 0)} near-misses · {int(skips.data?.total ?? 0)} skips
              </p>
              <ul className="plain-list">
                {nearReasons.slice(0, 6).map(([k, n]) => (
                  <li key={k}>
                    {k} <span className="num">{int(n)}</span>
                  </li>
                ))}
                {skipReasons.slice(0, 6).map(([k, n]) => (
                  <li key={`s-${k}`}>
                    skip · {k} <span className="num">{int(n)}</span>
                  </li>
                ))}
              </ul>
            </section>
            <section className="stack-block">
              <h2>Missed exits</h2>
              {signalExit ? (
                <p className="muted">Signal exits: no separate missed-exit ledger beyond days the exit conditions did not fire while in a position.</p>
              ) : (
                <p className="muted">Holding-period exits are scheduled, not missed.</p>
              )}
            </section>
          </div>

          <section className="stack-block">
            <h2>P&L by round trip</h2>
            {trips.length ? (
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
                      <tr key={`${t.symbol}-${t.entrySession}-${i}`}>
                        <td>{t.symbol}</td>
                        <td>
                          {t.entryDecisionId != null ? (
                            <button
                              type="button"
                              className="linkish"
                              onClick={(e) =>
                                openChart(
                                  t.entryDecisionId as number,
                                  `${t.symbol} buy · ${t.entrySession}`,
                                  e.currentTarget,
                                )
                              }
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
                          {t.exitDecisionId != null ? (
                            <button
                              type="button"
                              className="linkish"
                              onClick={(e) =>
                                openChart(
                                  t.exitDecisionId as number,
                                  `${t.symbol} sell · ${t.exitSession}`,
                                  e.currentTarget,
                                )
                              }
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
            ) : (
              <p className="muted">No closed round trips.</p>
            )}
          </section>

          <div className="grid-2">
            <section className="stack-block">
              <h2>Drawdown vs {benchName === "SPY" ? "the S&P (SPY)" : benchName}</h2>
              {eq.length ? (
                <EquityChart
                  label="Drawdown"
                  dates={dates}
                  series={[
                    { name: r.name, values: dd, color: "var(--series-1)" },
                    { name: benchName, values: benchDd, color: "var(--series-bench)", dashed: true },
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
              {eq.length ? (
                <EquityChart
                  label="Exposure"
                  dates={dates}
                  series={[{ name: "Positions / equity", values: expos, color: "var(--accent)" }]}
                  formatTick={(v) => pct(v)}
                  formatValue={(v) => pct(v)}
                />
              ) : (
                <Loading />
              )}
              <p className="muted small">
                Time in market {pct(inMkt)} of sessions. {benchName === "SPY" ? "SPY" : benchName} stays invested the whole time.
              </p>
            </section>
          </div>

          <section className="stack-block">
            <h2>Trades</h2>
            <ErrorBox error={fills.error} />
            {fills.data ? (
              <FillsTable
                fills={fills.data}
                onOpen={(fill, el) =>
                  openChart(fill.decision_id as number, `${fill.symbol} ${fill.side} · ${fill.session}`, el)
                }
              />
            ) : (
              <Loading />
            )}
          </section>

          <section className="stack-block">
            <div className="section-head">
              <h2>Decisions</h2>
              <div className="row">
                <select
                  aria-label="Which decisions"
                  value={action}
                  onChange={(e) => {
                    setAction(e.target.value);
                    setOffset(0);
                  }}
                  style={{ width: "auto" }}
                >
                  {ACTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <input
                  aria-label="Symbol"
                  placeholder="Symbol"
                  value={symbol}
                  onChange={(e) => {
                    setSymbol(e.target.value.toUpperCase());
                    setOffset(0);
                  }}
                  style={{ width: 110 }}
                />
              </div>
            </div>
            <ErrorBox error={decisions.error} />
            {decisions.data ? (
              <>
                <DecisionsTable rows={decisions.data.rows as DecisionRow[]} />
                <div className="row" style={{ marginTop: 10 }}>
                  <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                    Previous
                  </button>
                  <span className="muted small">
                    {decisions.data.total === 0
                      ? "0 rows"
                      : `${offset + 1}–${Math.min(offset + PAGE, decisions.data.total)} of ${decisions.data.total.toLocaleString("en-US")}`}
                  </span>
                  <button disabled={offset + PAGE >= decisions.data.total} onClick={() => setOffset(offset + PAGE)}>
                    Next
                  </button>
                </div>
              </>
            ) : (
              <Loading />
            )}
          </section>

          <section className="stack-block">
            <details>
              <summary>Settings as run</summary>
              <pre className="json">{JSON.stringify(r.resolved, null, 2)}</pre>
            </details>
          </section>
          {chartPick ? (
            <TradeChartDialog
              runId={id}
              decisionId={chartPick.decisionId}
              title={chartPick.title}
              onClose={closeChart}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export default function ReportPage() {
  return (
    <Suspense fallback={<Loading />}>
      <ReportView />
    </Suspense>
  );
}
