"use client";

import Link from "next/link";
import { useMemo } from "react";
import { DEMO_FORK, Spark, Specimen } from "@/components/Specimen";
import { Mark, RailIcon } from "@/components/Mark";
import { api, type EquityRow, type FirstDifference, type RunSummary } from "@/lib/api";
import { pct } from "@/lib/format";
import { useAsync, useInterval } from "@/lib/hooks";

const RAIL = [
  { href: "/data", label: "Data", icon: "data" as const },
  { href: "/builder", label: "Builder", icon: "builder" as const },
  { href: "/report", label: "Reports", icon: "reports" as const },
  { href: "/compare", label: "Compare", icon: "compare" as const },
];

function align(rows: EquityRow[], dates: string[], key: "equity" | "benchmark_equity"): (number | null)[] {
  const by = new Map(rows.map((r) => [r.session, r]));
  return dates.map((d) => {
    const v = by.get(d)?.[key];
    return typeof v === "number" ? v : null;
  });
}

export default function HomePage() {
  const runs = useAsync(() => api.runs(), []);
  const snaps = useAsync(() => api.snapshots(), []);
  const jobs = useAsync(() => api.jobs(), []);
  const active = (jobs.data ?? []).filter((j) => j.status === "queued" || j.status === "running");

  useInterval(
    () => {
      jobs.reload();
      runs.reload();
    },
    1000,
    active.length > 0,
  );

  const list = runs.data ?? [];
  const newest = list[0] as RunSummary | undefined;
  const strip = list.slice(0, 6);
  const hasData = (snaps.data ?? []).length > 0;
  const live = list.length > 0;

  const specimen = useAsync(async () => {
    for (const run of list) {
      const self = await api.equity(run.id);
      if (self.length < 2) continue;
      if (run.parent_id) {
        try {
          const [parent, cmp] = await Promise.all([api.equity(run.parent_id), api.compare(run.parent_id, run.id)]);
          const dates = Array.from(new Set([...parent.map((e) => e.session), ...self.map((e) => e.session)])).sort();
          if (dates.length < 2) continue;
          const fd = cmp.first_difference as FirstDifference | null;
          return {
            dates,
            a: align(parent, dates, "equity"),
            b: align(self, dates, "equity"),
            aName: list.find((r) => r.id === run.parent_id)?.name ?? "Parent",
            bName: run.name,
            pin: fd?.session ?? null,
            pinHref: `/compare?a=${encodeURIComponent(run.parent_id)}&b=${encodeURIComponent(run.id)}`,
            wellHref: `/report?id=${encodeURIComponent(run.id)}`,
          };
        } catch {
          /* Fall through to vs-benchmark overlay. */
        }
      }
      const dates = self.map((e) => e.session);
      return {
        dates,
        a: self.map((e) => e.equity),
        b: self.map((e) => e.benchmark_equity ?? null),
        aName: run.name,
        bName: "SPY",
        pin: null as string | null,
        pinHref: undefined as string | undefined,
        wellHref: `/report?id=${encodeURIComponent(run.id)}`,
      };
    }
    return null;
  }, [list.map((r) => r.id).join("|")]);

  const sparks = useAsync(async () => {
    if (!strip.length) return {} as Record<string, number[]>;
    const eqs = await Promise.all(strip.map((r) => api.equity(r.id).catch(() => [] as EquityRow[])));
    return Object.fromEntries(strip.map((r, i) => [r.id, eqs[i].map((e) => e.equity)]));
  }, [strip.map((r) => r.id).join("|")]);

  const demoSeries = useMemo(
    () => [
      { name: DEMO_FORK.aName, values: DEMO_FORK.a, color: "var(--series-1)" },
      { name: DEMO_FORK.bName, values: DEMO_FORK.b, color: "var(--series-2)" },
    ],
    [],
  );

  const ready = !runs.loading && !snaps.loading;
  const demo = (
    <Specimen dates={DEMO_FORK.dates} series={demoSeries} pinDate={DEMO_FORK.pin} caption="Demo fork · AAA 2023-04-04" />
  );

  return (
    <div className="bench">
      {!live && ready ? (
        <div className="bench-hero">
          <Mark size={120} draw />
        </div>
      ) : null}

      {specimen.data ? (
        <Specimen
          dates={specimen.data.dates}
          series={[
            { name: specimen.data.aName, values: specimen.data.a, color: "var(--series-1)" },
            {
              name: specimen.data.bName,
              values: specimen.data.b,
              color: specimen.data.pin ? "var(--series-2)" : "var(--series-bench)",
              dashed: !specimen.data.pin,
            },
          ]}
          pinDate={specimen.data.pin}
          pinHref={specimen.data.pinHref}
          wellHref={specimen.data.wellHref}
          caption={specimen.data.bName}
        />
      ) : runs.loading || specimen.loading ? (
        <div className="specimen-well specimen-wait" />
      ) : (
        demo
      )}

      {!live && ready ? (
        <p className="bench-cta">
          <Link href={hasData ? "/builder" : "/data"} className="button primary">
            {hasData ? "Open Builder" : "Import data"}
          </Link>
        </p>
      ) : live ? (
        <>
          <nav className="rail" aria-label="Instruments">
            {RAIL.map((slot) => (
              <Link key={slot.href} href={slot.href} className="rail-slot">
                <RailIcon name={slot.icon} />
                <span>{slot.label}</span>
              </Link>
            ))}
            {active.length ? <span className="lamp lamp-busy rail-lamp" title={active.map((j) => j.kind).join(", ")} /> : null}
          </nav>

          {strip.length ? (
            <section className="filmstrip" aria-label="Recent experiments">
              {strip.map((r) => (
                <Link
                  key={r.id}
                  href={`/report?id=${encodeURIComponent(r.id)}`}
                  className="film-well"
                  data-testid="run-row"
                  data-run-id={r.id}
                >
                  <Spark values={sparks.data?.[r.id] ?? []} color={r.id === newest?.id ? "var(--series-2)" : "var(--series-1)"} />
                  <strong>{r.name}</strong>
                  <span className={`num ${(r.total_return ?? 0) >= 0 ? "pos" : "neg"}`}>{pct(r.total_return, true)}</span>
                </Link>
              ))}
              {(runs.data ?? []).length > strip.length ? (
                <Link href="/report" className="film-more muted">
                  All experiments
                </Link>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
