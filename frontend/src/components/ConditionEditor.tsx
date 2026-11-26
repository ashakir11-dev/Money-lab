"use client";

import type { ReactNode } from "react";
import type { Component, Param } from "@/lib/api";

export type Value = string | number;
export type CondKind = "price_cross" | "threshold" | "indicator_compare" | "indicator_cross";
export type CondDraft = {
  kind: CondKind;
  indicator: string;
  indParams: Record<string, Value>;
  params: Record<string, Value>;
  /** Second indicator, only for indicator_compare and indicator_cross. */
  other?: string;
  otherParams?: Record<string, Value>;
};

export const PAIRED: ReadonlySet<string> = new Set(["indicator_compare", "indicator_cross"]);

const KIND_LABEL: Record<CondKind, string> = {
  threshold: "Level",
  price_cross: "Price cross",
  indicator_compare: "Compare",
  indicator_cross: "Indicator cross",
};

const IND_SHORT: Record<string, string> = {
  sma: "SMA",
  rsi: "RSI",
  macd: "MACD",
  breadth: "Breadth",
  ema: "EMA",
  atr: "ATR",
  bollinger_pctb: "Bollinger %B",
  roc: "ROC",
  highest_high: "Highest high",
  lowest_low: "Lowest low",
  avg_volume: "Avg volume",
  adx: "ADX / DI",
  linreg_trend: "Regression trend",
  market_structure: "Market structure",
  straightness: "Straightness",
  swing_high: "Swing high",
  swing_low: "Swing low",
  support: "Support",
  resistance: "Resistance",
  support_distance: "Dist. to support",
  resistance_distance: "Dist. to resistance",
  support_tests: "Support tests",
  resistance_tests: "Resistance tests",
};

const PARAM_SHORT: Record<string, string> = {
  length: "Lookback",
  fast: "Fast",
  slow: "Slow",
  signal: "Signal",
  value: "Level",
  op: "When",
  direction: "When",
  output: "Output",
  stdevs: "Width (σ)",
  pct: "Percent",
  sessions: "Sessions",
  amount: "Amount ($)",
  risk_fraction: "Risk",
  atr_length: "ATR lookback",
  atr_multiple: "Stop (× ATR)",
  left: "Bars before",
  right: "Confirm bars",
  min_move: "Min move (ATR)",
  zone: "Zone (ATR)",
  lookback: "Memory",
};

/** Params the engine takes as fractions (0.08) are shown and typed as percents (8). */
export const isFraction = (p: Param) => /fraction/i.test(p.label);

export const defaultsOf = (c: Component | undefined): Record<string, Value> =>
  Object.fromEntries((c?.params ?? []).map((p) => [p.name, (p.default as Value) ?? ""]));

function shortIndicator(c: Component): string {
  return IND_SHORT[c.id] ?? c.label;
}

function shortLabel(p: Param): string {
  const base = PARAM_SHORT[p.name] ?? (p.label.replace(/\s*\([^)]*\)/g, "").trim() || p.name);
  return isFraction(p) ? `${base} %` : base;
}

function asNum(v: Value): number | null {
  if (v === "" || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Practical slider window. Schema min/max stay on the typed field. */
/** Threshold slider ranges for indicators with a known scale. Key: id, or id:output. */
const VALUE_WINDOWS: Record<string, { min: number; max: number; step: number }> = {
  rsi: { min: 0, max: 100, step: 1 },
  breadth: { min: 0, max: 1, step: 0.01 },
  bollinger_pctb: { min: 0, max: 1, step: 0.01 },
  roc: { min: -0.5, max: 0.5, step: 0.01 },
  adx: { min: 0, max: 100, step: 1 },
  straightness: { min: -1, max: 1, step: 0.05 },
  "straightness:unsigned": { min: 0, max: 1, step: 0.05 },
  "linreg_trend:slope_pct_year": { min: -100, max: 100, step: 1 },
  "linreg_trend:tstat": { min: -5, max: 5, step: 0.1 },
  "linreg_trend:r2": { min: 0, max: 1, step: 0.01 },
  market_structure: { min: -1, max: 1, step: 1 },
  support_distance: { min: -5, max: 5, step: 0.1 },
  resistance_distance: { min: -5, max: 5, step: 0.1 },
  support_tests: { min: 1, max: 10, step: 1 },
  resistance_tests: { min: 1, max: 10, step: 1 },
};

function sliderWindow(p: Param, indicator: string): { min: number; max: number; step: number } {
  const step = p.type === "int" ? 1 : 0.01;
  const lo = typeof p.min === "number" ? p.min : null;
  const hi = typeof p.max === "number" ? p.max : null;
  if (lo != null && hi != null && hi - lo <= 500 && Math.abs(hi) < 1e6) {
    return { min: lo, max: hi, step };
  }
  if (p.name === "value") {
    // `indicator` may carry the output, e.g. "linreg_trend:tstat".
    const id = indicator.split(":")[0];
    return VALUE_WINDOWS[indicator] ?? VALUE_WINDOWS[id] ?? { min: 0, max: 100, step };
  }
  return { min: lo ?? 1, max: hi != null && hi < 1e6 ? hi : 200, step };
}

function tag(id: string, params: Record<string, Value>, comps: Map<string, Component>): string {
  const ind = comps.get(id);
  const name = ind ? shortIndicator(ind) : id.toUpperCase();
  const extra = Object.entries(params)
    .filter(([k, v]) => k !== "output" && v !== "" && v != null)
    .map(([, v]) => v)
    .join(",");
  return extra ? `${name}(${extra})` : name;
}

function sentence(cond: CondDraft, comps: Map<string, Component>): string {
  const tagged = tag(cond.indicator, cond.indParams, comps);
  if (PAIRED.has(cond.kind)) {
    const other = tag(cond.other ?? "", cond.otherParams ?? {}, comps);
    return cond.kind === "indicator_cross"
      ? `${tagged} crosses ${cond.params.direction ?? ""} ${other}`
      : `${tagged} ${cond.params.op ?? ""} ${other}`;
  }
  if (cond.kind === "threshold") {
    return `${tagged} ${cond.params.op ?? ""} ${cond.params.value ?? ""}`.trim();
  }
  return `close crosses ${cond.params.direction ?? ""} ${tagged}`.trim();
}

function ChoicePills({
  p,
  value,
  onChange,
  testid,
}: {
  p: Param;
  value: Value;
  onChange: (v: Value) => void;
  testid: string;
}) {
  const label = shortLabel(p);
  return (
    <div className="choice-wrap">
      <div className="choice-pills" role="group" aria-label={label}>
        {p.choices?.map((c) => (
          <button key={c} type="button" className={String(value) === c ? "on" : undefined} onClick={() => onChange(c)}>
            {c}
          </button>
        ))}
      </div>
      <select
        className="cond-sr"
        data-testid={testid}
        aria-label={label}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
      >
        {(p.choices ?? []).map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    </div>
  );
}

export function ParamControl({
  p,
  value,
  onChange,
  testid,
  inputId,
  indicator,
  extra,
}: {
  p: Param;
  value: Value;
  onChange: (v: Value) => void;
  testid: string;
  inputId: string;
  indicator: string;
  extra?: ReactNode;
}) {
  const label = shortLabel(p);
  if (p.choices && p.choices.length) {
    return (
      <div className="cond-row">
        <span className="cond-label">{label}</span>
        <ChoicePills p={p} value={value} onChange={onChange} testid={testid} />
      </div>
    );
  }

  if (p.type === "float" && isFraction(p)) {
    const shown: Param = {
      ...p,
      label: p.label,
      min: typeof p.min === "number" ? +(p.min * 100).toFixed(6) : p.min,
      max: typeof p.max === "number" ? Math.min(+(p.max * 100).toFixed(6), 50) : p.max,
    };
    const n = asNum(value);
    return (
      <NumberControl
        p={shown}
        label={label}
        value={n == null ? value : +(n * 100).toFixed(6)}
        onChange={(v) => {
          const m = asNum(v);
          onChange(m == null ? v : +(m / 100).toFixed(8));
        }}
        testid={testid}
        inputId={inputId}
        indicator={indicator}
        extra={extra}
      />
    );
  }
  if (p.type === "int" || p.type === "float") {
    return (
      <NumberControl p={p} label={label} value={value} onChange={onChange} testid={testid} inputId={inputId} indicator={indicator} extra={extra} />
    );
  }

  return (
    <div className="cond-row">
      <label className="cond-label" htmlFor={inputId}>
        {label}
      </label>
      <input id={inputId} data-testid={testid} type="text" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function NumberControl({
  p,
  label,
  value,
  onChange,
  testid,
  inputId,
  indicator,
  extra,
}: {
  p: Param;
  label: string;
  value: Value;
  onChange: (v: Value) => void;
  testid: string;
  inputId: string;
  indicator: string;
  extra?: ReactNode;
}) {
  {
    const win = sliderWindow(p, indicator);
    const n = asNum(value);
    const slider = n == null ? win.min : Math.min(win.max, Math.max(win.min, n));
    return (
      <div className={`cond-row cond-slider${extra ? " cond-slider-extra" : ""}`}>
        <label className="cond-label" htmlFor={inputId}>
          {label}
        </label>
        <input
          type="range"
          min={win.min}
          max={win.max}
          step={win.step}
          value={slider}
          aria-label={`${label} slider`}
          onChange={(e) => {
            const raw = Number(e.target.value);
            onChange(p.type === "int" ? Math.round(raw) : raw);
          }}
        />
        <input
          id={inputId}
          data-testid={testid}
          type="number"
          inputMode="decimal"
          step={p.type === "int" ? 1 : "any"}
          min={p.min ?? undefined}
          max={p.max ?? undefined}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
        {extra}
      </div>
    );
  }
}

/** Every param of one registry component, as generated controls. */
export function SchemaParams({
  schema,
  values,
  onChange,
  prefix,
}: {
  schema: Component | undefined;
  values: Record<string, Value>;
  onChange: (v: Record<string, Value>) => void;
  prefix: string;
}) {
  return (
    <>
      {(schema?.params ?? []).map((p) => (
        <ParamControl
          key={`${schema?.id}-${p.name}`}
          p={p}
          value={values[p.name] ?? ""}
          testid={`${prefix}-${p.name}`}
          inputId={`${prefix}-${p.name}`}
          indicator={schema?.id ?? ""}
          onChange={(v) => onChange({ ...values, [p.name]: v })}
        />
      ))}
    </>
  );
}

/** Indicators that may sit on the other side of `main` (same units: both price-scale, or the same indicator). */
export function partnersOf(main: Component | undefined, indicators: Component[]): Component[] {
  if (!main) return [];
  return main.price_scale ? indicators.filter((c) => c.price_scale) : indicators.filter((c) => c.id === main.id);
}

export function ConditionEditor({
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
  const kinds = (Object.keys(KIND_LABEL) as CondKind[]).filter((k) => byId.has(k));
  const paired = PAIRED.has(cond.kind);
  const allowed = cond.kind === "price_cross" ? indicators.filter((c) => c.price_scale) : indicators;
  const ind = byId.get(cond.indicator);
  const kindSchema = byId.get(cond.kind);
  const partners = partnersOf(ind, indicators);
  const otherInd = byId.get(cond.other ?? "");
  const where = prefix === "condition" ? "Rule" : "Exit";

  /** Keep `other` valid for the main indicator; default to a second copy of it. */
  function withPartner(next: CondDraft): CondDraft {
    if (!PAIRED.has(next.kind)) return { ...next, other: undefined, otherParams: undefined };
    const ok = partnersOf(byId.get(next.indicator), indicators).some((c) => c.id === next.other);
    if (ok) return next;
    const other = byId.get(next.indicator);
    const otherParams = defaultsOf(other);
    // A different length makes the default pair meaningful (e.g. SMA 20 vs SMA 50).
    if (typeof otherParams.length === "number") otherParams.length = otherParams.length * 2 + 10;
    return { ...next, other: other?.id, otherParams };
  }

  function setKind(kind: CondKind) {
    let indicator = cond.indicator;
    let indParams = cond.indParams;
    if (kind === "price_cross" && !byId.get(indicator)?.price_scale) {
      indicator = indicators.find((c) => c.price_scale)?.id ?? indicator;
      indParams = defaultsOf(byId.get(indicator));
    }
    onChange(withPartner({ ...cond, kind, indicator, indParams, params: defaultsOf(byId.get(kind)) }));
  }

  function setIndicator(id: string) {
    onChange(withPartner({ ...cond, indicator: id, indParams: defaultsOf(byId.get(id)) }));
  }

  const pairChoice = paired ? kindSchema?.params.find((x) => x.choices && x.choices.length) : undefined;

  return (
    <div className="cond" data-testid={`${prefix}-${index}`}>
      <div className="cond-head">
        <strong>
          {where} {index + 1}
        </strong>
        <span className="cond-sentence" title={sentence(cond, byId)}>
          {sentence(cond, byId)}
        </span>
        <button className="link" type="button" onClick={onRemove} aria-label={`Remove condition ${index + 1}`}>
          Remove
        </button>
      </div>
      <div className="cond-toolbar">
        <select
          className="cond-kind"
          value={cond.kind}
          onChange={(e) => setKind(e.target.value as CondKind)}
          data-testid="kind-select"
          aria-label="Condition type"
        >
          {kinds.map((k) => (
            <option key={k} value={k} title={byId.get(k)?.description}>
              {KIND_LABEL[k]}
            </option>
          ))}
        </select>
        <select
          className="cond-indicator"
          value={cond.indicator}
          onChange={(e) => setIndicator(e.target.value)}
          data-testid="indicator-select"
          aria-label="Indicator"
        >
          {allowed.map((c) => (
            <option key={c.id} value={c.id} title={c.description}>
              {shortIndicator(c)}
            </option>
          ))}
        </select>
      </div>
      <div className="cond-params">
        {(ind?.params ?? []).map((p) => {
          const dir = cond.kind === "price_cross" ? kindSchema?.params.find((x) => x.name === "direction") : undefined;
          const firstNum = (ind?.params ?? []).find((x) => x.type === "int" || x.type === "float")?.name;
          return (
            <ParamControl
              key={`${cond.indicator}-${p.name}`}
              p={p}
              value={cond.indParams[p.name] ?? ""}
              testid={`param-${p.name}`}
              inputId={`${prefix}-${index}-param-${p.name}`}
              indicator={cond.indicator}
              extra={
                dir && p.name === firstNum ? (
                  <ChoicePills
                    p={dir}
                    value={cond.params.direction ?? ""}
                    testid="cross-direction"
                    onChange={(v) => onChange({ ...cond, params: { ...cond.params, direction: v } })}
                  />
                ) : undefined
              }
              onChange={(v) => onChange({ ...cond, indParams: { ...cond.indParams, [p.name]: v } })}
            />
          );
        })}
        {paired ? (
          <>
            {pairChoice ? (
              <ParamControl
                p={pairChoice}
                value={cond.params[pairChoice.name] ?? ""}
                testid={`pair-${pairChoice.name}`}
                inputId={`${prefix}-${index}-pair-${pairChoice.name}`}
                indicator={cond.indicator}
                onChange={(v) => onChange({ ...cond, params: { ...cond.params, [pairChoice.name]: v } })}
              />
            ) : null}
            <div className="cond-row">
              <label className="cond-label" htmlFor={`${prefix}-${index}-other`}>
                Than
              </label>
              <select
                id={`${prefix}-${index}-other`}
                className="cond-indicator"
                value={cond.other ?? ""}
                data-testid="other-select"
                aria-label="Second indicator"
                onChange={(e) => onChange({ ...cond, other: e.target.value, otherParams: defaultsOf(byId.get(e.target.value)) })}
              >
                {partners.map((c) => (
                  <option key={c.id} value={c.id} title={c.description}>
                    {shortIndicator(c)}
                  </option>
                ))}
              </select>
            </div>
            {(otherInd?.params ?? []).map((p) => (
              <ParamControl
                key={`other-${cond.other}-${p.name}`}
                p={p}
                value={cond.otherParams?.[p.name] ?? ""}
                testid={`other-param-${p.name}`}
                inputId={`${prefix}-${index}-other-param-${p.name}`}
                indicator={cond.other ?? ""}
                onChange={(v) => onChange({ ...cond, otherParams: { ...(cond.otherParams ?? {}), [p.name]: v } })}
              />
            ))}
            {!ind?.price_scale ? (
              <p className="help">
                {ind ? shortIndicator(ind) : "This indicator"} is not in price units, so it can only be compared with another {ind ? shortIndicator(ind) : "copy"}.
              </p>
            ) : null}
          </>
        ) : (
          (kindSchema?.params ?? []).map((p) => {
            if (p.name === "op" || p.name === "direction") return null;
            const testid = cond.kind === "threshold" ? `threshold-${p.name}` : `cross-${p.name}`;
            const op = kindSchema?.params.find((x) => x.name === "op");
            return (
              <ParamControl
                key={`${cond.kind}-${p.name}`}
                p={p}
                value={cond.params[p.name] ?? ""}
                testid={testid}
                inputId={`${prefix}-${index}-${testid}`}
                indicator={cond.indParams.output ? `${cond.indicator}:${cond.indParams.output}` : cond.indicator}
                extra={
                  p.name === "value" && op ? (
                    <ChoicePills
                      p={op}
                      value={cond.params.op ?? ""}
                      testid="threshold-op"
                      onChange={(v) => onChange({ ...cond, params: { ...cond.params, op: v } })}
                    />
                  ) : undefined
                }
                onChange={(v) => onChange({ ...cond, params: { ...cond.params, [p.name]: v } })}
              />
            );
          })
        )}
      </div>
    </div>
  );
}
