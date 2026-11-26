"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import type { RunSummary } from "@/lib/api";
import { pct, versusSpy } from "@/lib/format";
import { families } from "@/lib/lineage";

function RunCells({ run }: { run: RunSummary }) {
  const href = `/report?id=${encodeURIComponent(run.id)}`;
  return (
    <>
      <td>
        <Link href={href}>{run.name}</Link>
      </td>
      <td className="num">
        <Link href={href}>{pct(run.total_return, true)}</Link>
      </td>
      <td>
        <Link href={href}>{versusSpy(run.total_return, run.benchmark_return, run.benchmark_symbol || "SPY")}</Link>
      </td>
      <td className="num">
        <Link href={href}>{pct(run.max_drawdown)}</Link>
      </td>
    </>
  );
}

export function RunTracker({ runs }: { runs: RunSummary[] }) {
  const groups = useMemo(() => families(runs), [runs]);
  const firstOpen = groups[0] && groups[0].runs.length > 1 ? groups[0].rootId : null;
  const [open, setOpen] = useState<string | null>(firstOpen);

  if (!groups.length) return null;

  return (
    <section className="tracker" data-testid="run-tracker">
      <h2>Tracker</h2>
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Return</th>
            <th>Versus benchmark</th>
            <th>Drawdown</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => {
            const root = group.runs.find((run) => run.id === group.rootId) ?? group.runs[0];
            const several = group.runs.length > 1;
            const expanded = open === group.rootId;
            return (
              <Fragment key={group.rootId}>
                {several ? (
                  <tr>
                    <td colSpan={4}>
                      <button
                        type="button"
                        className="tracker-parent"
                        aria-expanded={expanded}
                        onClick={() => setOpen(expanded ? null : group.rootId)}
                      >
                        {root.name} <span className="tracker-count">{group.runs.length} versions</span>
                      </button>
                    </td>
                  </tr>
                ) : (
                  <tr>
                    <RunCells run={root} />
                  </tr>
                )}
                {several && expanded
                  ? group.runs.map((run) => (
                      <tr key={run.id} className="tracker-kid">
                        <RunCells run={run} />
                      </tr>
                    ))
                  : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
