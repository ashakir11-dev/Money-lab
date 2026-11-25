"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { int, pct, pp, sharpe, when } from "@/lib/format";
import { useAsync, useInterval } from "@/lib/hooks";
import { Badge, ErrorBox, JobStatus, Loading, PageHeader, Section } from "@/components/ui";

export default function RunsPage() {
  const router = useRouter();
  const runs = useAsync(() => api.runs(), []);
  const jobs = useAsync(() => api.jobs(), []);
  const active = (jobs.data ?? []).filter((j) => j.status === "queued" || j.status === "running");
  const recentFailures = (jobs.data ?? []).filter((j) => j.status === "failed").slice(0, 3);

  useInterval(
    () => {
      jobs.reload();
      runs.reload();
    },
    1000,
    active.length > 0,
  );

  const names = new Map((runs.data ?? []).map((r) => [r.id, r.name]));

  return (
    <>
      <PageHeader
        title="Runs"
        lead="Every backtest you have run, newest first. Open one to see its trades and decisions, copy it to try a change, or compare it with the run it came from."
      >
        <Link href="/builder" className="button primary">
          New strategy
        </Link>
        <Link href="/compare" className="button">
          Compare runs
        </Link>
      </PageHeader>

      {active.length || recentFailures.length ? (
        <Section title="In progress">
          <ul className="plain-list">
            {active.map((j) => (
              <li key={j.id}>
                <JobStatus status={j.status} /> <span className="muted small">{j.kind} submitted {when(j.submitted_at)}</span>
              </li>
            ))}
            {recentFailures.map((j) => (
              <li key={j.id}>
                <JobStatus status={j.status} error={j.error} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Saved runs" aside={<button onClick={runs.reload}>Refresh</button>}>
        <ErrorBox error={runs.error} title="Could not load runs" />
        {runs.loading && !runs.data ? <Loading what="Loading runs" /> : null}
        {runs.data && runs.data.length === 0 ? (
          <p className="muted">
            No runs yet. Import prices on the <Link href="/data">Data</Link> page, then build a strategy in the{" "}
            <Link href="/builder">Builder</Link>.
          </p>
        ) : null}
        {runs.data && runs.data.length ? (
          <div className="table-wrap">
            <table data-testid="runs-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Data</th>
                  <th className="num">Return</th>
                  <th className="num">vs benchmark</th>
                  <th className="num">Max drawdown</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Round trips</th>
                  <th>Copy of</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((r) => (
                  <tr
                    key={r.id}
                    className="clickable"
                    data-testid="run-row"
                    data-run-id={r.id}
                    onClick={() => router.push(`/run?id=${encodeURIComponent(r.id)}`)}
                  >
                    <td>
                      <Link href={`/run?id=${encodeURIComponent(r.id)}`} onClick={(e) => e.stopPropagation()}>
                        {r.name}
                      </Link>
                      <div className="muted small">{r.summary}</div>
                    </td>
                    <td>
                      <Badge kind={r.source_kind}>{r.source_label}</Badge>
                    </td>
                    <td className={`num ${(r.total_return ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(r.total_return)}</td>
                    <td className="num">{pp(r.total_return, r.benchmark_return)}</td>
                    <td className="num">{pct(r.max_drawdown)}</td>
                    <td className="num">{sharpe(r.sharpe)}</td>
                    <td className="num">
                      {int(r.trades)}
                      {r.open_positions ? <span className="muted small"> (+{r.open_positions} open)</span> : null}
                    </td>
                    <td>{r.parent_id ? names.get(r.parent_id) ?? r.parent_id : ""}</td>
                    <td className="small">{when(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Section>
    </>
  );
}
