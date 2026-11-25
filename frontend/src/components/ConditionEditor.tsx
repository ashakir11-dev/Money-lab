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
  /** The second indicator, for indicator_compare and indicator_cross. */
  other?: string;
  otherParams?: Record<string, Value>;
};

export const PAIRED_KINDS: CondKind[] = ["indicator_compare", "indicator_cross"];
export const isPaired = (k: CondKind) => PAIRED_KINDS.includes(k);

const KIND_LABEL: Record<CondKind, string> = {
  threshold: "Level",
  price_cross: "Price cross",
  indicator_compare: "Compare two",
  indicator_cross: "Two-line cross",
};

const IND_SHORT: Record<string, string> = {
  sma: "SMA",
  rsi: "RSI",
  macd: "MACD",
  breadth: "Breadth",
  ema: "EMA",
  atr: "ATR",
  roc: "ROC",
  bollinger_pctb: "Bollinger %B",
  highest_high: "Highest high",
  lowest_low: "Lowest low",
  avg_volume: "Avg volume",
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
  pct: "Percent %",
  risk_fraction: "Risk %",
  atr_length: "ATR length",
  atr_multiple: "Stop × ATR",
  amount: "Amount $",
};

export const defaultsOf = (c: Component | undefined): Record<string, Value> =>
  Object.fromEntries((c?.params ?? []).map((p) => [p.name, (p.default as Value) ?? ""]));

export function shortIndicator(c: Component): string {
  return IND_SHORT[c.id] ?? c.label;
}

function shortLabel(p: Param): string {
  return PARAM_SHORT[p.name] ?? (p.label.replace(/\s*\([^)]*\)/g, "").trim() || p.name);
}

function asNum(v: Value): number | null {
  if (v === "" || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Practical slider window. Schema min/max stay on the typed field. */
function sliderWindow(p: Param, indicator: string): { min: number; max: number; step: number } {
  const step = p.type === "int" ? 1 : 0.01;
  const lo = typeof p.min === "number" ? p.min : null;
  const hi = typeof p.max === "number" ? p.max : null;
  if (lo != null && hi != null && hi - lo <= 500 && Math.abs(hi) < 1e6) {
    return { min: lo, max: hi, step };
  }
  if (p.name === "value") {
    if (indicator === "rsi") return { min: 0, max: 100, step: 1 };
    if (indicator === "breadth") return { min: 0, max: 1, step: 0.01 };
    return { min: 0, max: 100, step };
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

/** Indicators that can be paired with `primary`: two price-unit lines, or the same indicator. */
export function pairable(primary: Component | undefined, indicators: Component[]): Component[] {
  if (!primary) return indicators;
  return primary.price_scale ? indicators.filter((c) => c.price_scale) : indicators.filter((c) => c.id === primary.id);
}

function sentence(cond: CondDraft, comps: Map<string, Component>): string {
  const tagged = tag(cond.indicator, cond.indParams, comps);
  if (isPaired(cond.kind)) {
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

  if (p.type === "int" || p.type === "float") {
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

  return (
    <div className="cond-row">
      <label className="cond-label" htmlFor={inputId}>
        {label}
      </label>
      <input id={inputId} data-testid={testid} type="text" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
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
  const allowed = cond.kind === "price_cross" ? indicators.filter((c) => c.price_scale) : indicators;
  const ind = byId.get(cond.indicator);
  const kindSchema = byId.get(cond.kind);
  const where = prefix === "condition" ? "Rule" : "Exit";
  const paired = isPaired(cond.kind);
  const otherChoices = pairable(ind, indicators);
  const otherInd = byId.get(cond.other ?? "");
  const kinds = (Object.keys(KIND_LABEL) as CondKind[]).filter((k) => {
    const c = byId.get(k);
    return c && c.uses.includes(prefix === "condition" ? "entry" : "exit");
  });

  /** A second line for a paired condition: a different price-unit line, or the same indicator. */
  function defaultOther(primary: Component | undefined): Pick<CondDraft, "other" | "otherParams"> {
    const choices = pairable(primary, indicators);
    const pick = choices.find((c) => c.id !== primary?.id) ?? primary ?? choices[0];
    const params = defaultsOf(pick);
    if (pick && pick.id === primary?.id) {
      const len = pick.params.find((p) => p.name === "length");
      if (len && typeof params.length === "number") params.length = params.length * 2;
    }
    return { other: pick?.id, otherParams: params };
  }

  function setKind(kind: CondKind) {
    let indicator = cond.indicator;
    let indParams = cond.indParams;
    if ((kind === "price_cross" || isPaired(kind)) && !byId.get(indicator)?.price_scale) {
      indicator = indicators.find((c) => c.price_scale)?.id ?? indicator;
      indParams = defaultsOf(byId.get(indicator));
    }
    const base: CondDraft = { kind, indicator, indParams, params: defaultsOf(byId.get(kind)) };
    onChange(isPaired(kind) ? { ...base, ...defaultOther(byId.get(indicator)) } : base);
  }

  function setIndicator(id: string) {
    const next: CondDraft = { ...cond, indicator: id, indParams: defaultsOf(byId.get(id)) };
    if (paired && !pairable(byId.get(id), indicators).some((c) => c.id === cond.other)) {
      Object.assign(next, defaultOther(byId.get(id)));
    }
    onChange(next);
  }

  function setOther(id: string) {
    onChange({ ...cond, other: id, otherParams: defaultsOf(byId.get(id)) });
  }

  const pairPill = paired ? kindSchema?.params.find((x) => x.name === "op" || x.name === "direction") : undefined;

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
        {paired ? (
          <>
            <span className="muted small">{cond.kind === "indicator_cross" ? "crosses" : "vs"}</span>
            <select
              className="cond-indicator"
              value={cond.other ?? ""}
              onChange={(e) => setOther(e.target.value)}
              data-testid="other-indicator-select"
              aria-label="Second indicator"
            >
              {otherChoices.map((c) => (
                <option key={c.id} value={c.id} title={c.description}>
                  {shortIndicator(c)}
                </option>
              ))}
            </select>
          </>
        ) : null}
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
        {pairPill ? (
          <div className="cond-row">
            <span className="cond-label">When</span>
            <ChoicePills
              p={pairPill}
              value={cond.params[pairPill.name] ?? ""}
              testid={`pair-${pairPill.name}`}
              onChange={(v) => onChange({ ...cond, params: { ...cond.params, [pairPill.name]: v } })}
            />
          </div>
        ) : null}
        {paired
          ? (otherInd?.params ?? []).map((p) => (
              <ParamControl
                key={`other-${cond.other}-${p.name}`}
                p={{ ...p, name: `other_${p.name}`, label: `2nd ${shortLabel(p).toLowerCase()}` }}
                value={cond.otherParams?.[p.name] ?? ""}
                testid={`other-param-${p.name}`}
                inputId={`${prefix}-${index}-other-param-${p.name}`}
                indicator={cond.other ?? ""}
                onChange={(v) => onChange({ ...cond, otherParams: { ...(cond.otherParams ?? {}), [p.name]: v } })}
              />
            ))
          : null}
        {(kindSchema?.params ?? []).map((p) => {
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
              indicator={cond.indicator}
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
        })}
      </div>
    </div>
  );
}
