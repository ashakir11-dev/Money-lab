"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { ConditionEditor, defaultsOf, type CondDraft, type Value } from "@/components/ConditionEditor";
import { Mark } from "@/components/Mark";
import { Specimen } from "@/components/Specimen";
import { ErrorBox, JobStatus, Loading } from "@/components/ui";
import { api, type Component, type Condition, type Job, type Param, type RunSubmit, type StrategyConfig, waitForJob } from "@/lib/api";
import { useAsync, useDebounced } from "@/lib/hooks";
import { SECTORS, sectorSymbols } from "@/lib/sectors";

type FillKind = "next_open" | "next_range" | "dip";

type Draft = {
  name: string;
  universe: string;
  watchlist: string;
  universeSource: "custom" | "watchlist" | "sector";
  sector: string;
  benchmark: string;
  start: string;
  end: string;
  entry: CondDraft[];
  fillKind: FillKind;
  rangeBelow: string;
  rangeAbove: string;
  dipPct: string;
  maxHold: string;
  exitConds: CondDraft[];
  startingCash: string;
  targetPct: string;
  maxPositions: string;
  costBps: string;
};

const EMPTY: Draft = {
  name: "My strategy",
  universe: "",
  watchlist: "",
  universeSource: "custom",
  sector: "",
  benchmark: "SPY",
  start: "",
  end: "",
  entry: [],
  fillKind: "next_open",
  rangeBelow: "2",
  rangeAbove: "2",
  dipPct: "2",
  maxHold: "10",
  exitConds: [],
  startingCash: "",
  targetPct: "",
  maxPositions: "",
  costBps: "",
};

const toNumber = (p: Param | undefined, v: Value): unknown => {
  if (p && (p.type === "int" || p.type === "float")) {
    if (v === "" || v === null || v === undefined) return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : v;
  }
  return v;
};

function cleanParams(schema: Component | undefined, values: Record<string, Value>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(values)) {
    const p = schema?.params.find((x) => x.name === k);
    const n = toNumber(p, v);
    if (n !== undefined && n !== "") out[k] = n;
  }
  return out;
}

const splitSymbols = (s: string) =>
  s
    .split(/[\s,;]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);

function toCondition(c: CondDraft, comps: Map<string, Component>): Condition {
  return {
    kind: c.kind,
    indicator: { id: c.indicator, params: cleanParams(comps.get(c.indicator), c.indParams) },
    params: cleanParams(comps.get(c.kind), c.params),
  };
}

function toConfig(d: Draft, comps: Map<string, Component>): StrategyConfig {
  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const target = num(d.targetPct);
  return {
    name: d.name || "Untitled strategy",
    universe: splitSymbols(d.universe),
    benchmark: d.benchmark.trim().toUpperCase() || "SPY",
    start: d.start || null,
    end: d.end || null,
    entry: d.entry.map((c) => toCondition(c, comps)),
    entry_fill: {
      kind: d.fillKind,
      below_pct: d.fillKind === "next_range" && d.rangeBelow.trim() !== "" ? Number(d.rangeBelow) / 100 : null,
      above_pct: d.fillKind === "next_range" && d.rangeAbove.trim() !== "" ? Number(d.rangeAbove) / 100 : null,
      dip_pct: d.fillKind === "dip" && d.dipPct.trim() !== "" ? Number(d.dipPct) / 100 : null,
    },
    exit: (() => {
      const hold = d.maxHold.trim() === "" ? null : Number(d.maxHold);
      if (d.exitConds.length) {
        return {
          kind: "signal" as const,
          params: hold ? { max_sessions: hold } : {},
          conditions: d.exitConds.map((c) => toCondition(c, comps)),
        };
      }
      return { kind: "holding_period" as const, params: { sessions: hold ?? 10 }, conditions: [] };
    })(),
    portfolio: {
      starting_cash: num(d.startingCash),
      target_allocation: target === null ? null : target / 100,
      max_positions: num(d.maxPositions),
      cost_bps_per_side: num(d.costBps),
    },
  };
}

type ResolvedCond = { kind: CondDraft["kind"]; indicator: { id: string; params?: Record<string, Value> }; params?: Record<string, Value> };
type Resolved = {
  name?: string;
  universe?: string[];
  benchmark?: string;
  start?: string | null;
  end?: string | null;
  entry?: ResolvedCond[];
  entry_fill?: { kind?: FillKind; below_pct?: number | null; above_pct?: number | null; dip_pct?: number | null };
  exit?: { kind?: string; params?: Record<string, Value>; conditions?: ResolvedCond[] };
  watchlist?: { name?: string };
  portfolio?: { starting_cash?: number; target_allocation?: number; max_positions?: number; cost_bps_per_side?: number };
};

function fromResolved(r: Resolved): Draft {
  const cond = (c: ResolvedCond): CondDraft => ({
    kind: c.kind,
    indicator: c.indicator.id,
    indParams: { ...(c.indicator.params ?? {}) },
    params: { ...(c.params ?? {}) },
  });
  const p = r.portfolio ?? {};
  return {
    ...EMPTY,
    name: `${r.name ?? "Strategy"} (copy)`,
    universe: (r.universe ?? []).join(", "),
    benchmark: r.benchmark ?? "SPY",
    start: r.start ?? "",
    end: r.end ?? "",
    entry: (r.entry ?? []).map(cond),
    fillKind: r.entry_fill?.kind ?? "next_open",
    rangeBelow: r.entry_fill?.below_pct != null ? String(+(r.entry_fill.below_pct * 100).toFixed(6)) : "2",
    rangeAbove: r.entry_fill?.above_pct != null ? String(+(r.entry_fill.above_pct * 100).toFixed(6)) : "2",
    dipPct: r.entry_fill?.dip_pct != null ? String(+(r.entry_fill.dip_pct * 100).toFixed(6)) : "2",
    maxHold: String(r.exit?.params?.max_sessions ?? r.exit?.params?.sessions ?? ""),
    exitConds: (r.exit?.conditions ?? []).map(cond),
    universeSource: r.watchlist?.name ? "watchlist" : "custom",
    watchlist: r.watchlist?.name ?? "",
    startingCash: p.starting_cash != null ? String(p.starting_cash) : "",
    targetPct: p.target_allocation != null ? String(+(p.target_allocation * 100).toFixed(6)) : "",
    maxPositions: p.max_positions != null ? String(p.max_positions) : "",
    costBps: p.cost_bps_per_side != null ? String(p.cost_bps_per_side) : "",
  };
}

function PctSlider({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const n = Number(value);
  const slider = Number.isFinite(n) ? Math.min(20, Math.max(0, n)) : 0;
  return (
    <div className="cond-row cond-slider">
      <label className="cond-label" htmlFor={id}>
        {label}
      </label>
      <input
        type="range"
        min={0}
        max={20}
        step={0.5}
        value={slider}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
      />
      <input id={id} type="number" min={0} max={50} step="any" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function Builder() {
  const from = useSearchParams().get("from") ?? "";
  const comps = useAsync(() => api.components(), []);
  const defaults = useAsync(() => api.defaults(), []);
  const snaps = useAsync(() => api.snapshots(), []);
  const lists = useAsync(() => api.watchlists(), []);
  const source = useAsync(() => api.run(from), [from], !!from);
  const parentEq = useAsync(() => api.equity(from), [from], !!from);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [snapshot, setSnapshot] = useState("");
  const [ready, setReady] = useState(false);
  const [job, setJob] = useState<Job>();
  const [submitError, setSubmitError] = useState<unknown>();
  const doneId = job?.status === "complete" ? job.run_ids[0] : undefined;
  const newEq = useAsync(() => api.equity(doneId ?? ""), [doneId], !!doneId);

  const byId = useMemo(() => new Map((comps.data ?? []).map((c) => [c.id, c])), [comps.data]);

  // Initialise once: from the source run when duplicating, else a worked example.
  useEffect(() => {
    if (ready || !comps.data || !snaps.data) return;
    if (from) {
      const src = source.data;
      if (!src) return;
      const next = fromResolved(src.resolved as Resolved);
      const s = snaps.data.find((x) => x.id === src.snapshot_id) ?? snaps.data[0];
      if (s) {
        if (!next.start) next.start = s.first_date;
        if (!next.end) next.end = s.last_date;
      }
      setDraft(next);
      setSnapshot(src.snapshot_id);
    } else {
      const sma = byId.get("sma");
      const rsi = byId.get("rsi");
      const first = snaps.data[0];
      setDraft({
        ...EMPTY,
        universe: first ? first.symbols.filter((s) => s !== "SPY").join(", ") : "",
        start: first?.first_date ?? "",
        end: first?.last_date ?? "",
        entry: [
          ...(sma ? [{ kind: "price_cross" as const, indicator: "sma", indParams: defaultsOf(sma), params: defaultsOf(byId.get("price_cross")) }] : []),
          ...(rsi ? [{ kind: "threshold" as const, indicator: "rsi", indParams: defaultsOf(rsi), params: defaultsOf(byId.get("threshold")) }] : []),
        ],
        maxHold: String(defaults.data?.exit_sessions ?? 10),
      });
      setSnapshot(first?.id ?? "");
    }
    setReady(true);
  }, [ready, comps.data, snaps.data, source.data, from, byId, defaults.data]);

  const config = useMemo(() => (comps.data ? toConfig(draft, byId) : undefined), [draft, byId, comps.data]);
  const body: RunSubmit | undefined = config
    ? {
        config,
        snapshot_id: snapshot,
        parent_id: from || null,
        watchlist: draft.universeSource === "watchlist" ? draft.watchlist || null : null,
      }
    : undefined;
  const debounced = useDebounced(JSON.stringify(body ?? null), 400);
  const check = useAsync(
    () => api.validate(JSON.parse(debounced) as RunSubmit),
    [debounced],
    ready && debounced !== "null",
  );

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const snap = snaps.data?.find((s) => s.id === snapshot);
  const sectorAvail = snap ? sectorSymbols(draft.sector, snap.symbols) : [];

  function pickSnapshot(id: string) {
    setSnapshot(id);
    const s = snaps.data?.find((x) => x.id === id);
    if (s) setDraft((d) => ({ ...d, start: s.first_date, end: s.last_date }));
  }

  function setSource(universeSource: Draft["universeSource"]) {
    setDraft((d) => ({
      ...d,
      universeSource,
      watchlist: universeSource === "watchlist" ? d.watchlist : "",
      sector: universeSource === "sector" ? d.sector : "",
      universe: universeSource === "custom" ? d.universe : "",
    }));
  }
  const busy = job?.status === "queued" || job?.status === "running";
  const newRun = job?.status === "complete" ? job.run_ids[0] : undefined;

  async function submit() {
    if (!body) return;
    setSubmitError(undefined);
    setJob(undefined);
    try {
      const j = await api.submitRun(body);
      setJob(j);
      await waitForJob(j.id, setJob);
    } catch (e) {
      setSubmitError(e);
    }
  }

  function addCond(which: "entry" | "exitConds") {
    const ind = byId.get("rsi") ?? (comps.data ?? []).find((c) => c.uses.includes("indicator"));
    const c: CondDraft = { kind: "threshold", indicator: ind?.id ?? "", indParams: defaultsOf(ind), params: defaultsOf(byId.get("threshold")) };
    setDraft((d) => ({ ...d, [which]: [...d[which], c] }));
  }

  const loadError = comps.error || snaps.error || defaults.error || source.error;
  if (loadError) return <ErrorBox error={loadError} title="Could not load the builder" />;
  if (!ready) return <Loading what="Loading the builder" />;
  const dflt = defaults.data;

  const parentDates = (parentEq.data ?? []).map((e) => e.session);

  return (
    <div className="bench">
      {from && parentDates.length >= 2 ? (
        <Specimen
          dates={parentDates}
          series={[{ name: source.data?.name ?? "Parent", values: (parentEq.data ?? []).map((e) => e.equity), color: "var(--series-1)" }]}
          caption="Change one thing"
        />
      ) : newRun && (newEq.data?.length ?? 0) >= 2 ? (
        <Specimen
          dates={(newEq.data ?? []).map((e) => e.session)}
          series={[{ name: draft.name, values: (newEq.data ?? []).map((e) => e.equity), color: "var(--series-2)" }]}
        />
      ) : !from ? (
        <div className="bench-hero">
          <Mark size={72} />
        </div>
      ) : null}

      <div className="stack">
        <section className="stack-block">
          <h2>Strategy</h2>
          {snaps.data && snaps.data.length === 0 ? (
            <p className="notice">
              No price data yet. Import some on the <Link href="/data">Data</Link> page first.
            </p>
          ) : null}
          <div className="fields">
            <div className="field" style={{ gridColumn: "span 2" }}>
              <label htmlFor="snap">Price snapshot</label>
              <select id="snap" data-testid="builder-snapshot" value={snapshot} onChange={(e) => pickSnapshot(e.target.value)}>
                {(snaps.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.source_label} · {s.first_date} to {s.last_date}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="name">Name</label>
              <input id="name" value={draft.name} onChange={(e) => set("name", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="bench">Benchmark</label>
              <input id="bench" value={draft.benchmark} onChange={(e) => set("benchmark", e.target.value)} />
            </div>
          </div>
          <label>Universe</label>
          <div className="source-row">
            {(["watchlist", "custom", "sector"] as const).map((s) => (
              <button key={s} className={draft.universeSource === s ? "on" : undefined} onClick={() => setSource(s)}>
                {s === "custom" ? "Custom" : s === "watchlist" ? "Watchlist" : "Sector"}
              </button>
            ))}
          </div>
          {draft.universeSource === "watchlist" ? (
            <div className="field">
              <label htmlFor="wl">Watchlist</label>
              <select
                id="wl"
                value={draft.watchlist}
                onChange={(e) => {
                  const name = e.target.value;
                  const w = lists.data?.find((x) => x.name === name);
                  setDraft((d) => ({ ...d, watchlist: name, universe: w ? w.members.join(", ") : d.universe }));
                }}
              >
                <option value="">Choose…</option>
                {(lists.data ?? []).map((w) => (
                  <option key={w.name} value={w.name}>
                    {w.name} v{w.version} ({w.members.length})
                  </option>
                ))}
              </select>
            </div>
          ) : draft.universeSource === "sector" ? (
            <div className="field">
              <label htmlFor="sector">Sector</label>
              <select
                id="sector"
                value={draft.sector}
                onChange={(e) => {
                  const id = e.target.value;
                  setDraft((d) => ({
                    ...d,
                    sector: id,
                    universe: snap ? sectorSymbols(id, snap.symbols).join(", ") : "",
                  }));
                }}
              >
                <option value="">Choose…</option>
                {SECTORS.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
              {draft.sector && snap && sectorAvail.length === 0 ? (
                <div className="help">
                  None of those names are in this snapshot. Import them on <Link href="/data">Data</Link>.
                </div>
              ) : draft.sector ? (
                <div className="help">{sectorAvail.join(", ") || "None"}</div>
              ) : null}
            </div>
          ) : (
            <div className="field">
              <label htmlFor="universe">Symbols</label>
              <input
                id="universe"
                data-testid="builder-universe"
                value={draft.universe}
                onChange={(e) => set("universe", e.target.value)}
                placeholder="AAPL, MSFT, NVDA"
              />
              {snap ? <div className="help">In this snapshot: {snap.symbols.join(", ")}</div> : null}
            </div>
          )}
          {draft.universeSource !== "custom" ? (
            <input type="hidden" data-testid="builder-universe" value={draft.universe} readOnly />
          ) : null}
          <div className="date-range">
            <div className="field">
              <label htmlFor="start">From</label>
              <input id="start" type="date" value={draft.start} onChange={(e) => set("start", e.target.value)} min={snap?.first_date} max={snap?.last_date} />
            </div>
            <div className="field">
              <label htmlFor="end">To</label>
              <input id="end" type="date" value={draft.end} onChange={(e) => set("end", e.target.value)} min={snap?.first_date} max={snap?.last_date} />
            </div>
          </div>
        </section>

        <section className="stack-block">
          <div className="section-head">
            <h2>Strategy config</h2>
            <button data-testid="add-condition" onClick={() => addCond("entry")}>
              Add condition
            </button>
          </div>
          <p className="caption">Signals at the close. These rules decide whether a name is a candidate that session.</p>
          {draft.entry.length === 0 ? <p className="muted">Add at least one rule.</p> : null}
          {draft.entry.map((c, i) => (
            <ConditionEditor
              key={i}
              index={i}
              prefix="condition"
              cond={c}
              comps={comps.data ?? []}
              onChange={(n) => setDraft((d) => ({ ...d, entry: d.entry.map((x, j) => (j === i ? n : x)) }))}
              onRemove={() => setDraft((d) => ({ ...d, entry: d.entry.filter((_, j) => j !== i) }))}
            />
          ))}
        </section>

        <section className="stack-block">
          <h2>Entry</h2>
          <p className="caption">When a rule passes, how the buy is placed.</p>
          <div className="source-row" role="group" aria-label="Entry fill">
            {(
              [
                ["next_open", "Next open"],
                ["next_range", "Next candle in range"],
                ["dip", "Dip to a level"],
              ] as const
            ).map(([id, label]) => (
              <button key={id} type="button" className={draft.fillKind === id ? "on" : undefined} onClick={() => set("fillKind", id)}>
                {label}
              </button>
            ))}
          </div>
          {draft.fillKind === "next_open" ? (
            <p className="help">Fill at the next session open. The default, and what the demo path uses.</p>
          ) : draft.fillKind === "next_range" ? (
            <>
              <p className="help">Fill on the next bar only if it trades inside this band around the signal close. A gap outside cancels the order.</p>
              <PctSlider id="range-below" label="Below %" value={draft.rangeBelow} onChange={(v) => set("rangeBelow", v)} />
              <PctSlider id="range-above" label="Above %" value={draft.rangeAbove} onChange={(v) => set("rangeAbove", v)} />
            </>
          ) : (
            <>
              <p className="help">Limit below the signal close. Waits until that session’s low prints through; a gap through fills at the open.</p>
              <PctSlider id="dip-pct" label="Dip %" value={draft.dipPct} onChange={(v) => set("dipPct", v)} />
            </>
          )}
        </section>

        <section className="stack-block">
          <div className="section-head">
            <h2>Exits</h2>
            <button onClick={() => addCond("exitConds")}>Add condition</button>
          </div>
          <p className="caption">Optional. Without an exit signal, the max hold below (or the default) closes the position.</p>
          {draft.exitConds.map((c, i) => (
            <ConditionEditor
              key={i}
              index={i}
              prefix="exit-condition"
              cond={c}
              comps={comps.data ?? []}
              onChange={(n) => setDraft((d) => ({ ...d, exitConds: d.exitConds.map((x, j) => (j === i ? n : x)) }))}
              onRemove={() => setDraft((d) => ({ ...d, exitConds: d.exitConds.filter((_, j) => j !== i) }))}
            />
          ))}
        </section>

        <section className="stack-block">
          <h2>Position size</h2>
          <div className="field">
            <label htmlFor="target">Percent of equity per position</label>
            <input id="target" type="number" value={draft.targetPct} placeholder={dflt ? String(dflt.target_allocation * 100) : ""} onChange={(e) => set("targetPct", e.target.value)} />
          </div>
        </section>

        <section className="stack-block">
          <h2>Amount of positions</h2>
          <div className="field">
            <label htmlFor="maxpos">Max positions at once</label>
            <input id="maxpos" type="number" value={draft.maxPositions} placeholder={dflt ? String(dflt.max_positions) : ""} onChange={(e) => set("maxPositions", e.target.value)} />
          </div>
        </section>

        <section className="stack-block">
          <h2>Capital</h2>
          <div className="fields">
            <div className="field">
              <label htmlFor="cash">Starting cash ($)</label>
              <input id="cash" type="number" value={draft.startingCash} placeholder={dflt ? String(dflt.starting_cash) : ""} onChange={(e) => set("startingCash", e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="bps">Cost per side (bps)</label>
              <input id="bps" type="number" value={draft.costBps} placeholder={dflt ? String(dflt.cost_bps_per_side) : ""} onChange={(e) => set("costBps", e.target.value)} />
            </div>
          </div>
        </section>

        <section className="stack-block">
          <h2>Maximum hold time</h2>
          <div className="field">
            <label htmlFor="sessions">Sessions (optional)</label>
            <input
              id="sessions"
              data-testid="exit-sessions"
              type="number"
              min={1}
              value={draft.maxHold}
              onChange={(e) => set("maxHold", e.target.value)}
              placeholder="none"
            />
            <div className="help">Fill day counts as session one. With exit signals, this is a cap: signal or this many sessions, whichever first.</div>
          </div>
        </section>

        <section className="stack-block">
          {check.error ? (
            <ErrorBox error={check.error} title="Not valid yet" />
          ) : check.data ? (
            <div className="summary-box" data-testid="builder-summary">
              <p className="plain-summary">{check.data.summary}</p>
            </div>
          ) : null}
          <div className="row" style={{ marginTop: 12 }}>
            <button
              className="primary"
              data-testid="run-backtest"
              onClick={submit}
              disabled={busy || !snapshot || !!check.error || !draft.entry.length}
            >
              Run backtest
            </button>
            <JobStatus status={job?.status} error={job?.error} />
            {newRun && from ? (
              <Link
                className="button primary"
                data-testid="compare-with-original"
                href={`/compare?a=${encodeURIComponent(from)}&b=${encodeURIComponent(newRun)}`}
              >
                Compare with the original run
              </Link>
            ) : null}
            {newRun ? (
              <Link className="button" href={`/report?id=${encodeURIComponent(newRun)}`} data-testid="open-new-run">
                Open the report
              </Link>
            ) : null}
          </div>
          <ErrorBox error={submitError} title="Could not start the run" />
        </section>
      </div>
    </div>
  );
}

export default function BuilderPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Builder />
    </Suspense>
  );
}
