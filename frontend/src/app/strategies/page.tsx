"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type ReactNode } from "react";
import { ErrorBox, Loading } from "@/components/ui";
import { api, type StrategyFamily, type StrategyVersion } from "@/lib/api";
import { pct, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

function gapText(gap: number | null | undefined, symbol: string): string {
  if (typeof gap !== "number") return `No ${symbol} series`;
  const points = gap * 100;
  const mag = `${Math.abs(points).toFixed(2)} pp`;
  if (Math.abs(points) < 0.005) return `Matched ${symbol}`;
  return points > 0 ? `Beat ${symbol} by ${mag}` : `Trailed ${symbol} by ${mag}`;
}

function symbolFor(family: StrategyFamily, runId: string | null): string {
  for (const version of family.versions) {
    const run = version.runs.find((item) => item.id === runId);
    if (run?.benchmark_symbol) return run.benchmark_symbol;
  }
  return "S&P";
}

function VersionNodes({
  versions,
  selected,
  onSelect,
}: {
  versions: StrategyVersion[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  const byParent = useMemo(() => {
    const known = new Set(versions.map((version) => version.id));
    const groups = new Map<string | null, StrategyVersion[]>();
    for (const version of versions) {
      const parent = version.parent_id && known.has(version.parent_id) ? version.parent_id : null;
      groups.set(parent, [...(groups.get(parent) ?? []), version]);
    }
    return groups;
  }, [versions]);

  function branch(parent: string | null): ReactNode {
    const rows = byParent.get(parent) ?? [];
    if (!rows.length) return null;
    return (
      <ul className="version-tree">
        {rows.map((version) => (
          <li key={version.id}>
            <button
              type="button"
              className={version.id === selected ? "version-node on" : "version-node"}
              onClick={() => onSelect(version.id)}
            >
              <strong>{version.name}</strong>
              <span>{version.run_count === 1 ? "1 run" : `${version.run_count} runs`}</span>
              <span className={typeof version.best_gap === "number" && version.best_gap >= 0 ? "pos" : "neg"}>
                {gapText(version.best_gap, "its benchmark")}
              </span>
            </button>
            {branch(version.id)}
          </li>
        ))}
      </ul>
    );
  }

  return branch(null);
}

function StrategyDesk({ families }: { families: StrategyFamily[] }) {
  const params = useSearchParams();
  const router = useRouter();
  const requested = params.get("id") ?? "";
  const family = families.find((item) => item.id === requested || item.versions.some((version) => version.id === requested)) ?? families[0];
  const [picked, setPicked] = useState(requested);
  const selectedId =
    family && family.versions.some((version) => version.id === picked)
      ? picked
      : family?.versions.find((version) => version.id === requested)?.id ?? family?.id ?? "";
  const version = family?.versions.find((item) => item.id === selectedId);

  function openFamily(id: string) {
    setPicked(id);
    router.replace(`/strategies?id=${encodeURIComponent(id)}`);
  }

  return (
    <div className="bench">
      <header className="page-header">
        <div>
          <h1>Strategies</h1>
          <p className="lead">Versions keep their rules. A run chooses the universe, dates, and data.</p>
        </div>
        <div className="page-actions">
          <Link href="/builder" className="button primary" data-testid="create-strategy">
            Create strategy
          </Link>
        </div>
      </header>

      {!families.length ? (
        <p className="muted">
          No strategies yet. <Link href="/builder">Create one</Link> and save it, or run a backtest.
        </p>
      ) : (
        <div className="strategy-layout">
          <ol className="strategy-rank" data-testid="strategy-rank">
            {families.map((item, index) => {
              const symbol = symbolFor(item, item.best_run_id);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    className={item.id === family?.id ? "on" : undefined}
                    onClick={() => openFamily(item.id)}
                  >
                    <span className="rank-index">{index + 1}</span>
                    <span>
                      <strong>{item.name}</strong>
                      <span className="muted small">
                        {item.version_count} versions · {item.run_count} runs
                      </span>
                    </span>
                    <span className={typeof item.best_gap === "number" && item.best_gap >= 0 ? "pos" : "neg"}>
                      {gapText(item.best_gap, symbol)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>

          {family && version ? (
            <section className="strategy-detail" data-testid="strategy-detail">
              <VersionNodes versions={family.versions} selected={version.id} onSelect={setPicked} />
              <div className="version-panel">
                <header className="page-header">
                  <div>
                    <h2>{version.name}</h2>
                    <p className="lead">
                      {gapText(version.best_gap, symbolFor(family, version.best_run_id))}
                      {version.best_run_id ? (
                        <>
                          {" "}
                          ·{" "}
                          <Link href={`/analytics?a=${encodeURIComponent(version.best_run_id)}`}>Best run</Link>
                        </>
                      ) : (
                        " · no runs yet"
                      )}
                    </p>
                  </div>
                  <div className="page-actions">
                    <Link className="button primary" href={`/builder?strategy=${encodeURIComponent(version.id)}`}>
                      Edit
                    </Link>
                  </div>
                </header>
                <p className="muted small">
                  Saved {when(version.created_at)}. Editing rules, sizing, or costs saves a child. Running with the
                  same rules adds a run to this version.
                </p>
                <h3>Runs</h3>
                {version.runs.length ? (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Run</th>
                          <th>Window</th>
                          <th>Universe</th>
                          <th>Versus its benchmark</th>
                        </tr>
                      </thead>
                      <tbody>
                        {version.runs.map((run) => (
                          <tr key={run.id} className={run.id === version.best_run_id ? "best-run" : undefined}>
                            <td>
                              <Link href={`/analytics?a=${encodeURIComponent(run.id)}`}>{run.name}</Link>
                            </td>
                            <td>
                              {run.start ?? "—"} → {run.end ?? "—"}
                            </td>
                            <td>{run.universe.join(", ") || "—"}</td>
                            <td>
                              {gapText(run.gap, run.benchmark_symbol || "S&P")}
                              {typeof run.total_return === "number" ? ` · ${pct(run.total_return, true)}` : ""}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted">This version has no runs. Edit it and press Run backtest.</p>
                )}
              </div>
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}

function StrategiesPage() {
  const families = useAsync(() => api.strategies(), []);
  if (families.loading) return <Loading what="Loading strategies" />;
  if (families.error) return <ErrorBox error={families.error} title="Could not load strategies" />;
  return <StrategyDesk families={families.data ?? []} />;
}

export default function Page() {
  return (
    <Suspense fallback={<Loading what="Loading strategies" />}>
      <StrategiesPage />
    </Suspense>
  );
}
