"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { FirstDifferenceReadout } from "@/components/FirstDiff";
import { Specimen } from "@/components/Specimen";
import { FillsTable } from "@/components/tables";
import { Badge, ErrorBox, Loading } from "@/components/ui";
import { fillBreakdown, pnlSummary } from "@/lib/analysis";
import { api, type EquityRow, type FirstDifference, type RunMetrics, type RunSummary } from "@/lib/api";
import { int, money, pct, plain, pp, sharpe, versusSpy, versusSpyDollars } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

function defaultPair(runs: RunSummary[]): [string, string] | null {
  if (runs.length < 2) return null;
  const newest = runs[0];
  if (newest.parent_id && runs.some((r) => r.id === newest.parent_id)) return [newest.parent_id, newest.id];
  return [runs[1].id, newest.id];
}

function show(v: unknown): string {
  if (v === undefined) return "(not set)";
  if (v === null) return "none";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function humanSetting(path: string): string {
  const named: Record<string, string> = {
    universe: "Universe",
    benchmark: "Benchmark",
    name: "Run name",
    start: "First session",
    end: "Last session",
  };
  if (named[path]) return named[path];
  const indexed = path.match(/^(entry|exits|exit)\[(\d+)\](?:\.(.+))?$/);
  if (indexed) {
    const kind = indexed[1] === "entry" ? "Entry" : "Exit";
    const n = Number(indexed[2]) + 1;
    const rest = (indexed[3] ?? "")
      .replace(/^params\./, "")
      .replace(/[._]/g, " ")
      .replace(/\s*id$/, "")
      .trim();
    const word =
      rest === "value"
        ? "threshold"
        : rest === "op"
          ? "comparison"
          : rest === "pct"
            ? "percent"
            : rest || "setting";
    return `${kind} ${n} ${word}`;
  }
  return path.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function showSetting(path: string, v: unknown): string {
  if (typeof v === "number" && /pct|fraction/i.test(path) && Math.abs(v) <= 1) {
    return `${(v * 100).toFixed(2)}%`;
  }
  return show(v);
}

function startCash(resolved: Record<string, unknown> | undefined): number {
  const pf = resolved?.portfolio as { starting_cash?: number } | undefined;
  return pf?.starting_cash ?? 100000;
}

function spyTone(symbol: string, strategy: number | null | undefined, spy: number | null | undefined): string {
  if (symbol !== "SPY" || typeof strategy !== "number" || typeof spy !== "number") return "num";
  return strategy >= spy ? "num pos" : "num neg";
}

function dollarTone(symbol: string, delta: number | null | undefined): string {
  if (symbol !== "SPY" || typeof delta !== "number") return "num";
  return delta >= 0 ? "num pos" : "num neg";
}

function benchSymbol(metrics: RunMetrics, resolved: unknown): string {
  const fromRun = (resolved as { benchmark?: string } | undefined)?.benchmark;
  return String(metrics.benchmark?.symbol || fromRun || "SPY").toUpperCase();
}

/** Buy-and-hold SPY on the shared date axis. One line when both runs share that path. */
function spySeries(
  sessions: string[],
  byA: Map<string, EquityRow>,
  byB: Map<string, EquityRow>,
  symbolA: string,
  symbolB: string,
): { name: string; values: (number | null)[]; color: string; dashed: true }[] {
  const aVals = sessions.map((s) => (symbolA === "SPY" ? (byA.get(s)?.benchmark_equity ?? null) : null));
  const bVals = sessions.map((s) => (symbolB === "SPY" ? (byB.get(s)?.benchmark_equity ?? null) : null));
  if (symbolA !== "SPY" && symbolB !== "SPY") return [];
  const same = sessions.every((_, i) => {
    const x = aVals[i];
    const y = bVals[i];
    if (typeof x !== "number" || typeof y !== "number") return true;
    return Math.abs(x - y) < 0.05;
  });
  if (symbolA !== "SPY" || symbolB !== "SPY" || same) {
    return [
      {
        name: "SPY",
        values: sessions.map((_, i) => (typeof aVals[i] === "number" ? aVals[i] : bVals[i])),
        color: "var(--series-bench)",
        dashed: true,
      },
    ];
  }
  return [
    { name: "SPY (A)", values: aVals, color: "var(--series-bench)", dashed: true },
    { name: "SPY (B)", values: bVals, color: "var(--ink-2)", dashed: true },
  ];
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
  const [detailed, setDetailed] = useState(false);

  function pick(next: { a?: string; b?: string }) {
    const na = next.a ?? a;
    const nb = next.b ?? b;
    router.replace(`/compare?a=${encodeURIComponent(na)}&b=${encodeURIComponent(nb)}`);
  }

  if (runs.error) return <ErrorBox error={runs.error} title="Could not load runs" />;
  if (!runs.data) return <Loading what="Loading runs" />;
  if (runs.data.length < 2 && !(params.get("a") && params.get("b")))
    return (
      <div className="bench">
        <h1>Compare</h1>
        <p className="muted">
          You need at least two runs. Open a report and use <em>Duplicate and edit</em>, or build one in the{" "}
          <Link href="/builder">Builder</Link>.
        </p>
      </div>
    );

  const c = cmp.data;
  const fd = (c?.first_difference ?? null) as FirstDifference | null;
  const ma = (c?.a.metrics ?? {}) as RunMetrics;
  const mb = (c?.b.metrics ?? {}) as RunMetrics;
  const benchA = ma.benchmark ?? {};
  const benchB = mb.benchmark ?? {};
  const symbolA = c ? benchSymbol(ma, c.a.resolved) : "SPY";
  const symbolB = c ? benchSymbol(mb, c.b.resolved) : "SPY";
  const sessions = Array.from(new Set([...(eqA.data ?? []).map((e) => e.session), ...(eqB.data ?? []).map((e) => e.session)])).sort();
  const byA = new Map((eqA.data ?? []).map((e) => [e.session, e]));
  const byB = new Map((eqB.data ?? []).map((e) => [e.session, e]));
  const brA = fillBreakdown(fillsA.data ?? []);
  const brB = fillBreakdown(fillsB.data ?? []);
  const pnlA = pnlSummary(fillsA.data ?? [], eqA.data ?? [], ma, startCash(c?.a.resolved as Record<string, unknown>));
  const pnlB = pnlSummary(fillsB.data ?? [], eqB.data ?? [], mb, startCash(c?.b.resolved as Record<string, unknown>));

  const delta = (x: number | null | undefined, y: number | null | undefined) =>
    typeof x === "number" && typeof y === "number" ? y - x : null;

  return (
    <div className="bench">
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

      {c ? (
        <>
          {c.data_differs ? (
            <p className="notice">
              Different price data. A: <Badge kind={c.a.source_kind}>{c.a.source_label}</Badge> {c.a.snapshot_name}{" "}
              B: <Badge kind={c.b.source_kind}>{c.b.source_label}</Badge> {c.b.snapshot_name}
            </p>
          ) : null}

          {c.config_diff.length ? (
            <div className="chip-strip" data-testid="config-diff">
              {c.config_diff.map((d) => (
                <span key={d.setting} className="chip">
                  <strong>{humanSetting(d.setting)}</strong>
                  <span>{showSetting(d.setting, d.a)}</span>
                  <span aria-hidden="true">→</span>
                  <span>{showSetting(d.setting, d.b)}</span>
                </span>
              ))}
            </div>
          ) : (
            <p className="muted" data-testid="config-diff">
              Settings are identical.
            </p>
          )}

          {sessions.length >= 2 ? (
            <Specimen
              dates={sessions}
              series={[
                { name: c.a.name, values: sessions.map((s) => byA.get(s)?.equity ?? null), color: "var(--series-1)" },
                { name: c.b.name, values: sessions.map((s) => byB.get(s)?.equity ?? null), color: "var(--series-2)" },
                ...spySeries(sessions, byA, byB, symbolA, symbolB),
              ]}
              pinDate={fd?.session}
              caption={[
                fd ? `${fd.session} · ${fd.symbol}` : null,
                symbolA === "SPY" ? `A ${versusSpy(ma.total_return, benchA.total_return).replace(/^./, (c) => c.toLowerCase())}` : `A benchmark is ${symbolA}, not SPY`,
                symbolB === "SPY" ? `B ${versusSpy(mb.total_return, benchB.total_return).replace(/^./, (c) => c.toLowerCase())}` : `B benchmark is ${symbolB}, not SPY`,
              ]
                .filter(Boolean)
                .join(" · ")}
            >
              <FirstDifferenceReadout fd={fd} a={c.a} b={c.b} />
            </Specimen>
          ) : (
            <>
              {eqA.loading || eqB.loading ? (
                <div className="specimen-well specimen-wait" />
              ) : (
                <p className="muted">Not enough equity points to draw a specimen.</p>
              )}
              <FirstDifferenceReadout fd={fd} a={c.a} b={c.b} />
            </>
          )}

          <section className="stack-block">
            <h2>Breakdown</h2>
            <div className="table-wrap">
              <table className="pair">
                <thead>
                  <tr>
                    <th></th>
                    <th className="num side-a">A</th>
                    <th className="num side-b">B</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Buys / sells</td>
                    <td className="num">
                      {int(brA.buys)} / {int(brA.sells)}
                    </td>
                    <td className="num">
                      {int(brB.buys)} / {int(brB.sells)}
                    </td>
                  </tr>
                  <tr>
                    <td>Symbols traded</td>
                    <td className="num">{brA.symbols.length ? brA.symbols.join(", ") : "–"}</td>
                    <td className="num">{brB.symbols.length ? brB.symbols.join(", ") : "–"}</td>
                  </tr>
                  <tr>
                    <td>Still open</td>
                    <td className="num">{brA.held.length ? brA.held.join(", ") : int(0)}</td>
                    <td className="num">{brB.held.length ? brB.held.join(", ") : int(0)}</td>
                  </tr>
                  <tr>
                    <td>Costs</td>
                    <td className="num">{money(brA.costs)}</td>
                    <td className="num">{money(brB.costs)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className="stack-block">
            <h2>Statistics</h2>
            <div className="table-wrap">
              <table className="pair">
                <thead>
                  <tr>
                    <th></th>
                    <th className="num side-a">
                      <Link href={`/report?id=${encodeURIComponent(c.a.id)}`}>A</Link>
                    </th>
                    <th className="num side-b">
                      <Link href={`/report?id=${encodeURIComponent(c.b.id)}`}>B</Link>
                    </th>
                    <th className="num">B − A</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Return</td>
                    <td className="num">{pct(ma.total_return)}</td>
                    <td className="num">{pct(mb.total_return)}</td>
                    <td className="num">{pp(mb.total_return, ma.total_return)}</td>
                  </tr>
                  <tr>
                    <td>SPY return</td>
                    <td className="num">{symbolA === "SPY" ? pct(benchA.total_return) : "–"}</td>
                    <td className="num">{symbolB === "SPY" ? pct(benchB.total_return) : "–"}</td>
                    <td className="num">
                      {symbolA === "SPY" && symbolB === "SPY" ? pp(benchB.total_return, benchA.total_return) : ""}
                    </td>
                  </tr>
                  <tr>
                    <td>vs SPY</td>
                    <td className={spyTone(symbolA, ma.total_return, benchA.total_return)}>
                      {symbolA === "SPY" ? versusSpy(ma.total_return, benchA.total_return) : `Benchmark is ${plain(symbolA)}`}
                    </td>
                    <td className={spyTone(symbolB, mb.total_return, benchB.total_return)}>
                      {symbolB === "SPY" ? versusSpy(mb.total_return, benchB.total_return) : `Benchmark is ${plain(symbolB)}`}
                    </td>
                    <td className="num">
                      {symbolA === "SPY" && symbolB === "SPY"
                        ? pp(
                            typeof mb.total_return === "number" && typeof benchB.total_return === "number"
                              ? mb.total_return - benchB.total_return
                              : null,
                            typeof ma.total_return === "number" && typeof benchA.total_return === "number"
                              ? ma.total_return - benchA.total_return
                              : null,
                          )
                        : ""}
                    </td>
                  </tr>
                  <tr>
                    <td>Max drawdown</td>
                    <td className="num">{pct(ma.max_drawdown)}</td>
                    <td className="num">{pct(mb.max_drawdown)}</td>
                    <td className="num">{pp(mb.max_drawdown, ma.max_drawdown)}</td>
                  </tr>
                  <tr>
                    <td>Sharpe</td>
                    <td className="num">{sharpe(ma.sharpe)}</td>
                    <td className="num">{sharpe(mb.sharpe)}</td>
                    <td className="num"></td>
                  </tr>
                  <tr>
                    <td>P&L</td>
                    <td className={`num ${pnlA.total >= 0 ? "pos" : "neg"}`}>{money(pnlA.total)}</td>
                    <td className={`num ${pnlB.total >= 0 ? "pos" : "neg"}`}>{money(pnlB.total)}</td>
                    <td className="num">{money(delta(pnlA.total, pnlB.total))}</td>
                  </tr>
                  <tr>
                    <td>vs SPY $</td>
                    <td className={dollarTone(symbolA, pnlA.vsBenchmark)}>
                      {symbolA === "SPY" ? versusSpyDollars(pnlA.vsBenchmark) : "–"}
                    </td>
                    <td className={dollarTone(symbolB, pnlB.vsBenchmark)}>
                      {symbolB === "SPY" ? versusSpyDollars(pnlB.vsBenchmark) : "–"}
                    </td>
                    <td className="num">
                      {symbolA === "SPY" && symbolB === "SPY" ? money(delta(pnlA.vsBenchmark, pnlB.vsBenchmark)) : ""}
                    </td>
                  </tr>
                  <tr>
                    <td>Final equity</td>
                    <td className="num">{money(ma.final_equity)}</td>
                    <td className="num">{money(mb.final_equity)}</td>
                    <td className="num">{money(delta(ma.final_equity, mb.final_equity))}</td>
                  </tr>
                  {detailed ? (
                    <>
                      <tr>
                        <td>Realized P&L</td>
                        <td className="num">{money(pnlA.realized)}</td>
                        <td className="num">{money(pnlB.realized)}</td>
                        <td className="num">{money(delta(pnlA.realized, pnlB.realized))}</td>
                      </tr>
                      <tr>
                        <td>Unrealized P&L</td>
                        <td className="num">{money(pnlA.unrealized)}</td>
                        <td className="num">{money(pnlB.unrealized)}</td>
                        <td className="num">{money(delta(pnlA.unrealized, pnlB.unrealized))}</td>
                      </tr>
                      <tr>
                        <td>Avg exposure</td>
                        <td className="num">{pct(ma.avg_exposure)}</td>
                        <td className="num">{pct(mb.avg_exposure)}</td>
                        <td className="num"></td>
                      </tr>
                      <tr>
                        <td>Round trips</td>
                        <td className="num">{int(ma.trades)}</td>
                        <td className="num">{int(mb.trades)}</td>
                        <td className="num">{int(delta(ma.trades, mb.trades))}</td>
                      </tr>
                      <tr>
                        <td>Costs</td>
                        <td className="num">{money(ma.costs)}</td>
                        <td className="num">{money(mb.costs)}</td>
                        <td className="num">{money(delta(ma.costs, mb.costs))}</td>
                      </tr>
                      {fd ? (
                        <>
                          <tr>
                            <td>Cash at first difference</td>
                            <td className="num">{money(fd.impact.cash_a)}</td>
                            <td className="num">{money(fd.impact.cash_b)}</td>
                            <td className="num">{money(delta(fd.impact.cash_a, fd.impact.cash_b))}</td>
                          </tr>
                          <tr>
                            <td>Final equity (from fork)</td>
                            <td className="num">{money(fd.impact.final_equity_a)}</td>
                            <td className="num">{money(fd.impact.final_equity_b)}</td>
                            <td className="num">{money(fd.impact.final_equity_delta)}</td>
                          </tr>
                        </>
                      ) : null}
                    </>
                  ) : null}
                </tbody>
              </table>
            </div>
            <p className="caption">
              SPY is buy-and-hold of the S&P 500 ETF on each run&apos;s own dates, starting cash, and trading costs. Beat
              SPY means that run finished ahead of the S&P. The B − A column on vs SPY is how much more B beat the S&P
              than A did.
            </p>
          </section>

          {detailed ? (
            <div className="grid-2">
              <section className="stack-block">
                <h2>Trades in A</h2>
                <ErrorBox error={fillsA.error} />
                {fillsA.data ? <FillsTable fills={fillsA.data} /> : <Loading />}
              </section>
              <section className="stack-block">
                <h2>Trades in B</h2>
                <ErrorBox error={fillsB.error} />
                {fillsB.data ? <FillsTable fills={fillsB.data} /> : <Loading />}
              </section>
            </div>
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
