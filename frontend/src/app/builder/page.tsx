"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { api, type Component, type Condition, type Job, type Param, type RunSubmit, type StrategyConfig, waitForJob } from "@/lib/api";
import { useAsync, useDebounced } from "@/lib/hooks";
import { ErrorBox, JobStatus, Loading, PageHeader, Section } from "@/components/ui";

type Value = string | number;
type CondDraft = {
  kind: "price_cross" | "threshold";
  indicator: string;
  indParams: Record<string, Value>;
  params: Record<string, Value>;
};
type Draft = {
  name: string;
  universe: string;
  watchlist: string;
  benchmark: string;
  start: string;
  end: string;
  entry: CondDraft[];
  exitKind: "holding_period" | "signal";
  exitSessions: Value;
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
  benchmark: "SPY",
  start: "",
  end: "",
  entry: [],
  exitKind: "holding_period",
  exitSessions: 10,
  exitConds: [],
  startingCash: "",
  targetPct: "",
  maxPositions: "",
  costBps: "",
};

const defaultsOf = (c: Component | undefined): Record<string, Value> =>
  Object.fromEntries((c?.params ?? []).map((p) => [p.name, (p.default as Value) ?? ""]));

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
    exit:
      d.exitKind === "holding_period"
        ? { kind: "holding_period", params: { sessions: Number(d.exitSessions) }, conditions: [] }
        : { kind: "signal", params: {}, conditions: d.exitConds.map((c) => toCondition(c, comps)) },
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
  exit?: { kind?: Draft["exitKind"]; params?: Record<string, Value>; conditions?: ResolvedCond[] };
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
    exitKind: r.exit?.kind ?? "holding_period",
    exitSessions: r.exit?.params?.sessions ?? 10,
    exitConds: (r.exit?.conditions ?? []).map(cond),
    startingCash: p.starting_cash != null ? String(p.starting_cash) : "",
    targetPct: p.target_allocation != null ? String(+(p.target_allocation * 100).toFixed(6)) : "",
    maxPositions: p.max_positions != null ? String(p.max_positions) : "",
    costBps: p.cost_bps_per_side != null ? String(p.cost_bps_per_side) : "",
  };
}

function ParamInput({ p, value, onChange, testid }: { p: Param; value: Value; onChange: (v: Value) => void; testid: string }) {
  const id = `${testid}-${Math.random().toString(36).slice(2, 7)}`;
  return (
    <div className="field">
      <label htmlFor={id}>{p.label || p.name}</label>
      {p.choices && p.choices.length ? (
        <select id={id} data-testid={testid} value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {p.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          data-testid={testid}
          type={p.type === "int" || p.type === "float" ? "number" : "text"}
          step={p.type === "int" ? 1 : "any"}
          min={p.min ?? undefined}
          max={p.max ?? undefined}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {p.help ? <div className="help">{p.help}</div> : null}
    </div>
  );
}

function CondEditor({
  index,
  cond,
  comps,
  onChange,
  onRemove,
  prefix,
}: {
  index: number;
  cond: CondDraft;
  comps: Component[];
  onChange: (c: CondDraft) => void;
  onRemove: () => void;
  prefix: string;
}) {
  const byId = new Map(comps.map((c) => [c.id, c]));
  const indicators = comps.filter((c) => c.uses.includes("indicator"));
  const allowed = cond.kind === "price_cross" ? indicators.filter((c) => c.price_scale) : indicators;
  const ind = byId.get(cond.indicator);
  const kindSchema = byId.get(cond.kind);

  function setKind(kind: CondDraft["kind"]) {
    let indicator = cond.indicator;
    let indParams = cond.indParams;
    if (kind === "price_cross" && !byId.get(indicator)?.price_scale) {
      indicator = indicators.find((c) => c.price_scale)?.id ?? indicator;
      indParams = defaultsOf(byId.get(indicator));
    }
    onChange({ kind, indicator, indParams, params: defaultsOf(byId.get(kind)) });
  }

  function setIndicator(id: string) {
    // A new indicator starts from its own declared defaults (no carried-over values).
    onChange({ ...cond, indicator: id, indParams: defaultsOf(byId.get(id)) });
  }

  return (
    <div className="cond" data-testid={`${prefix}-${index}`}>
      <div className="cond-head">
        <strong>
          {prefix === "condition" ? "Entry" : "Exit"} condition {index + 1}
        </strong>
        <button className="link" onClick={onRemove} aria-label={`Remove condition ${index + 1}`}>
          Remove
        </button>
      </div>
      <div className="fields">
        <div className="field">
          <label>Type</label>
          <select value={cond.kind} onChange={(e) => setKind(e.target.value as CondDraft["kind"])} data-testid="kind-select">
            <option value="threshold">Indicator is above / below a value</option>
            <option value="price_cross">Close crosses an indicator</option>
          </select>
        </div>
        <div className="field">
          <label>Indicator</label>
          <select value={cond.indicator} onChange={(e) => setIndicator(e.target.value)} data-testid="indicator-select">
            {allowed.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label} ({c.id} v{c.version})
              </option>
            ))}
          </select>
          {ind?.description ? <div className="help">{ind.description}</div> : null}
        </div>
        {(ind?.params ?? []).map((p) => (
          <ParamInput
            key={`${cond.indicator}-${p.name}`}
            p={p}
            value={cond.indParams[p.name] ?? ""}
            testid={`param-${p.name}`}
            onChange={(v) => onChange({ ...cond, indParams: { ...cond.indParams, [p.name]: v } })}
          />
        ))}
        {(kindSchema?.params ?? []).map((p) => (
          <ParamInput
            key={`${cond.kind}-${p.name}`}
            p={p}
            value={cond.params[p.name] ?? ""}
            testid={cond.kind === "threshold" ? `threshold-${p.name}` : `cross-${p.name}`}
            onChange={(v) => onChange({ ...cond, params: { ...cond.params, [p.name]: v } })}
          />
        ))}
      </div>
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

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [snapshot, setSnapshot] = useState("");
  const [ready, setReady] = useState(false);
  const [job, setJob] = useState<Job>();
  const [submitError, setSubmitError] = useState<unknown>();

  const byId = useMemo(() => new Map((comps.data ?? []).map((c) => [c.id, c])), [comps.data]);

  // Initialise once: from the source run when duplicating, else a worked example.
  useEffect(() => {
    if (ready || !comps.data || !snaps.data) return;
    if (from) {
      if (!source.data) return;
      setDraft(fromResolved(source.data.resolved as Resolved));
      setSnapshot(source.data.snapshot_id);
    } else {
      const sma = byId.get("sma");
      const rsi = byId.get("rsi");
      const first = snaps.data[0];
      setDraft({
        ...EMPTY,
        universe: first ? first.symbols.filter((s) => s !== "SPY").join(", ") : "",
        entry: [
          ...(sma ? [{ kind: "price_cross" as const, indicator: "sma", indParams: defaultsOf(sma), params: defaultsOf(byId.get("price_cross")) }] : []),
          ...(rsi ? [{ kind: "threshold" as const, indicator: "rsi", indParams: defaultsOf(rsi), params: defaultsOf(byId.get("threshold")) }] : []),
        ],
        exitSessions: defaults.data?.exit_sessions ?? 10,
      });
      setSnapshot(first?.id ?? "");
    }
    setReady(true);
  }, [ready, comps.data, snaps.data, source.data, from, byId, defaults.data]);

  const config = useMemo(() => (comps.data ? toConfig(draft, byId) : undefined), [draft, byId, comps.data]);
  const body: RunSubmit | undefined = config
    ? { config, snapshot_id: snapshot, parent_id: from || null, watchlist: draft.watchlist || null }
    : undefined;
  const debounced = useDebounced(JSON.stringify(body ?? null), 400);
  const check = useAsync(
    () => api.validate(JSON.parse(debounced) as RunSubmit),
    [debounced],
    ready && debounced !== "null",
  );

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const snap = snaps.data?.find((s) => s.id === snapshot);
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

  return (
    <>
      <PageHeader
        title={from ? "Edit a copy" : "Strategy builder"}
        lead={
          from
            ? `A copy of “${source.data?.name ?? from}”. Change one thing and run it; the original stays as it was and becomes this run's parent.`
            : "Pick data, write the rules, and run a backtest. Controls come from the registered indicators, so a new indicator appears here without code changes."
        }
      />

      <Section title="Data">
        {snaps.data && snaps.data.length === 0 ? (
          <p className="notice">
            No price data yet. Import some on the <Link href="/data">Data</Link> page first.
          </p>
        ) : null}
        <div className="fields">
          <div className="field" style={{ gridColumn: "span 2" }}>
            <label htmlFor="snap">Price snapshot</label>
            <select id="snap" data-testid="builder-snapshot" value={snapshot} onChange={(e) => setSnapshot(e.target.value)}>
              {(snaps.data ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.source_label} · {s.symbols.slice(0, 5).join(", ")}
                  {s.symbols.length > 5 ? ` +${s.symbols.length - 5}` : ""} · {s.first_date} to {s.last_date} ({s.id.slice(0, 8)})
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="start">Start (optional)</label>
            <input id="start" type="date" value={draft.start} onChange={(e) => set("start", e.target.value)} min={snap?.first_date} max={snap?.last_date} />
          </div>
          <div className="field">
            <label htmlFor="end">End (optional)</label>
            <input id="end" type="date" value={draft.end} onChange={(e) => set("end", e.target.value)} min={snap?.first_date} max={snap?.last_date} />
          </div>
        </div>
        <div className="fields">
          <div className="field" style={{ gridColumn: "span 2" }}>
            <label htmlFor="universe">Universe (symbols, comma or space separated)</label>
            <input
              id="universe"
              data-testid="builder-universe"
              value={draft.universe}
              disabled={!!draft.watchlist}
              onChange={(e) => set("universe", e.target.value)}
              placeholder="AAPL, MSFT, NVDA"
            />
            {snap ? <div className="help">This snapshot has: {snap.symbols.join(", ")}</div> : null}
          </div>
          <div className="field">
            <label htmlFor="wl">Or use a watchlist</label>
            <select
              id="wl"
              value={draft.watchlist}
              onChange={(e) => {
                const name = e.target.value;
                const w = lists.data?.find((x) => x.name === name);
                setDraft((d) => ({ ...d, watchlist: name, universe: w ? w.members.join(", ") : d.universe }));
              }}
            >
              <option value="">No watchlist</option>
              {(lists.data ?? []).map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name} v{w.version} ({w.members.length})
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="bench">Benchmark</label>
            <input id="bench" value={draft.benchmark} onChange={(e) => set("benchmark", e.target.value)} />
          </div>
        </div>
      </Section>

      <Section title="Buy when all of these are true" aside={<button data-testid="add-condition" onClick={() => addCond("entry")}>Add condition</button>}>
        {draft.entry.length === 0 ? <p className="muted">Add at least one entry condition.</p> : null}
        {draft.entry.map((c, i) => (
          <CondEditor
            key={i}
            index={i}
            prefix="condition"
            cond={c}
            comps={comps.data ?? []}
            onChange={(n) => setDraft((d) => ({ ...d, entry: d.entry.map((x, j) => (j === i ? n : x)) }))}
            onRemove={() => setDraft((d) => ({ ...d, entry: d.entry.filter((_, j) => j !== i) }))}
          />
        ))}
      </Section>

      <Section title="Sell">
        <div className="fields">
          <div className="field">
            <label htmlFor="exitkind">Exit rule</label>
            <select id="exitkind" value={draft.exitKind} onChange={(e) => set("exitKind", e.target.value as Draft["exitKind"])}>
              <option value="holding_period">After a number of sessions</option>
              <option value="signal">When a condition is true</option>
            </select>
          </div>
          {draft.exitKind === "holding_period" ? (
            <div className="field">
              <label htmlFor="sessions">Sessions held (the fill day counts as day one)</label>
              <input
                id="sessions"
                data-testid="exit-sessions"
                type="number"
                min={1}
                value={draft.exitSessions}
                onChange={(e) => set("exitSessions", e.target.value)}
              />
            </div>
          ) : null}
        </div>
        {draft.exitKind === "signal" ? (
          <>
            {draft.exitConds.map((c, i) => (
              <CondEditor
                key={i}
                index={i}
                prefix="exit-condition"
                cond={c}
                comps={comps.data ?? []}
                onChange={(n) => setDraft((d) => ({ ...d, exitConds: d.exitConds.map((x, j) => (j === i ? n : x)) }))}
                onRemove={() => setDraft((d) => ({ ...d, exitConds: d.exitConds.filter((_, j) => j !== i) }))}
              />
            ))}
            <button onClick={() => addCond("exitConds")}>Add exit condition</button>
          </>
        ) : null}
      </Section>

      <Section title="Account">
        <div className="fields">
          <div className="field">
            <label htmlFor="cash">Starting cash ($)</label>
            <input id="cash" type="number" value={draft.startingCash} placeholder={dflt ? String(dflt.starting_cash) : ""} onChange={(e) => set("startingCash", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="target">Target per position (% of equity)</label>
            <input id="target" type="number" value={draft.targetPct} placeholder={dflt ? String(dflt.target_allocation * 100) : ""} onChange={(e) => set("targetPct", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="maxpos">Max positions</label>
            <input id="maxpos" type="number" value={draft.maxPositions} placeholder={dflt ? String(dflt.max_positions) : ""} onChange={(e) => set("maxPositions", e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="bps">Cost per side (basis points)</label>
            <input id="bps" type="number" value={draft.costBps} placeholder={dflt ? String(dflt.cost_bps_per_side) : ""} onChange={(e) => set("costBps", e.target.value)} />
          </div>
        </div>
        <p className="help">Empty fields use today&apos;s defaults; the run records the values it actually used.</p>
      </Section>

      <Section title="Run">
        <div className="field">
          <label htmlFor="name">Run name</label>
          <input id="name" value={draft.name} onChange={(e) => set("name", e.target.value)} />
        </div>
        {check.error ? (
          <ErrorBox error={check.error} title="Not valid yet" />
        ) : check.data ? (
          <p className="summary-box" data-testid="builder-summary">
            {check.data.summary}
          </p>
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
            <Link className="button" href={`/run?id=${encodeURIComponent(newRun)}`} data-testid="open-new-run">
              Open the new run
            </Link>
          ) : null}
        </div>
        <ErrorBox error={submitError} title="Could not start the run" />
      </Section>
    </>
  );
}

export default function BuilderPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Builder />
    </Suspense>
  );
}
