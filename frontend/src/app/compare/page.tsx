"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, type DecisionRow, type FirstDifference, type RunDetail, type RunMetrics, type RunSummary } from "@/lib/api";
import { int, money, pct, plain, pp, sharpe } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { EquityChart } from "@/components/EquityChart";
import { DecisionCard, FillsTable } from "@/components/tables";
import { Badge, ErrorBox, Loading, PageHeader, Section } from "@/components/ui";

/** Newest run against its parent, else against the run before it. */
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
  const [inspecting, setInspecting] = useState<string>("");

  function pick(next: { a?: string; b?: string }) {
    const na = next.a ?? a;
    const nb = next.b ?? b;
    setInspecting("");
    router.replace(`/compare?a=${encodeURIComponent(na)}&b=${encodeURIComponent(nb)}`);
  }

  if (runs.error) return <ErrorBox error={runs.error} title="Could not load runs" />;
  if (!runs.data) return <Loading what="Loading runs" />;
  if (runs.data.length < 2 && !(params.get("a") && params.get("b")))
    return (
      <>
        <PageHeader title="Compare runs" />
        <p className="muted">
          You need at least two runs to compare. Open a run and use <em>Duplicate and edit</em>, or build one in the{" "}
          <Link href="/builder">Builder</Link>.
        </p>
      </>
    );

  const c = cmp.data;
  const fd = (c?.first_difference ?? null) as FirstDifference | null;
  const fdKey = fd ? `${fd.session}|${fd.symbol}` : "";
  const ma = (c?.a.metrics ?? {}) as RunMetrics;
  const mb = (c?.b.metrics ?? {}) as RunMetrics;
  const bench = ma.benchmark ?? {};
  const benchName = ((c?.a.resolved as { benchmark?: string } | undefined)?.benchmark ?? "Benchmark") as string;

  // Join both equity curves on session.
  const sessions = Array.from(new Set([...(eqA.data ?? []).map((e) => e.session), ...(eqB.data ?? []).map((e) => e.session)])).sort();
  const byA = new Map((eqA.data ?? []).map((e) => [e.session, e]));
  const byB = new Map((eqB.data ?? []).map((e) => [e.session, e]));

  return (
    <>
      <PageHeader
        title="Compare runs"
        lead="Two runs side by side. The first decision that differs comes first, because everything after it follows from it."
      />

      <div className="row" style={{ marginBottom: 16, alignItems: "flex-end" }}>
        <RunPicker id="compare-a" label="Run A" value={a} runs={runs.data} onChange={(v) => pick({ a: v })} />
        <RunPicker id="compare-b" label="Run B" value={b} runs={runs.data} onChange={(v) => pick({ b: v })} />
        <button onClick={() => pick({ a: b, b: a })} title="Swap A and B">
          Swap
        </button>
      </div>

      <ErrorBox error={cmp.error} title="Could not compare these runs" />
      {cmp.loading && !c ? <Loading what="Comparing" /> : null}

      {c ? (
        <>
          {c.data_differs ? (
            <div className="notice">
              These runs used different price data, so differences may come from the data rather than the rules. A:{" "}
              <Badge kind={c.a.source_kind}>{c.a.source_label}</Badge> B: <Badge kind={c.b.source_kind}>{c.b.source_label}</Badge>
            </div>
          ) : null}

          <Section title="First difference">
            {fd ? (
              <div className="first-diff" data-testid="first-difference" data-session={fd.session} data-symbol={fd.symbol}>
                <div className="diff-head">
                  <div>
                    <div className="big">
                      {fd.session} · {fd.symbol}
                    </div>
                    <div className="muted small">The earliest session and symbol where the two runs decided differently.</div>
                  </div>
                  <button
                    className="primary"
                    data-testid="inspect-first-difference"
                    onClick={() => setInspecting(inspecting === fdKey ? "" : fdKey)}
                  >
                    {inspecting === fdKey ? "Hide details" : "Inspect"}
                  </button>
                </div>
                <div className="grid-2">
                  <DecisionCard row={fd.a} title={`A: ${c.a.name}`} />
                  <DecisionCard row={fd.b} title={`B: ${c.b.name}`} />
                </div>
                <dl className="impact values">
                  {fd.impact.next_session ? (
                    <div>
                      <dt>Next session</dt>
                      <dd>{fd.impact.next_session}</dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>Cash after (A / B)</dt>
                    <dd>
                      {money(fd.impact.cash_a)} / {money(fd.impact.cash_b)}
                    </dd>
                  </div>
                  <div>
                    <dt>Final equity (A / B)</dt>
                    <dd>
                      {money(fd.impact.final_equity_a)} / {money(fd.impact.final_equity_b)}
                    </dd>
                  </div>
                  <div>
                    <dt>Final equity, B minus A</dt>
                    <dd className={(fd.impact.final_equity_delta ?? 0) >= 0 ? "pos" : "neg"}>{money(fd.impact.final_equity_delta)}</dd>
                  </div>
                </dl>
                {inspecting === fdKey ? <Inspector a={c.a} b={c.b} fd={fd} /> : null}
              </div>
            ) : (
              <p className="muted" data-testid="first-difference" data-session="" data-symbol="">
                Every decision is the same in both runs.
              </p>
            )}
          </Section>

          <Section title="What changed in the settings">
            {c.config_diff.length ? (
              <div className="table-wrap">
                <table data-testid="config-diff">
                  <thead>
                    <tr>
                      <th>Setting</th>
                      <th>A</th>
                      <th>B</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.config_diff.map((d) => (
                      <tr key={d.setting}>
                        <td>
                          <code>{d.setting}</code>
                        </td>
                        <td>{show(d.a)}</td>
                        <td>{show(d.b)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted" data-testid="config-diff">
                The settings are identical.
              </p>
            )}
          </Section>

          <Section title="Performance">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th className="num">Return</th>
                    <th className="num">vs {plain(benchName)}</th>
                    <th className="num">Max drawdown</th>
                    <th className="num">Sharpe</th>
                    <th className="num">Round trips</th>
                    <th className="num">Costs</th>
                    <th className="num">Final equity</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["A", c.a, ma],
                      ["B", c.b, mb],
                    ] as const
                  ).map(([k, run, m]) => (
                    <tr key={k}>
                      <td>
                        {k}: <Link href={`/run?id=${encodeURIComponent(run.id)}`}>{run.name}</Link>
                      </td>
                      <td className="num">{pct(m.total_return)}</td>
                      <td className="num">{pp(m.total_return, bench.total_return)}</td>
                      <td className="num">{pct(m.max_drawdown)}</td>
                      <td className="num">{sharpe(m.sharpe)}</td>
                      <td className="num">{int(m.trades)}</td>
                      <td className="num">{money(m.costs)}</td>
                      <td className="num">{money(m.final_equity)}</td>
                    </tr>
                  ))}
                  <tr className="muted">
                    <td>{plain(benchName)} (bought once, held)</td>
                    <td className="num">{pct(bench.total_return)}</td>
                    <td className="num"></td>
                    <td className="num">{pct(bench.max_drawdown)}</td>
                    <td className="num">{sharpe(bench.sharpe)}</td>
                    <td className="num"></td>
                    <td className="num">{money(bench.cost)}</td>
                    <td className="num"></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Account value">
            {sessions.length ? (
              <EquityChart
                dates={sessions}
                series={[
                  { name: `A: ${c.a.name}`, values: sessions.map((s) => byA.get(s)?.equity ?? null), color: "var(--series-1)" },
                  { name: `B: ${c.b.name}`, values: sessions.map((s) => byB.get(s)?.equity ?? null), color: "var(--series-2)" },
                  {
                    name: plain(benchName),
                    values: sessions.map((s) => byA.get(s)?.benchmark_equity ?? null),
                    color: "var(--series-bench)",
                    dashed: true,
                  },
                ]}
              />
            ) : (
              <Loading />
            )}
          </Section>

          <div>
            <Section title={`Trades in A`}>
              <ErrorBox error={fillsA.error} />
              {fillsA.data ? <FillsTable fills={fillsA.data} /> : <Loading />}
            </Section>
            <Section title={`Trades in B`}>
              <ErrorBox error={fillsB.error} />
              {fillsB.data ? <FillsTable fills={fillsB.data} /> : <Loading />}
            </Section>
          </div>
        </>
      ) : null}
    </>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<Loading />}>
      <CompareView />
    </Suspense>
  );
}
