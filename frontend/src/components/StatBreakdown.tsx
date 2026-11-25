import type { ReactNode } from "react";

export type StatItem = {
  label: string;
  value: ReactNode;
  meaning: string;
  tone?: "pos" | "neg";
};

/** One result per statistic, with a short explanation of what the number is. */
export function StatBreakdown({ stats }: { stats: StatItem[] }) {
  return (
    <dl className="stat-grid">
      {stats.map((stat) => (
        <div className="stat" key={stat.label}>
          <div className="stat-top">
            <dt>{stat.label}</dt>
            <dd className={stat.tone ? `num ${stat.tone}` : "num"}>{stat.value}</dd>
          </div>
          <p>{stat.meaning}</p>
        </div>
      ))}
    </dl>
  );
}
