"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, waitForJob, type DecisionRow, type Job, type RunMetrics } from "@/lib/api";
import { int, money, pct, pp, sharpe, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { EquityChart } from "@/components/EquityChart";
import { DecisionsTable, FillsTable } from "@/components/tables";
import { Badge, ErrorBox, JobStatus, Loading, PageHeader, Section, Tile } from "@/components/ui";

const PAGE = 50;
const ACTIONS = [
  { value: "", label: "Trading decisions (buy, sell, skip)" },
  { value: "enter", label: "Buy orders" },
  { value: "exit", label: "Sell orders" },
  { value: "skip", label: "Skips" },
  { value: "no_signal", label: "Near misses (some conditions passed)" },
  { value: "ineligible", label: "Not eligible (warm-up, missing bar)" },
  { value: "all", label: "Everything" },
];

type Watchlist = { name?: string; version?: number; created_at?: string };

function RunView() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const run = useAsync(() => api.run(id), [id], !!id);
  const equity = useAsync(() => api.equity(id), [id], !!id);
  const fills = useAsync(() => api.fills(id), [id], !!id);
  const parent = useAsync(() => api.run(run.data?.parent_id ?? ""), [run.data?.parent_id], !!run.data?.parent_id);
  const [action, setAction] = useState("");
  const [symbol, setSymbol] = useState("");
  const [offset, setOffset] = useState(0);
  const actionFilter = action === "all" ? undefined : action ? [action] : ["enter", "exit", "skip"];
  const decisions = useAsync(
    () => api.decisions(id, { offset, limit: PAGE, action: actionFilter, symbol: symbol || undefined }),
    [id, action, symbol, offset],
    !!id,
  );
  const [job, setJob] = useState<Job>();
  const [rerunError, setRerunError] = useState<unknown>();

  if (!id) return <p className="muted">No run chosen. Pick one on the <Link href="/">Runs</Link> page.</p>;
  if (run.error) return <ErrorBox error={run.error} title="Could not load this run" />;
  if (!run.data) return <Loading what="Loading run" />;

  const r = run.data;
  const m = r.metrics as RunMetrics;
  const b = m.benchmark ?? {};
  const resolved = r.resolved as { benchmark?: string; watchlist?: Watchlist };
  const wl = resolved.watchlist;
  const benchName = resolved.benchmark ?? "Benchmark";
  const eq = equity.data ?? [];

  async function rerun() {
    setRerunError(undefined);
    try {
      const j = await api.duplicate(id);
      setJob(j);
      const done = await waitForJob(j.id, setJob);
      if (done.status === "complete" && done.run_ids[0]) router.push(`/run?id=${encodeURIComponent(done.run_ids[0])}`);
    } catch (e) {
      setRerunError(e);
    }
  }

  return (
    <>
      <PageHeader title={r.name} lead={r.summary}>
        <Link href={`/builder?from=${encodeURIComponent(id)}`} className="button primary">
          Duplicate and edit
        </Link>
        {r.parent_id ? (
          <Link href={`/compare?a=${encodeURIComponent(r.parent_id)}&b=${encodeURIComponent(id)}`} className="button">
            Compare with parent
          </Link>
        ) : null}
        <button onClick={rerun} disabled={job?.status === "queued" || job?.status === "running"}>
          Re-run exactly
        </button>
        <JobStatus status={job?.status} error={job?.error} />
      </PageHeader>
      <ErrorBox error={rerunError} title="Could not re-run" />

      <p className="caption">
        <Badge kind={r.source_kind}>{r.source_label}</Badge> · run <code>{r.id}</code> · {r.engine_version} · created {when(r.created_at)}
        {r.parent_id ? (
          <>
            {" "}
            · copy of <Link href={`/run?id=${encodeURIComponent(r.parent_id)}`}>{parent.data?.name ?? r.parent_id}</Link>
          </>
        ) : null}
      </p>

      <div className="tiles" style={{ margin: "16px 0" }}>
        <Tile label="Return" value={pct(m.total_return)} sub={`${pp(m.total_return, b.total_return)} vs ${benchName}`} />
        <Tile label={`${benchName} return`} value={pct(b.total_return)} />
        <Tile label="Max drawdown" value={pct(m.max_drawdown)} />
        <Tile label="Sharpe" value={sharpe(m.sharpe)} title={m.sharpe_assumptions} />
        <Tile label="Round trips" value={int(m.trades)} sub={`${int(m.open_positions ?? 0)} still open at the end`} />
        <Tile label="Costs" value={money(m.costs)} />
        <Tile label="Final equity" value={money(m.final_equity)} />
      </div>

      <p className="caption">
        Open positions at the end: {int(m.open_positions ?? 0)}
        {b.invested_session ? (
          <>
            {" "}
            · {benchName} bought {int(b.shares)} shares at {money(b.price)} on {b.invested_session} (cost {money(b.cost)}, cash left{" "}
            {money(b.cash_left)})
          </>
        ) : null}
        {wl?.created_at ? (
          <>
            {" "}
            · Watchlist {wl.name} v{wl.version} selected {wl.created_at.slice(0, 10)}: a list chosen today describes past performance of
            today&apos;s picks
          </>
        ) : null}
      </p>

      <Section title={`Account value vs ${benchName}`}>
        <ErrorBox error={equity.error} />
        {equity.loading && !equity.data ? <Loading /> : null}
        {eq.length ? (
          <EquityChart
            dates={eq.map((e) => e.session)}
            series={[
              { name: r.name, values: eq.map((e) => e.equity), color: "var(--series-1)" },
              { name: benchName, values: eq.map((e) => e.benchmark_equity), color: "var(--series-bench)", dashed: true },
            ]}
          />
        ) : null}
      </Section>

      <Section title="Trades">
        <ErrorBox error={fills.error} />
        {fills.data ? <FillsTable fills={fills.data} /> : <Loading />}
      </Section>

      <Section
        title="Decisions"
        aside={
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
        }
      >
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
      </Section>

      <Section title="Settings (exactly as run, including defaults)">
        <pre className="json">{JSON.stringify(r.resolved, null, 2)}</pre>
      </Section>
    </>
  );
}

export default function RunPage() {
  return (
    <Suspense fallback={<Loading />}>
      <RunView />
    </Suspense>
  );
}
