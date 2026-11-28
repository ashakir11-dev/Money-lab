"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { EquityChart } from "@/components/EquityChart";
import { TradeChartDialog } from "@/components/TradeChartDialog";
import { DecisionsTable, FillsTable } from "@/components/tables";
import { ErrorBox, Loading } from "@/components/ui";
import { chartRequestForFill, roundTrips } from "@/lib/analysis";
import { api, batchExportPath, type DecisionRow, type FillRow, type FirstDifference, type RunMetrics, type RunSummary, type TradeRow } from "@/lib/api";
import { pct, sharpe, versusSpy, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

const SLOTS = ["a", "b", "c"] as const;

function slotsFrom(params: { get(name: string): string | null }): string[] {
  const slots = SLOTS.map((key) => params.get(key) ?? "");
  if (!slots[0] && params.get("id")) slots[0] = params.get("id") ?? "";
  return slots;
}

function RunReport({ id, runs }: { id: string; runs: RunSummary[] }) {
  const run = useAsync(() => api.run(id), [id]);
  const equity = useAsync(() => api.equity(id), [id]);
  const fills = useAsync(() => api.fills(id), [id]);
  const trades = useAsync(() => api.trades(id), [id]);
  const decisions = useAsync(
    () => api.decisions(id, { action: ["enter", "exit", "skip"], limit: 80 }),
    [id],
  );
  const [chart, setChart] = useState<{ decisionId: number; pairedDecisionId?: number; title: string } | null>(null);
  const detail = run.data;
  const metrics = detail?.metrics as RunMetrics | undefined;
  const bench = metrics?.benchmark;
  const benchName = bench?.symbol || detail?.benchmark_symbol || "S&P";
  const rows = (equity.data ?? []).filter((row) => row.session);
  const fillRows = (fills.data ?? []) as FillRow[];
  const trips = useMemo(() => roundTrips((trades.data ?? []) as TradeRow[]), [trades.data]);
  const decisionRows = (decisions.data?.rows ?? []) as DecisionRow[];

  if (run.loading) return <Loading what="Loading run" />;
  if (run.error || !detail) return <ErrorBox error={run.error} title="Could not load this run" />;

  return (
    <article className="analytics-run" data-testid="analytics-run">
      <header>
        <h2>{detail.name}</h2>
        <p className="lead">
          {pct(metrics?.total_return, true)} · {versusSpy(metrics?.total_return, bench?.total_return, benchName)} ·
          drawdown {pct(metrics?.max_drawdown)} · Sharpe {sharpe(metrics?.sharpe)}
        </p>
        <p className="muted small">
          {runs.find((item) => item.id === id)?.summary} · {when(detail.created_at)}
        </p>
      </header>
      {rows.length ? (
        <EquityChart
          dates={rows.map((row) => row.session)}
          series={[
            { name: detail.name, values: rows.map((row) => row.equity), color: "var(--series-1)" },
            {
              name: benchName,
              values: rows.map((row) => row.benchmark_equity ?? null),
              color: "var(--series-bench)",
              dashed: true,
            },
          ]}
        />
      ) : null}
      <p className="muted small">
        The benchmark line is this run’s own {benchName} buy-and-hold over {rows[0]?.session ?? "its start"} to{" "}
        {rows.at(-1)?.session ?? "its end"}.
      </p>
      <h3>Trades</h3>
      <FillsTable
        fills={fillRows}
        onOpen={(fill, opener) => {
          opener.focus();
          const request = chartRequestForFill(fill, trips);
          if (request) setChart(request);
        }}
      />
      <h3>Decisions</h3>
      {decisions.loading ? <Loading what="Loading decisions" /> : <DecisionsTable rows={decisionRows} />}
      <p>
        <Link href={`/report?id=${encodeURIComponent(id)}`}>Full report</Link>
      </p>
      {chart ? (
        <TradeChartDialog
          runId={id}
          decisionId={chart.decisionId}
          pairedDecisionId={chart.pairedDecisionId}
          title={chart.title}
          onClose={() => setChart(null)}
        />
      ) : null}
    </article>
  );
}

function PairDiff({ a, b }: { a: string; b: string }) {
  const cmp = useAsync(() => api.compare(a, b), [a, b]);
  if (cmp.loading) return <Loading what="Comparing the two runs" />;
  if (cmp.error || !cmp.data) return <ErrorBox error={cmp.error} title="Could not compare these runs" />;
  const diff = cmp.data.config_diff ?? [];
  const first = cmp.data.first_difference as FirstDifference | null;
  return (
    <section className="pair-diff" data-testid="analytics-diff">
      <h2>What changed between the two runs</h2>
      {diff.length ? (
        <ul>
          {diff.slice(0, 12).map((row) => (
            <li key={row.setting}>
              {row.setting}: {String(row.a)} → {String(row.b)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">The settings match. A difference here would be the universe, dates, or data.</p>
      )}
      {first ? (
        <p data-testid="first-difference">
          First different decision: {first.symbol} on {first.session}. {first.a.action} versus {first.b.action}.
        </p>
      ) : (
        <p>No differing decision. The ledgers walk together.</p>
      )}
    </section>
  );
}

function AnalyticsDesk({ runs }: { runs: RunSummary[] }) {
  const params = useSearchParams();
  const router = useRouter();
  const [slots, setSlots] = useState(() => slotsFrom(params));
  const chosen = slots.filter(Boolean).slice(0, 3);
  const batchIds = [...new Set(chosen)];

  function choose(index: number, id: string) {
    const next = [...slots];
    next[index] = id;
    setSlots(next);
    const query = new URLSearchParams();
    next.forEach((value, i) => {
      if (value) query.set(SLOTS[i], value);
    });
    router.replace(query.size ? `/analytics?${query}` : "/analytics");
  }

  return (
    <div className="bench">
      <header className="page-header">
        <div>
          <h1>Analytics</h1>
          <p className="lead">Up to three runs. Each one is scored against its own benchmark, over its own dates.</p>
        </div>
        {batchIds.length >= 2 ? (
          <div className="page-actions">
            <a
              className="button"
              data-testid="export-batch"
              href={batchExportPath(batchIds)}
              download="moneylab-batch.zip"
              title="Download one zip with a folder of trades and the summary for each selected run"
            >
              Export reports
            </a>
          </div>
        ) : null}
      </header>
      <div className="analytics-picks">
        {SLOTS.map((key, index) => (
          <label key={key}>
            Run {index + 1}
            <select
              data-testid={`analytics-${key}`}
              value={slots[index]}
              onChange={(event) => choose(index, event.target.value)}
            >
              <option value="">None</option>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.name}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {!runs.length ? <p className="muted">No runs yet.</p> : null}
      {chosen.length === 2 ? <PairDiff a={chosen[0]} b={chosen[1]} /> : null}
      {chosen.length === 3 ? (
        <p className="muted">Three runs sit side by side. The first differing decision is shown for exactly two.</p>
      ) : null}
      <div className={`analytics-grid cols-${chosen.length || 1}`}>
        {chosen.map((id) => (
          <RunReport key={id} id={id} runs={runs} />
        ))}
      </div>
    </div>
  );
}

function AnalyticsPage() {
  const list = useAsync(() => api.runs(), []);
  if (list.loading) return <Loading what="Loading runs" />;
  if (list.error) return <ErrorBox error={list.error} title="Could not load runs" />;
  return <AnalyticsDesk runs={list.data ?? []} />;
}

export default function Page() {
  return (
    <Suspense fallback={<Loading what="Loading analytics" />}>
      <AnalyticsPage />
    </Suspense>
  );
}
