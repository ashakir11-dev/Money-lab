"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { ChoiceBlock, SectionWrap, ViewBlock, type BlockIconName } from "@/components/BuildSection";
import { ConditionEditor, PAIRED, SchemaParams, defaultsOf, type CondDraft, type Value } from "@/components/ConditionEditor";
import { Mark } from "@/components/Mark";
import { Specimen } from "@/components/Specimen";
import { ErrorBox, JobStatus, Loading } from "@/components/ui";
import { api, type Component, type Condition, type Job, type Param, type RunSubmit, type StrategyConfig, type StrategyVersion, waitForJob } from "@/lib/api";
import { nextVersionName, withRun, type LineageRun } from "@/lib/lineage";
import { useAsync, useDebounced } from "@/lib/hooks";
import { SECTORS, sectorSymbols } from "@/lib/sectors";

type FillKind = "next_open" | "next_range" | "dip";
type RuleDraft = { kind: string; params: Record<string, Value> };
/** Exits the builder handles through its own controls (signal conditions and max hold). */
const OWN_EXITS = new Set(["signal", "holding_period"]);

type Timeframe = NonNullable<StrategyConfig["timeframe"]>;
const TIMEFRAMES: Timeframe[] = ["1d", "1h", "30min", "15min", "5min", "1min"];
const asTimeframe = (s: string | undefined): Timeframe =>
  TIMEFRAMES.includes(s as Timeframe) ? (s as Timeframe) : "1d";

type Draft = {
  timeframe: Timeframe;
  name: string;
  universe: string;
  watchlist: string;
  universeSource: "custom" | "watchlist" | "sector";
  sector: string;
  benchmark: string;
  start: string;
  end: string;
  entry: CondDraft[];
  /** False when the fill block has been removed. A buy then fills at the next open. */
  fillOn: boolean;
  fillKind: FillKind;
  rangeBelow: string;
  rangeAbove: string;
  dipPct: string;
  /** Signal conditions on the entry row. They must pass along with `entry`. */
  entryConds: CondDraft[];
  /** False when the hold-sessions exit has been removed. */
  hold: boolean;
  maxHold: string;
  exitConds: CondDraft[];
  /** Price exits (stop loss, take profit, trailing stop...); the first exit that fires sells. */
  guards: RuleDraft[];
  sizingId: string;
  sizingParams: Record<string, Value>;
  rankInd: string;
  rankParams: Record<string, Value>;
  rankOrder: "ascending" | "descending";
  startingCash: string;
  targetPct: string;
  maxPositions: string;
  costBps: string;
  /** What cash not needed by a position does: stay as cash, or be held in the benchmark. */
  idleCash: "cash" | "benchmark";
};

const EMPTY: Draft = {
  timeframe: "1d",
  name: "My strategy",
  universe: "",
  watchlist: "",
  universeSource: "custom",
  sector: "",
  benchmark: "SPY",
  start: "",
  end: "",
  entry: [],
  fillOn: true,
  fillKind: "next_open",
  rangeBelow: "2",
  rangeAbove: "2",
  dipPct: "2",
  entryConds: [],
  hold: true,
  maxHold: "10",
  exitConds: [],
  guards: [],
  sizingId: "percent_of_equity",
  sizingParams: {},
  rankInd: "",
  rankParams: {},
  rankOrder: "ascending",
  startingCash: "",
  targetPct: "",
  maxPositions: "",
  costBps: "",
  idleCash: "cash",
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
  const out: Condition = {
    kind: c.kind,
    indicator: { id: c.indicator, params: cleanParams(comps.get(c.indicator), c.indParams) },
    params: cleanParams(comps.get(c.kind), c.params),
  };
  if (PAIRED.has(c.kind) && c.other) {
    out.other = { id: c.other, params: cleanParams(comps.get(c.other), c.otherParams ?? {}) };
  }
  return out;
}

/**
 * The signal / max-hold exit, plus any price exits. Without price exits only `exit` is
 * sent, so configs built before price exits existed keep their exact shape.
 */
const FILLS: { id: FillKind; label: string; help: string; icon: BlockIconName }[] = [
  { id: "next_open", label: "Next open", help: "Fill at the next session open.", icon: "arrow" },
  {
    id: "next_range",
    label: "In range",
    help: "Fill on the next bar only if it trades inside this band around the signal close. A gap outside cancels the order.",
    icon: "range",
  },
  {
    id: "dip",
    label: "Dip",
    help: "Limit below the signal close. Waits until that session’s low prints through; a gap through fills at the open.",
    icon: "dip",
  },
];

/** Sessions on the hold block, or null when the block is off or the number is blank. */
function holdSessions(d: Draft): number | null {
  if (!d.hold || d.maxHold.trim() === "") return null;
  const n = Number(d.maxHold);
  return Number.isFinite(n) && n >= 1 ? n : null;
}

/** A run needs one signal, one entry block, and one complete exit block. */
function draftReady(d: Draft): boolean {
  if (!d.entry.length) return false;
  if (!d.fillOn && !d.entryConds.length) return false;
  if (d.hold && holdSessions(d) == null) return false;
  return d.guards.length > 0 || d.exitConds.length > 0 || holdSessions(d) != null;
}

function exitsOf(d: Draft, comps: Map<string, Component>): Pick<StrategyConfig, "exit" | "exits"> {
  const hold = holdSessions(d);
  const guards = d.guards.map((g) => ({ kind: g.kind, params: cleanParams(comps.get(g.kind), g.params), conditions: [] }));
  const primary = d.exitConds.length
    ? { kind: "signal", params: hold ? { max_sessions: hold } : {}, conditions: d.exitConds.map((c) => toCondition(c, comps)) }
    : hold
      ? { kind: "holding_period", params: { sessions: hold }, conditions: [] }
      : null;
  if (!guards.length) return { exit: primary! };
  // Price exits go first so a session where both fire is reported as the stop.
  const exits = primary ? [...guards, primary] : guards;
  return { exit: exits[0], exits };
}

function toConfig(d: Draft, comps: Map<string, Component>): StrategyConfig {
  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const target = num(d.targetPct);
  return {
    name: d.name || "Untitled strategy",
    universe: splitSymbols(d.universe),
    benchmark: d.benchmark.trim().toUpperCase() || "SPY",
    timeframe: d.timeframe,
    start: d.start || null,
    end: d.end || null,
    entry: d.entry.map((c) => toCondition(c, comps)),
    ...(d.entryConds.length ? { entry_signals: d.entryConds.map((c) => toCondition(c, comps)) } : {}),
    entry_fill: d.fillOn
      ? {
          kind: d.fillKind,
          below_pct: d.fillKind === "next_range" && d.rangeBelow.trim() !== "" ? Number(d.rangeBelow) / 100 : null,
          above_pct: d.fillKind === "next_range" && d.rangeAbove.trim() !== "" ? Number(d.rangeAbove) / 100 : null,
          dip_pct: d.fillKind === "dip" && d.dipPct.trim() !== "" ? Number(d.dipPct) / 100 : null,
        }
      : { kind: "next_open", below_pct: null, above_pct: null, dip_pct: null },
    ...exitsOf(d, comps),
    ...(d.sizingId && d.sizingId !== "percent_of_equity"
      ? { sizing: { id: d.sizingId, params: cleanParams(comps.get(d.sizingId), d.sizingParams) } }
      : {}),
    ...(d.rankInd
      ? { rank_by: { indicator: { id: d.rankInd, params: cleanParams(comps.get(d.rankInd), d.rankParams) }, order: d.rankOrder } }
      : {}),
    portfolio: {
      starting_cash: num(d.startingCash),
      target_allocation: target === null ? null : target / 100,
      max_positions: num(d.maxPositions),
      cost_bps_per_side: num(d.costBps),
      idle_cash: d.idleCash,
    },
  };
}

type ResolvedRef = { id: string; params?: Record<string, Value> };
type ResolvedCond = { kind: CondDraft["kind"]; indicator: ResolvedRef; other?: ResolvedRef | null; params?: Record<string, Value> };
type ResolvedExit = { kind?: string; params?: Record<string, Value>; conditions?: ResolvedCond[] };
type Resolved = {
  timeframe?: string;
  name?: string;
  universe?: string[];
  benchmark?: string;
  start?: string | null;
  end?: string | null;
  entry?: ResolvedCond[];
  entry_signals?: ResolvedCond[];
  entry_fill?: { kind?: FillKind; below_pct?: number | null; above_pct?: number | null; dip_pct?: number | null };
  exit?: ResolvedExit;
  exits?: ResolvedExit[];
  sizing?: { id?: string; params?: Record<string, Value> };
  rank_by?: { indicator: ResolvedRef; order?: "ascending" | "descending" } | null;
  watchlist?: { name?: string };
  portfolio?: { starting_cash?: number; target_allocation?: number; max_positions?: number; cost_bps_per_side?: number; idle_cash?: "cash" | "benchmark" };
};

function fromResolved(r: Resolved): Draft {
  const cond = (c: ResolvedCond): CondDraft => ({
    kind: c.kind,
    indicator: c.indicator.id,
    indParams: { ...(c.indicator.params ?? {}) },
    params: { ...(c.params ?? {}) },
    ...(c.other ? { other: c.other.id, otherParams: { ...(c.other.params ?? {}) } } : {}),
  });
  const p = r.portfolio ?? {};
  const all = r.exits?.length ? r.exits : r.exit ? [r.exit] : [];
  const main = all.find((e) => OWN_EXITS.has(e.kind ?? "holding_period"));
  const mainKind = main ? (main.kind ?? "holding_period") : undefined;
  const holdRaw =
    mainKind === "holding_period" ? main?.params?.sessions : mainKind === "signal" ? main?.params?.max_sessions : undefined;
  const holdOn = holdRaw != null && holdRaw !== "";
  const extras = r.entry_signals ?? [];
  const stored = r.entry ?? [];
  const split = extras.length > 0 && extras.length <= stored.length;
  return {
    ...EMPTY,
    name: r.name ?? "Strategy",
    universe: (r.universe ?? []).join(", "),
    benchmark: r.benchmark ?? "SPY",
    timeframe: asTimeframe(r.timeframe),
    start: r.start ?? "",
    end: r.end ?? "",
    entry: (split ? stored.slice(0, stored.length - extras.length) : stored).map(cond),
    fillOn: true,
    fillKind: r.entry_fill?.kind ?? "next_open",
    rangeBelow: r.entry_fill?.below_pct != null ? String(+(r.entry_fill.below_pct * 100).toFixed(6)) : "2",
    rangeAbove: r.entry_fill?.above_pct != null ? String(+(r.entry_fill.above_pct * 100).toFixed(6)) : "2",
    dipPct: r.entry_fill?.dip_pct != null ? String(+(r.entry_fill.dip_pct * 100).toFixed(6)) : "2",
    entryConds: (split ? extras : []).map(cond),
    hold: holdOn,
    maxHold: holdOn ? String(holdRaw) : "10",
    exitConds: (main?.conditions ?? []).map(cond),
    guards: all.filter((e) => !OWN_EXITS.has(e.kind ?? "holding_period")).map((e) => ({ kind: e.kind ?? "", params: { ...(e.params ?? {}) } })),
    sizingId: r.sizing?.id ?? "percent_of_equity",
    sizingParams: { ...(r.sizing?.params ?? {}) },
    rankInd: r.rank_by?.indicator.id ?? "",
    rankParams: { ...(r.rank_by?.indicator.params ?? {}) },
    rankOrder: r.rank_by?.order ?? "ascending",
    universeSource: r.watchlist?.name ? "watchlist" : "custom",
    watchlist: r.watchlist?.name ?? "",
    startingCash: p.starting_cash != null ? String(p.starting_cash) : "",
    targetPct: p.target_allocation != null ? String(+(p.target_allocation * 100).toFixed(6)) : "",
    maxPositions: p.max_positions != null ? String(p.max_positions) : "",
    costBps: p.cost_bps_per_side != null ? String(p.cost_bps_per_side) : "",
    idleCash: p.idle_cash === "benchmark" ? "benchmark" : "cash",
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

function versionAsResolved(version: StrategyVersion): Resolved {
  const rules = version.rules as Resolved;
  const run = version.runs[0];
  const exits = (rules.exits ?? []) as Resolved["exits"];
  return {
    ...rules,
    name: version.name,
    universe: run?.universe ?? rules.universe ?? [],
    start: run?.start ?? rules.start,
    end: run?.end ?? rules.end,
    exits,
    exit: exits?.[0] ?? rules.exit,
  };
}

function Builder() {
  const params = useSearchParams();
  const from = params.get("from") ?? "";
  const editing = params.get("strategy") ?? "";
  const comps = useAsync(() => api.components(), []);
  const defaults = useAsync(() => api.defaults(), []);
  const snaps = useAsync(() => api.snapshots(), []);
  const lists = useAsync(() => api.watchlists(), []);
  const source = useAsync(() => api.run(from), [from], !!from);
  const version = useAsync(() => api.strategy(editing), [editing], !!editing && !from);
  const prior = useAsync(() => api.runs(), [], !!from);
  const parentEq = useAsync(() => api.equity(from), [from], !!from);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [open, setOpen] = useState("");
  const [snapshot, setSnapshot] = useState("");
  const [ready, setReady] = useState(false);
  const [job, setJob] = useState<Job>();
  const [savedId, setSavedId] = useState<string>();
  const [submitError, setSubmitError] = useState<unknown>();
  const doneId = job?.status === "complete" ? job.run_ids[0] : undefined;
  const newEq = useAsync(() => api.equity(doneId ?? ""), [doneId], !!doneId);

  const byId = useMemo(() => new Map((comps.data ?? []).map((c) => [c.id, c])), [comps.data]);

  // Initialise once: from the source run when duplicating, else a worked example.
  useEffect(() => {
    if (ready || !comps.data || !snaps.data) return;
    if (!from && editing) {
      if (!version.data) return;
      const next = fromResolved(versionAsResolved(version.data));
      const first = snaps.data[0];
      if (!next.universe && first) next.universe = first.symbols.filter((s) => s !== "SPY").join(", ");
      if (!next.start && first) next.start = first.first_date;
      if (!next.end && first) next.end = first.last_date;
      const snapId = version.data.runs[0]?.snapshot_id ?? first?.id ?? "";
      setDraft(next);
      setSnapshot(snapId);
    } else if (from) {
      const src = source.data;
      if (!src || (!prior.data && !prior.error)) return;
      const next = fromResolved(src.resolved as Resolved);
      const known = withRun<LineageRun>(prior.data ?? [], {
        id: src.id,
        parent_id: src.parent_id,
        name: src.name,
        created_at: src.created_at,
      });
      next.name = nextVersionName(known, from);
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
        timeframe: asTimeframe(first?.timeframe),
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
  }, [ready, comps.data, snaps.data, source.data, prior.data, prior.error, from, editing, version.data, byId, defaults.data]);

  const readyDraft = draftReady(draft);
  const config = useMemo(
    () => (comps.data && readyDraft ? toConfig(draft, byId) : undefined),
    [draft, byId, comps.data, readyDraft],
  );
  const body: RunSubmit | undefined = config
    ? {
        config,
        snapshot_id: snapshot,
        parent_id: from || null,
        strategy_id: editing || source.data?.strategy_id || null,
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
    if (s) setDraft((d) => ({ ...d, start: s.first_date, end: s.last_date, timeframe: asTimeframe(s.timeframe) }));
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

  async function saveVersion() {
    if (!config) return;
    setSubmitError(undefined);
    try {
      const saved = await api.saveStrategy({
        config,
        strategy_id: editing || source.data?.strategy_id || null,
      });
      setSavedId(saved.id);
    } catch (e) {
      setSubmitError(e);
    }
  }

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

  function addCond(which: "entry" | "exitConds" | "entryConds") {
    const ind = byId.get("rsi") ?? (comps.data ?? []).find((c) => c.uses.includes("indicator"));
    const c: CondDraft = { kind: "threshold", indicator: ind?.id ?? "", indParams: defaultsOf(ind), params: defaultsOf(byId.get("threshold")) };
    const index = draft[which].length;
    setDraft((d) => ({ ...d, [which]: [...d[which], c] }));
    setOpen(which === "entry" ? `condition-${index}` : which === "entryConds" ? `entry-signal-${index}` : `exit-${index}`);
  }

  function addFill(kind: string) {
    if (kind !== "next_open" && kind !== "next_range" && kind !== "dip") return;
    setDraft((d) => ({ ...d, fillOn: true, fillKind: kind }));
    setOpen("fill");
  }

  function addHold() {
    setDraft((d) => ({ ...d, hold: true, maxHold: d.maxHold.trim() === "" ? "10" : d.maxHold }));
    setOpen("hold");
  }

  const loadError = comps.error || snaps.error || defaults.error || source.error || version.error;
  if (loadError) return <ErrorBox error={loadError} title="Could not load the builder" />;
  if (!ready) return <Loading what="Loading the builder" />;
  const dflt = defaults.data;

  const parentDates = (parentEq.data ?? []).map((e) => e.session);
  const all = comps.data ?? [];
  const guardKinds = all.filter((c) => c.uses.includes("exit_rule") && !OWN_EXITS.has(c.id));
  const sizers = all.filter((c) => c.uses.includes("sizing"));
  const rankable = all.filter((c) => c.uses.includes("indicator"));
  const usedGuards = new Set(draft.guards.map((g) => g.kind));

  function addGuard(kind: string) {
    if (!kind) return;
    setDraft((d) => ({ ...d, guards: [...d.guards, { kind, params: defaultsOf(byId.get(kind)) }] }));
    setOpen(`guard-${kind}`);
  }

  const toggle = (id: string) => setOpen((cur) => (cur === id ? "" : id));
  const guardIcon = (kind: string): BlockIconName => (kind === "take_profit" ? "target" : kind === "trailing_stop" ? "trail" : "stop");

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
        <SectionWrap tone="setup" title="Setup">
          {snaps.data && snaps.data.length === 0 ? (
            <p className="notice">
              No price data yet. Import some on the <Link href="/data">Data</Link> page first.
            </p>
          ) : null}
          <div className="fields fields-setup">
            <div className="field">
              <label htmlFor="snap">Price snapshot</label>
              <select id="snap" data-testid="builder-snapshot" value={snapshot} onChange={(e) => pickSnapshot(e.target.value)}>
                {(snaps.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="timeframe">Bar size</label>
              <select
                id="timeframe"
                data-testid="builder-timeframe"
                value={draft.timeframe}
                onChange={(e) => set("timeframe", e.target.value as Timeframe)}
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>
                    {tf === "1d" ? "Daily (1d)" : tf}
                  </option>
                ))}
              </select>
              <div className="help">
                {snap && asTimeframe(snap.timeframe) !== draft.timeframe
                  ? `This snapshot has ${snap.timeframe} bars. Pick a ${draft.timeframe} snapshot, or switch back.`
                  : "Lookbacks and hold times count bars of this size."}
              </div>
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
              <textarea
                id="universe"
                data-testid="builder-universe"
                rows={Math.min(6, Math.max(3, Math.ceil(draft.universe.length / 90)))}
                value={draft.universe}
                onChange={(e) => set("universe", e.target.value)}
                placeholder="AAPL, MSFT, NVDA"
              />
              {snap ? (
                <div className="help">
                  {snap.symbols.length} names in this snapshot
                  {snap.symbols.length <= 12 ? `: ${snap.symbols.join(", ")}` : ""}
                </div>
              ) : null}
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
        </SectionWrap>

        <SectionWrap
          tone="signal"
          title="Signal"
          count={draft.entry.length}
          action={
            <button className="block-section-action" data-testid="add-condition" onClick={() => addCond("entry")}>
              Add
            </button>
          }
        >
          {draft.entry.length === 0 ? <p className="muted">Add at least one rule.</p> : null}
          <div className="block-row">
            {draft.entry.map((c, i) => (
              <ConditionEditor
                key={i}
                index={i}
                prefix="condition"
                cond={c}
                comps={comps.data ?? []}
                open={open === `condition-${i}`}
                onOpen={() => toggle(`condition-${i}`)}
                onChange={(n) => setDraft((d) => ({ ...d, entry: d.entry.map((x, j) => (j === i ? n : x)) }))}
                onRemove={() => setDraft((d) => ({ ...d, entry: d.entry.filter((_, j) => j !== i) }))}
              />
            ))}
          </div>
        </SectionWrap>

        <SectionWrap
          tone="entry"
          title="Entry"
          count={(draft.fillOn ? 1 : 0) + draft.entryConds.length}
          action={
            <span className="block-section-action">
              <button type="button" data-testid="add-entry-signal" onClick={() => addCond("entryConds")}>
                Add signal
              </button>
              <select
                data-testid="add-entry"
                aria-label="Add an entry"
                value=""
                onChange={(e) => addFill(e.target.value)}
                style={{ width: "auto" }}
              >
                <option value="">Add entry…</option>
                {FILLS.map((f) => (
                  <option key={f.id} value={f.id} disabled={draft.fillOn && draft.fillKind === f.id}>
                    {f.label}
                  </option>
                ))}
              </select>
            </span>
          }
        >
          <p className="muted">
            One fill, and any signals. A signal here has to pass along with the Signal section before the buy.
          </p>
          {!draft.fillOn && draft.entryConds.length === 0 ? <p className="muted">Add at least one entry.</p> : null}
          {!draft.fillOn && draft.entryConds.length > 0 ? (
            <p className="muted">No fill block: the buy fills at the next open.</p>
          ) : null}
          <div className="block-row">
            {draft.fillOn
              ? (() => {
                  const fill = FILLS.find((f) => f.id === draft.fillKind) ?? FILLS[0];
                  const summary =
                    fill.id === "dip"
                      ? `Dip ${draft.dipPct || "—"}%`
                      : fill.id === "next_range"
                        ? `In range ${draft.rangeBelow || "—"}% / ${draft.rangeAbove || "—"}%`
                        : fill.label;
                  return (
                    <ViewBlock
                      testId="entry-fill"
                      icon={fill.icon}
                      summary={summary}
                      help={fill.help}
                      open={open === "fill"}
                      onOpen={() => toggle("fill")}
                      onRemove={() => setDraft((d) => ({ ...d, fillOn: false }))}
                      removeLabel={`Remove ${fill.label}`}
                    >
                      {fill.id === "next_range" ? (
                        <>
                          <PctSlider id="range-below" label="Below %" value={draft.rangeBelow} onChange={(v) => set("rangeBelow", v)} />
                          <PctSlider id="range-above" label="Above %" value={draft.rangeAbove} onChange={(v) => set("rangeAbove", v)} />
                        </>
                      ) : fill.id === "dip" ? (
                        <PctSlider id="dip-pct" label="Dip %" value={draft.dipPct} onChange={(v) => set("dipPct", v)} />
                      ) : null}
                    </ViewBlock>
                  );
                })()
              : null}
            {draft.entryConds.map((c, i) => (
              <ConditionEditor
                key={i}
                index={i}
                prefix="entry-signal"
                cond={c}
                comps={comps.data ?? []}
                open={open === `entry-signal-${i}`}
                onOpen={() => toggle(`entry-signal-${i}`)}
                onChange={(n) => setDraft((d) => ({ ...d, entryConds: d.entryConds.map((x, j) => (j === i ? n : x)) }))}
                onRemove={() => setDraft((d) => ({ ...d, entryConds: d.entryConds.filter((_, j) => j !== i) }))}
              />
            ))}
          </div>
        </SectionWrap>

        <SectionWrap
          tone="exit"
          title="Exit"
          count={draft.guards.length + draft.exitConds.length + (draft.hold ? 1 : 0)}
          action={
            <span className="block-section-action">
              <button type="button" onClick={() => addCond("exitConds")}>
                Add signal
              </button>
              <select
                data-testid="add-price-exit"
                aria-label="Add an exit"
                value=""
                onChange={(e) => {
                  if (e.target.value === "holding_period") addHold();
                  else addGuard(e.target.value);
                }}
                style={{ width: "auto" }}
                disabled={draft.hold && guardKinds.every((c) => usedGuards.has(c.id))}
              >
                <option value="">Add exit…</option>
                <option value="holding_period" disabled={draft.hold}>
                  Hold sessions
                </option>
                {guardKinds.map((c) => (
                  <option key={c.id} value={c.id} disabled={usedGuards.has(c.id)} title={c.description}>
                    {c.label}
                  </option>
                ))}
              </select>
            </span>
          }
        >
          <p className="muted">
            Any one of these sells. A stop and a profit target do not both have to hit, and an exit signal fires when any of its conditions pass.
          </p>
          {draft.guards.length + draft.exitConds.length + (draft.hold ? 1 : 0) === 0 ? (
            <p className="muted">Add at least one exit.</p>
          ) : null}
          <div className="block-row">
            {draft.guards.map((g, i) => {
              const schema = byId.get(g.kind);
              const pct = Number(g.params.pct);
              const summary = Number.isFinite(pct) ? `${schema?.label ?? g.kind} ${+(pct * 100).toFixed(2)}%` : (schema?.label ?? g.kind);
              return (
                <ViewBlock
                  key={`${g.kind}-${i}`}
                  testId={`price-exit-${g.kind}`}
                  icon={guardIcon(g.kind)}
                  summary={summary}
                  help={schema?.description ?? ""}
                  open={open === `guard-${g.kind}`}
                  onOpen={() => toggle(`guard-${g.kind}`)}
                  onRemove={() => setDraft((d) => ({ ...d, guards: d.guards.filter((_, j) => j !== i) }))}
                  removeLabel={`Remove ${schema?.label ?? g.kind}`}
                >
                  <SchemaParams
                    schema={schema}
                    values={g.params}
                    prefix={`price-exit-${g.kind}`}
                    onChange={(v) => setDraft((d) => ({ ...d, guards: d.guards.map((x, j) => (j === i ? { ...x, params: v } : x)) }))}
                  />
                </ViewBlock>
              );
            })}
            {draft.exitConds.map((c, i) => (
              <ConditionEditor
                key={i}
                index={i}
                prefix="exit-condition"
                cond={c}
                comps={comps.data ?? []}
                open={open === `exit-${i}`}
                onOpen={() => toggle(`exit-${i}`)}
                onChange={(n) => setDraft((d) => ({ ...d, exitConds: d.exitConds.map((x, j) => (j === i ? n : x)) }))}
                onRemove={() => setDraft((d) => ({ ...d, exitConds: d.exitConds.filter((_, j) => j !== i) }))}
              />
            ))}
            {draft.hold ? (
              <ViewBlock
                testId="exit-hold"
                icon="clock"
                summary={holdSessions(draft) ? `Hold ${draft.maxHold}` : "Hold sessions"}
                help="Fill day counts as session one. With exit signals, this is a cap: the signal or this many sessions, whichever comes first."
                open={open === "hold"}
                onOpen={() => toggle("hold")}
                onRemove={() => setDraft((d) => ({ ...d, hold: false }))}
                removeLabel="Remove hold sessions"
              >
                <div className="cond-row">
                  <label className="cond-label" htmlFor="sessions">
                    Sessions
                  </label>
                  <input
                    id="sessions"
                    data-testid="exit-sessions"
                    type="number"
                    min={1}
                    value={draft.maxHold}
                    onChange={(e) => set("maxHold", e.target.value)}
                    placeholder="none"
                  />
                </div>
              </ViewBlock>
            ) : null}
          </div>
        </SectionWrap>

        <SectionWrap tone="size" title="Size" count={1}>
          <div className="block-row" role="group" aria-label="Sizing method">
            {sizers.map((c) => (
              <ChoiceBlock
                key={c.id}
                icon="bars"
                testId={`sizing-${c.id}`}
                label={c.id === "percent_of_equity" ? "% equity" : c.id === "fixed_dollar" ? "Fixed $" : "ATR risk"}
                help={c.description}
                selected={draft.sizingId === c.id}
                onClick={() => setDraft((d) => ({ ...d, sizingId: c.id, sizingParams: defaultsOf(c) }))}
              >
                {c.params.length ? (
                  <SchemaParams schema={c} values={draft.sizingParams} prefix="sizing" onChange={(v) => set("sizingParams", v)} />
                ) : null}
              </ChoiceBlock>
            ))}
            <ViewBlock
              icon="bars"
              summary={draft.targetPct ? `${draft.targetPct}% cap` : "Size cap"}
              help={draft.sizingId === "percent_of_equity" ? "Percent of equity used for each new position." : "Largest position, as a percent of equity."}
              open={open === "cap"}
              onOpen={() => toggle("cap")}
            >
              <div className="cond-row">
                <label className="cond-label" htmlFor="target">
                  {draft.sizingId === "percent_of_equity" ? "Each position" : "Largest"}
                </label>
                <input id="target" type="number" value={draft.targetPct} placeholder={dflt ? String(dflt.target_allocation * 100) : ""} onChange={(e) => set("targetPct", e.target.value)} />
              </div>
            </ViewBlock>
          </div>
        </SectionWrap>

        <SectionWrap tone="positions" title="Positions" count={draft.rankInd ? 2 : 1}>
          <div className="block-row">
            <ViewBlock
              icon="grid"
              summary={draft.maxPositions ? `Max ${draft.maxPositions}` : "Max positions"}
              help="How many names can be held at once. Extra signals wait."
              open={open === "maxpos"}
              onOpen={() => toggle("maxpos")}
            >
              <div className="cond-row">
                <label className="cond-label" htmlFor="maxpos">
                  Positions
                </label>
                <input id="maxpos" type="number" value={draft.maxPositions} placeholder={dflt ? String(dflt.max_positions) : ""} onChange={(e) => set("maxPositions", e.target.value)} />
              </div>
            </ViewBlock>
            <ViewBlock
              testId="rank-block"
              icon="rank"
              summary={draft.rankInd ? `By ${byId.get(draft.rankInd)?.label ?? draft.rankInd}` : "A to Z"}
              help="When more names qualify than there is room for, this decides which ones are bought."
              open={open === "rank"}
              onOpen={() => toggle("rank")}
            >
              <div className="cond-row">
                <label className="cond-label" htmlFor="rank">
                  Order
                </label>
                <select
                  id="rank"
                  data-testid="rank-indicator"
                  value={draft.rankInd}
                  onChange={(e) => {
                    const id = e.target.value;
                    setDraft((d) => ({ ...d, rankInd: id, rankParams: defaultsOf(byId.get(id)) }));
                  }}
                >
                  <option value="">By symbol, A to Z</option>
                  {rankable.map((c) => (
                    <option key={c.id} value={c.id} title={c.description}>
                      By {c.label}
                    </option>
                  ))}
                </select>
              </div>
              {draft.rankInd ? (
                <>
                  <div className="block-row" role="group" aria-label="Ranking order">
                    {(
                      [
                        ["ascending", "Lowest first"],
                        ["descending", "Highest first"],
                      ] as const
                    ).map(([id, label]) => (
                      <ChoiceBlock
                        key={id}
                        icon="rank"
                        testId={`rank-${id}`}
                        label={label}
                        selected={draft.rankOrder === id}
                        onClick={() => set("rankOrder", id)}
                      />
                    ))}
                  </div>
                  <SchemaParams schema={byId.get(draft.rankInd)} values={draft.rankParams} prefix="rank" onChange={(v) => set("rankParams", v)} />
                </>
              ) : null}
            </ViewBlock>
          </div>
        </SectionWrap>

        <SectionWrap tone="capital" title="Capital" count={3}>
          <div className="block-row">
            <ViewBlock
              icon="cash"
              summary={draft.startingCash ? `$${draft.startingCash}` : "Cash"}
              help="The account balance the backtest starts with."
              open={open === "cash"}
              onOpen={() => toggle("cash")}
            >
              <div className="cond-row">
                <label className="cond-label" htmlFor="cash">
                  Cash ($)
                </label>
                <input id="cash" type="number" value={draft.startingCash} placeholder={dflt ? String(dflt.starting_cash) : ""} onChange={(e) => set("startingCash", e.target.value)} />
              </div>
            </ViewBlock>
            <ViewBlock
              icon="cost"
              summary={draft.costBps ? `${draft.costBps} bp` : "Cost"}
              help="Trading cost charged on each buy and each sell, in basis points."
              open={open === "cost"}
              onOpen={() => toggle("cost")}
            >
              <div className="cond-row">
                <label className="cond-label" htmlFor="bps">
                  Per side
                </label>
                <input id="bps" type="number" value={draft.costBps} placeholder={dflt ? String(dflt.cost_bps_per_side) : ""} onChange={(e) => set("costBps", e.target.value)} />
              </div>
            </ViewBlock>
          </div>
          <p className="muted small" id="idle-cash-label">
            Idle cash: what the cash that is not in a position does
          </p>
          <div className="block-row" role="group" aria-labelledby="idle-cash-label" data-testid="idle-cash">
            {(
              [
                ["cash", "Keep as cash", "Cash not in a position sits uninvested. This is how every run before engine 0.4 worked."],
                [
                  "benchmark",
                  `Hold ${draft.benchmark.trim().toUpperCase() || "SPY"}`,
                  "Cash not in a position buys whole shares of the benchmark at each open, and those shares are sold at the open just before a trade needs the cash. This makes the comparison with the benchmark fair for strategies that are often in cash.",
                ],
              ] as const
            ).map(([id, label, help]) => (
              <ChoiceBlock
                key={id}
                icon="cash"
                testId={`idle-cash-${id}`}
                selected={draft.idleCash === id}
                label={label}
                help={help}
                onClick={() => set("idleCash", id)}
              />
            ))}
          </div>
        </SectionWrap>

        <section className="stack-block">
          {readyDraft && check.error ? (
            <ErrorBox error={check.error} title="Not valid yet" />
          ) : readyDraft && check.data ? (
            <div className="summary-box" data-testid="builder-summary">
              <p className="plain-summary">{check.data.summary}</p>
            </div>
          ) : null}
          <div className="row" style={{ marginTop: 12 }}>
            <button
              className="primary"
              data-testid="run-backtest"
              onClick={submit}
              disabled={busy || !snapshot || !readyDraft || !!check.error}
            >
              Run backtest
            </button>
            <button data-testid="save-strategy" onClick={saveVersion} disabled={!readyDraft || !!check.error}>
              Save strategy
            </button>
            {savedId ? (
              <Link className="button" href={`/strategies?id=${encodeURIComponent(savedId)}`}>
                Open the saved version
              </Link>
            ) : null}
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
