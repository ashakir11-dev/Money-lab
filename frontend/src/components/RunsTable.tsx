"use client";

import Link from "next/link";
import { LowSample, signed } from "@/components/Analysis";
import type { RunSummary } from "@/lib/api";
import { int, num, pct, pp } from "@/lib/format";

const tone = (v: number | null | undefined) => (typeof v === "number" ? (v >= 0 ? "pos" : "neg") : "");

/** Every saved run with its headline numbers, newest first. */
export function RunsTable({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="table-wrap" data-testid="runs-table">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th className="num">Return</th>
            <th className="num">Vs SPY</th>
            <th className="num">Per year</th>
            <th className="num">Win rate</th>
            <th className="num">Profit factor</th>
            <th className="num">Edge / trade</th>
            <th className="num">Edge t</th>
            <th className="num">Trades</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} data-run-id={r.id}>
              <td>
                <Link href={`/report?id=${encodeURIComponent(r.id)}`}>{r.name}</Link>
                <span className="muted small"> …{r.id.slice(-6)}</span>
              </td>
              <td className={`num ${tone(r.total_return)}`}>{pct(r.total_return, true)}</td>
              <td className={`num ${tone(typeof r.total_return === "number" && typeof r.benchmark_return === "number" ? r.total_return - r.benchmark_return : null)}`}>
                {pp(r.total_return, r.benchmark_return)}
              </td>
              <td className={`num ${tone(r.cagr)}`}>{pct(r.cagr, true)}</td>
              <td className="num">{pct(r.win_rate)}</td>
              <td className="num">{r.profit_factor == null ? "–" : num(r.profit_factor)}</td>
              <td className={`num ${tone(r.edge_mean)}`}>{pct(r.edge_mean, true)}</td>
              <td className="num">{signed(r.edge_t)}</td>
              <td className="num">
                {int(r.trades)} <LowSample trades={r.low_sample ? (r.trades ?? 0) : null} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
