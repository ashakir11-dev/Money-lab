"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useRef, useState } from "react";
import {
  ByYearTable,
  ExitTable,
  FunnelSection,
  LowSample,
  MonthlyGrid,
  RegimeTable,
  StatGroup,
  SymbolTable,
  costStats,
  riskStats,
  streakStats,
  tradeQualityStats,
  versusStats,
  EXIT_LABEL,
  signed,
} from "@/components/Analysis";
import { EquityChart } from "@/components/EquityChart";
import { RunsTable } from "@/components/RunsTable";
import { Specimen } from "@/components/Specimen";
import { StatBreakdown, type StatItem } from "@/components/StatBreakdown";
import { TradeChartDialog } from "@/components/TradeChartDialog";
import { DecisionsTable, FillsTable } from "@/components/tables";
import { Badge, ErrorBox, JobStatus, Loading } from "@/components/ui";
import {
  drawdownSeries,
  exposureSeries,
  fillBreakdown,
  marksFromFills,
  pnlSummary,
  barsPerYear,
  reasonCounts,
  rollingReturn,
  sweepEnd,
  timeInMarket,
} from "@/lib/analysis";
import { api, waitForJob, type DecisionRow, type Job, type RunMetrics, type RunSummary, type Watchlist } from "@/lib/api";
import { int, money, num, pct, sharpe, versusSpy, versusSpyDollars, when } from "@/lib/format";
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

function toneOf(n: number | null | undefined): "pos" | "neg" | undefined {
  if (typeof n !== "number") return undefined;
  return n >= 0 ? "pos" : "neg";
}

function metricStats({
  benchName,
  m,
  b,
  pnl,
  inMkt,
}: {
  benchName: string;
  m: RunMetrics;
  b: NonNullable<RunMetrics["benchmark"]>;
  pnl: { total: number; vsBenchmark: number | null; realized: number; unrealized: number };
  inMkt: number | null;
}): StatItem[] {
  const gap =
    typeof m.total_return === "number" && typeof b.total_return === "number"
      ? m.total_return - b.total_return
      : null;
  return [
    {
      label: "Return",
      value: pct(m.total_return),
      tone: toneOf(m.total_return),
      meaning:
        "How much the account grew or shrank from the first day of the run to the last, compared with what it was worth at the start. 10% means the account ended one tenth larger than it began.",
    },
    ...(benchName === "SPY"
      ? [
          {
            label: "SPY return",
            value: pct(b.total_return),
            tone: toneOf(b.total_return),
            meaning:
              "What buying SPY, the S&P 500 ETF, and holding it would have made over this same run, with the same starting cash and trading costs. This is the S&P result these comparisons use.",
          },
        ]
      : []),
    {
      label: benchName === "SPY" ? "Versus the S&P" : `Versus ${benchName}`,
      value: versusSpy(m.total_return, b.total_return, benchName),
      tone: toneOf(gap),
      meaning:
        benchName === "SPY"
          ? "Whether this run beat the S&P 500. Beat SPY means the account's percent return finished above buy-and-hold SPY. Trailed SPY means it finished below. The gap is in percentage points: 12% against SPY's 9% is Beat SPY by 3.00 pp."
          : `Whether this run beat ${benchName}, the benchmark saved with this run. This run was not scored against SPY, so this line is not an S&P result.`,
    },
    {
      label: "Max drawdown",
      value: pct(m.max_drawdown),
      meaning:
        "The deepest fall from a high in the account to a later low, measured against that high. −20% means that, at the worst moment, the account was a fifth below an earlier peak. This is the worst dip along the way, not the result at the end. A number closer to zero is a shallower dip.",
    },
    {
      label: `${benchName} drawdown`,
      value: pct(b.max_drawdown),
      meaning: `The same deepest-fall measure for ${benchName}, which stays invested the whole time. Set it next to this run's max drawdown to see which path fell harder from its own peak.`,
    },
    {
      label: "Sharpe",
      value: sharpe(m.sharpe),
      meaning:
        "A score for how much the account gained for the amount it jumped around from bar to bar. Higher means more gain for the amount of bouncing. It uses each bar's percent change in account value (a bar is one day on daily runs, one hour on hourly runs, and so on), treats cash as earning nothing, and scales the score to a full year. It reads \"undefined\" when those changes never vary, because there is no bouncing to measure.",
    },
    {
      label: "Profit and loss",
      value: money(pnl.total),
      tone: toneOf(pnl.total),
      meaning: "The ending account value minus the cash the run started with. This is the return, written in dollars instead of a percent.",
    },
    {
      label: benchName === "SPY" ? "Versus the S&P in dollars" : `Versus ${benchName} in dollars`,
      value: versusSpyDollars(pnl.vsBenchmark, benchName),
      tone: toneOf(pnl.vsBenchmark),
      meaning:
        benchName === "SPY"
          ? "The same S&P comparison in dollars: this run's profit minus the profit from buying SPY and holding it with the same starting cash. Beat SPY means the account finished ahead of the S&P in dollars."
          : `This run's profit minus the profit from buying ${benchName} and holding it. This run was not scored against SPY.`,
    },
    {
      label: "Realized",
      value: money(pnl.realized),
      tone: toneOf(pnl.realized),
      meaning:
        "Dollars already locked in from shares that were bought and later sold, after the trading costs on those buys and sells. This number does not change once the sale is done.",
    },
    {
      label: "Unrealized",
      value: money(pnl.unrealized),
      tone: toneOf(pnl.unrealized),
      meaning:
        "Dollars still at stake in shares that have not been sold. It is what those shares are worth now, minus what was paid for them, including the cost of buying them. It keeps moving until the shares are sold.",
    },
    {
      label: "Costs",
      value: money(m.costs),
      meaning:
        "Trading costs charged on the buys and sells in this run. They have already been taken out of profit and loss, the return, and the final account value.",
    },
    {
      label: "Final equity",
      value: money(m.final_equity),
      meaning: "What the account was worth on the last day: leftover cash plus the value of any shares still held.",
    },
    {
      label: "Round trips",
      value: int(m.trades),
      meaning: "How many times a position was bought and then fully sold. A position still open at the end is not included.",
    },
    {
      label: "Average exposure",
      value: pct(m.avg_exposure),
      meaning:
        "On a typical day, how much of the account was in stocks rather than cash. 100% means the account was fully invested. 0% means it sat entirely in cash.",
    },
    {
      label: "Time in market",
      value: pct(inMkt),
      meaning: `The share of days the account held at least one stock. ${benchName} is invested every day, so a run that spends time in cash is in the market less often than ${benchName}.`,
    },
    {
      label: "Open positions",
      value: int(m.open_positions ?? 0),
      meaning: "How many buys were still unmatched by a sell when the run ended.",
    },
  ];
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

function ReportView() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const list = useAsync(() => api.runs(), []);
  const run = useAsync(() => api.run(id), [id], !!id);
  const equity = useAsync(() => api.equity(id), [id], !!id);
  const fills = useAsync(() => api.fills(id), [id], !!id);
  const skips = useAsync(() => api.decisions(id, { action: ["skip"], limit: 1000 }), [id], !!id);
  const near = useAsync(() => api.decisions(id, { action: ["no_signal"], limit: 500 }), [id], !!id);
  const tradeList = useAsync(() => api.trades(id), [id], !!id);
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
          <>
            <RunPicker runs={list.data} id="" />
            <RunsTable runs={list.data} />
            <p className="caption">
              Runs saved before these columns existed fill in when you open them once, or all at once with{" "}
              <code>uv run moneylab recompute-metrics</code>.
            </p>
          </>
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
  const trips = tradeList.data ?? [];
  const an = r.analysis ?? null;
  const swept = sweepEnd(eq);
  const tf = String((r.resolved as { timeframe?: string }).timeframe ?? "1d");
  const roll = barsPerYear(tf);
  const rollOurs = rollingReturn(eq.map((e) => e.equity), roll);
  const rollBench = rollingReturn(eq.map((e) => e.benchmark_equity), roll);
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
        {r.parent_id ? (
          <>
            {" "}
            · copy of <Link href={`/report?id=${encodeURIComponent(r.parent_id)}`}>{parent.data?.name ?? r.parent_id}</Link>
          </>
        ) : null}
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
          {swept != null ? (
            <>
              {" "}
              · Idle cash held in {benchName} at the end: {money(swept)} (kept out of &ldquo;Average exposure&rdquo; and
              of the trade counts)
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

      {an ? (
        <>
          <StatGroup
            title="Trade quality"
            testId="trade-quality"
            badge={<LowSample trades={an.trades.count} />}
            intro={`${int(an.trades.count)} closed ${an.trades.count === 1 ? "trade" : "trades"}. Results from fewer than 30 trades are shown but should not be trusted.`}
            stats={tradeQualityStats(an)}
          />
          <StatGroup
            title={`Versus ${benchName}`}
            testId="versus-spy"
            badge={<LowSample trades={an.edge.count} />}
            intro={
              (resolved.portfolio as { idle_cash?: string } | undefined)?.idle_cash === "benchmark"
                ? `Idle cash was held in ${benchName}, so this compares the strategy's stock picks with simply staying in ${benchName}.`
                : `Cash not in a position sat uninvested, so being in cash is part of any gap to ${benchName}. The "Hold ${benchName}" idle cash setting in the Builder removes that effect.`
            }
            stats={versusStats(an, benchName)}
          />
          <StatGroup title="Risk" testId="risk" stats={riskStats(an)} />
        </>
      ) : !run.loading ? (
        <p className="muted" data-testid="no-analysis">
          The detailed analysis is not available for this run (its price data is no longer on this machine).
        </p>
      ) : null}

      {detailed ? (
        <>
          {an ? (
            <>
              <ByYearTable a={an} benchName={benchName} />
              <MonthlyGrid a={an} />
              <section className="stack-block" data-testid="rolling-return">
                <h2>Rolling one-year return vs {benchName === "SPY" ? "the S&P (SPY)" : benchName}</h2>
                <p className="muted small">
                  Each point is the return over the previous year. Where the strategy line is above the dashed line it was
                  ahead of {benchName} over that year.
                </p>
                {eq.length > roll ? (
                  <EquityChart
                    label="Rolling one-year return"
                    dates={dates}
                    series={[
                      { name: r.name, values: rollOurs, color: "var(--series-1)" },
                      { name: benchName, values: rollBench, color: "var(--series-bench)", dashed: true },
                    ]}
                    formatTick={(v) => pct(v)}
                    formatValue={(v) => pct(v)}
                  />
                ) : (
                  <p className="muted">Needs more than a year of data.</p>
                )}
              </section>
              <ExitTable a={an} />
              <SymbolTable a={an} />
              <RegimeTable a={an} benchName={benchName} />
              <div className="grid-2">
                <StatGroup title="Costs and turnover" testId="costs" stats={costStats(an)} />
                <StatGroup title="Streaks" testId="streaks" stats={streakStats(an)} />
              </div>
              <FunnelSection a={an} />
            </>
          ) : null}
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
            <h2>Closed trades</h2>
            <p className="muted small">
              Return is after costs. Vs {benchName} is the trade&apos;s return minus {benchName}&apos;s over the same days.
              Worst and best are the lowest and highest price reached while the trade was open, against the buy price.
              Hold counts sessions from the buy fill to the sell fill.
            </p>
            <ErrorBox error={tradeList.error} />
            {trips.length ? (
              <div className="table-wrap" data-testid="trade-table">
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th className="num">Qty</th>
                      <th className="num">P&L</th>
                      <th className="num">Return</th>
                      <th className="num">Vs {benchName}</th>
                      <th className="num">Worst</th>
                      <th className="num">Best</th>
                      <th>Ended by</th>
                      <th className="num">Hold</th>
                      <th className="num">Costs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trips.map((t, i) => (
                      <tr key={`${t.symbol}-${t.entry_session}-${i}`}>
                        <td>{t.symbol}</td>
                        <td>
                          {t.entry_decision_id != null ? (
                            <button
                              type="button"
                              className="linkish"
                              onClick={(e) =>
                                openChart(t.entry_decision_id as number, `${t.symbol} buy · ${t.entry_session}`, e.currentTarget)
                              }
                            >
                              {t.entry_session} @ {money(t.entry_price)}
                            </button>
                          ) : (
                            <>
                              {t.entry_session} @ {money(t.entry_price)}
                            </>
                          )}
                        </td>
                        <td>
                          {t.exit_decision_id != null ? (
                            <button
                              type="button"
                              className="linkish"
                              onClick={(e) =>
                                openChart(t.exit_decision_id as number, `${t.symbol} sell · ${t.exit_session}`, e.currentTarget)
                              }
                            >
                              {t.exit_session} @ {money(t.exit_price)}
                            </button>
                          ) : (
                            <>
                              {t.exit_session} @ {money(t.exit_price)}
                            </>
                          )}
                        </td>
                        <td className="num">{int(t.qty)}</td>
                        <td className={`num ${t.pnl >= 0 ? "pos" : "neg"}`}>{money(t.pnl)}</td>
                        <td className={`num ${(t.ret ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(t.ret, true)}</td>
                        <td className={`num ${(t.edge ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(t.edge, true)}</td>
                        <td className="num">{pct(t.mae)}</td>
                        <td className="num">{pct(t.mfe)}</td>
                        <td>{EXIT_LABEL[t.exit_kind] ?? t.exit_kind}</td>
                        <td className="num">{t.hold_sessions != null ? `${int(t.hold_sessions)} sess` : "–"}</td>
                        <td className="num">{money(t.costs)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : tradeList.loading ? (
              <Loading />
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
