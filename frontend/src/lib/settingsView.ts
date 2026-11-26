export type SettingRow = {
  label: string;
  a: string;
  b: string;
  changed: boolean;
};

export type SettingSection = {
  title: string;
  rows: SettingRow[];
};

type Cell = { text: string; token: string };

const NONE: Cell = { text: "none", token: "none" };
const UNSET: Cell = { text: "(not set)", token: "unset" };

const FILL_LABEL: Record<string, string> = {
  next_open: "Next open",
  next_range: "In range",
  dip: "Dip",
};

const EXIT_LABEL: Record<string, string> = {
  stop_loss: "Stop",
  take_profit: "Take profit",
  trailing_stop: "Trailing stop",
};

const SIZE_LABEL: Record<string, string> = {
  percent_of_equity: "Percent of equity",
  fixed_dollar: "Fixed dollar",
  volatility: "Volatility",
};

const PARAM_LABEL: Record<string, string> = {
  amount: "Amount",
  risk_fraction: "Risk fraction",
  atr_length: "ATR length",
  atr_multiple: "ATR multiple",
  pct: "Percent",
};

const FLAGS: [string, string][] = [
  ["long_only", "Long only"],
  ["whole_shares", "Whole shares"],
  ["one_position_per_symbol", "One position per symbol"],
];

function isRecord(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function asRecord(v: unknown): Record<string, unknown> {
  return isRecord(v) ? v : {};
}

function snippet(v: unknown): string {
  let text: string;
  try {
    text = JSON.stringify(v);
  } catch {
    text = String(v);
  }
  if (!text) return "(not set)";
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

function atom(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) return Number.isInteger(v) ? String(v) : String(v);
  if (typeof v === "string" || typeof v === "boolean") return String(v);
  return snippet(v);
}

function numCell(v: unknown, text: string): Cell {
  return typeof v === "number" && Number.isFinite(v) ? { text, token: `n:${v}` } : UNSET;
}

function percentCell(v: unknown): Cell {
  if (typeof v !== "number" || !Number.isFinite(v)) return UNSET;
  return numCell(v, `${(v * 100).toFixed(2)}%`);
}

function moneyCell(v: unknown): Cell {
  if (typeof v !== "number" || !Number.isFinite(v)) return UNSET;
  const text = `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return numCell(v, text);
}

function plainNum(v: unknown): Cell {
  if (typeof v !== "number" || !Number.isFinite(v)) return UNSET;
  return numCell(v, Number.isInteger(v) ? v.toLocaleString("en-US") : String(v));
}

function textCell(text: string, token = text): Cell {
  return { text, token };
}

function boolCell(v: unknown): Cell {
  if (v === true) return { text: "yes", token: "y" };
  if (v === false) return { text: "no", token: "n" };
  return UNSET;
}

function pair(label: string, a: Cell, b: Cell): SettingRow {
  return { label, a: a.text, b: b.text, changed: a.token !== b.token };
}

function zip(label: (i: number) => string, a: Cell[], b: Cell[]): SettingRow[] {
  const n = Math.max(a.length, b.length);
  const rows: SettingRow[] = [];
  for (let i = 0; i < n; i++) rows.push(pair(label(i), a[i] ?? NONE, b[i] ?? NONE));
  return rows;
}

function indicatorKey(ind: unknown): string | null {
  if (!isRecord(ind) || typeof ind.id !== "string") return null;
  const params = isRecord(ind.params) ? ind.params : {};
  const args = Object.keys(params)
    .sort()
    .map((key) => `${key}=${atom(params[key])}`)
    .join(",");
  return `${ind.id}(${args})`;
}

function conditionCell(v: unknown): Cell {
  if (!isRecord(v)) return textCell(snippet(v), `j:${snippet(v)}`);
  const ind = indicatorKey(v.indicator);
  const params = isRecord(v.params) ? v.params : {};
  let text: string | null = null;
  if (v.kind === "threshold" && ind) text = `${ind} ${atom(params.op)} ${atom(params.value)}`;
  if (v.kind === "price_cross" && ind) text = `close crosses ${atom(params.direction)} ${ind}`;
  if (v.kind === "indicator_compare" && ind && indicatorKey(v.other)) {
    text = `${ind} ${atom(params.op)} ${indicatorKey(v.other)}`;
  }
  if (v.kind === "indicator_cross" && ind && indicatorKey(v.other)) {
    text = `${ind} crosses ${atom(params.direction)} ${indicatorKey(v.other)}`;
  }
  if (!text) {
    const raw = snippet(v);
    return textCell(raw, `j:${raw}`);
  }
  return textCell(text);
}

function list(v: unknown): unknown[] {
  if (Array.isArray(v)) return v;
  if (v == null) return [];
  return [v];
}

function exitsOf(resolved: Record<string, unknown>): Record<string, unknown>[] {
  if (Array.isArray(resolved.exits) && resolved.exits.length) return resolved.exits.map(asRecord);
  if (isRecord(resolved.exit)) return [resolved.exit];
  return [];
}

function fillOf(resolved: Record<string, unknown>): Record<string, unknown> {
  if (!isRecord(resolved.entry_fill)) return { kind: "next_open" };
  return resolved.entry_fill;
}

function sizingOf(resolved: Record<string, unknown>): { id: string; params: Record<string, unknown> } {
  if (!isRecord(resolved.sizing)) return { id: "percent_of_equity", params: {} };
  return {
    id: typeof resolved.sizing.id === "string" ? resolved.sizing.id : "",
    params: isRecord(resolved.sizing.params) ? resolved.sizing.params : {},
  };
}

function holdCell(exits: Record<string, unknown>[]): Cell {
  if (!exits.length) return UNSET;
  const hold = exits.find((ex) => ex.kind === "holding_period");
  if (hold) {
    const n = asRecord(hold.params).sessions;
    if (typeof n !== "number") return UNSET;
    return textCell(`Hold ${n} session${n === 1 ? "" : "s"}`, `hold:${n}`);
  }
  const signal = exits.find((ex) => ex.kind === "signal");
  if (signal) {
    const n = asRecord(signal.params).max_sessions;
    if (n == null || n === "") return textCell("No hold cap", "hold:none");
    if (typeof n !== "number") return UNSET;
    return textCell(`Hold ${n} session${n === 1 ? "" : "s"}`, `hold:${n}`);
  }
  return textCell("No hold cap", "hold:none");
}

function priceCell(ex: Record<string, unknown>): Cell {
  const kind = typeof ex.kind === "string" ? ex.kind : "";
  const name = EXIT_LABEL[kind] ?? (kind ? kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "Exit");
  const params = asRecord(ex.params);
  if (typeof params.pct === "number") {
    return textCell(`${name} ${((params.pct as number) * 100).toFixed(2)}%`, `exit:${kind}:${params.pct}`);
  }
  const raw = Object.keys(params).length ? ` ${snippet(params)}` : "";
  return textCell(`${name}${raw}`, `exit:${kind}:${snippet(params)}`);
}

function paramCell(key: string, v: unknown): Cell {
  if (v === undefined) return NONE;
  if (/pct|fraction|allocation/i.test(key) && typeof v === "number" && Math.abs(v) <= 1) return percentCell(v);
  if (key === "amount") return moneyCell(v);
  if (typeof v === "number") return plainNum(v);
  if (typeof v === "string") return textCell(v);
  return textCell(snippet(v), `j:${snippet(v)}`);
}

function rankCell(rank: unknown): Cell {
  if (rank == null) return textCell("By symbol, A to Z", "rank:symbol");
  if (!isRecord(rank)) return textCell(snippet(rank), `j:${snippet(rank)}`);
  const key = indicatorKey(rank.indicator);
  const order = rank.order === "descending" ? "Highest first" : rank.order === "ascending" ? "Lowest first" : atom(rank.order);
  if (!key) return textCell(snippet(rank), `j:${snippet(rank)}`);
  return textCell(`${key}, ${order}`, `rank:${key}:${String(rank.order)}`);
}

function watchCell(w: unknown): Cell {
  if (!isRecord(w)) return NONE;
  const name = typeof w.name === "string" ? w.name : "";
  const version = typeof w.version === "number" ? w.version : null;
  const text = version == null ? name || snippet(w) : `${name} v${version}`;
  return textCell(text, `wl:${name}:${version ?? ""}`);
}

function stringCell(v: unknown): Cell {
  if (typeof v !== "string" || v === "") return UNSET;
  return textCell(v);
}

function symbolsCell(v: unknown): Cell {
  if (!Array.isArray(v)) return UNSET;
  const names = v.map((item) => String(item));
  return textCell(names.join(", "), `syms:${names.join(",")}`);
}

function combineCell(v: unknown): Cell {
  if (v == null || v === "all") return textCell("All conditions must pass", "combine:all");
  if (typeof v === "string") return textCell(v, `combine:${v}`);
  return textCell(snippet(v), `j:${snippet(v)}`);
}

function fillKindCell(fill: Record<string, unknown>): Cell {
  const kind = typeof fill.kind === "string" ? fill.kind : "next_open";
  return textCell(FILL_LABEL[kind] ?? kind, `fill:${kind}`);
}

function fillPercent(fill: Record<string, unknown>, kind: string, key: string): Cell {
  if (fill.kind !== kind) return NONE;
  return percentCell(fill[key]);
}

function setup(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const rows = [
    pair("Name", stringCell(a.name), stringCell(b.name)),
    pair("Symbols", symbolsCell(a.universe), symbolsCell(b.universe)),
    pair("Benchmark", stringCell(a.benchmark), stringCell(b.benchmark)),
    pair("Timeframe", stringCell(a.timeframe), stringCell(b.timeframe)),
    pair("First session", stringCell(a.start), stringCell(b.start)),
    pair("Last session", stringCell(a.end), stringCell(b.end)),
  ];
  if (a.watchlist != null || b.watchlist != null) {
    rows.push(pair("Watchlist", watchCell(a.watchlist), watchCell(b.watchlist)));
  }
  return { title: "Setup", rows };
}

/** Entry-row signals are stored again at the end of `entry`. The signal section is the rest. */
function signalOnly(resolved: Record<string, unknown>): unknown[] {
  const all = list(resolved.entry);
  const extra = list(resolved.entry_signals);
  if (!extra.length || extra.length > all.length) return all;
  return all.slice(0, all.length - extra.length);
}

function signal(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const left = signalOnly(a).map(conditionCell);
  const right = signalOnly(b).map(conditionCell);
  return {
    title: "Signal",
    rows: [pair("Combine", combineCell(a.entry_combine), combineCell(b.entry_combine)), ...zip((i) => `Condition ${i + 1}`, left, right)],
  };
}

function entry(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const fa = fillOf(a);
  const fb = fillOf(b);
  const rows = [pair("Fill", fillKindCell(fa), fillKindCell(fb))];
  if (fa.kind === "dip" || fb.kind === "dip") {
    rows.push(pair("Dip", fillPercent(fa, "dip", "dip_pct"), fillPercent(fb, "dip", "dip_pct")));
  }
  if (fa.kind === "next_range" || fb.kind === "next_range") {
    rows.push(pair("Below", fillPercent(fa, "next_range", "below_pct"), fillPercent(fb, "next_range", "below_pct")));
    rows.push(pair("Above", fillPercent(fa, "next_range", "above_pct"), fillPercent(fb, "next_range", "above_pct")));
  }
  rows.push(
    ...zip(
      (i) => `Entry signal ${i + 1}`,
      list(a.entry_signals).map(conditionCell),
      list(b.entry_signals).map(conditionCell),
    ),
  );
  return { title: "Entry", rows };
}

function exitSection(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const ea = exitsOf(a);
  const eb = exitsOf(b);
  const price = (exits: Record<string, unknown>[]) =>
    exits.filter((ex) => ex.kind !== "signal" && ex.kind !== "holding_period").map(priceCell);
  const conditions = (exits: Record<string, unknown>[]) =>
    exits.filter((ex) => ex.kind === "signal").flatMap((ex) => list(ex.conditions).map(conditionCell));
  return {
    title: "Exit",
    rows: [
      ...zip((i) => `Price exit ${i + 1}`, price(ea), price(eb)),
      ...zip((i) => `Exit condition ${i + 1}`, conditions(ea), conditions(eb)),
      pair("Hold cap", holdCell(ea), holdCell(eb)),
    ],
  };
}

function size(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const sa = sizingOf(a);
  const sb = sizingOf(b);
  const pa = asRecord(a.portfolio);
  const pb = asRecord(b.portfolio);
  const rows = [
    pair(
      "Method",
      textCell(SIZE_LABEL[sa.id] ?? (sa.id || "(not set)"), `size:${sa.id}`),
      textCell(SIZE_LABEL[sb.id] ?? (sb.id || "(not set)"), `size:${sb.id}`),
    ),
  ];
  const keys = [...new Set([...Object.keys(sa.params), ...Object.keys(sb.params)])].sort();
  for (const key of keys) {
    const label = PARAM_LABEL[key] ?? key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    rows.push(pair(label, paramCell(key, sa.params[key]), paramCell(key, sb.params[key])));
  }
  rows.push(pair("Equity cap", percentCell(pa.target_allocation), percentCell(pb.target_allocation)));
  return { title: "Size", rows };
}

function positions(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const pa = asRecord(a.portfolio);
  const pb = asRecord(b.portfolio);
  return {
    title: "Positions",
    rows: [
      pair("Max positions", plainNum(pa.max_positions), plainNum(pb.max_positions)),
      pair("Ranking", rankCell(a.rank_by), rankCell(b.rank_by)),
    ],
  };
}

function capital(a: Record<string, unknown>, b: Record<string, unknown>): SettingSection {
  const pa = asRecord(a.portfolio);
  const pb = asRecord(b.portfolio);
  const rows = [
    pair("Starting cash", moneyCell(pa.starting_cash), moneyCell(pb.starting_cash)),
    pair(
      "Cost per side",
      typeof pa.cost_bps_per_side === "number" ? numCell(pa.cost_bps_per_side, `${atom(pa.cost_bps_per_side)} bps`) : UNSET,
      typeof pb.cost_bps_per_side === "number" ? numCell(pb.cost_bps_per_side, `${atom(pb.cost_bps_per_side)} bps`) : UNSET,
    ),
  ];
  for (const [key, label] of FLAGS) {
    const left = boolCell(pa[key]);
    const right = boolCell(pb[key]);
    if (left.token !== right.token) rows.push(pair(label, left, right));
  }
  return { title: "Capital", rows };
}

export function settingsSections(a: unknown, b: unknown): SettingSection[] {
  const left = asRecord(a);
  const right = asRecord(b);
  return [setup(left, right), signal(left, right), entry(left, right), exitSection(left, right), size(left, right), positions(left, right), capital(left, right)];
}

export function differenceCount(sections: SettingSection[]): number {
  return sections.reduce((sum, section) => sum + section.rows.filter((row) => row.changed).length, 0);
}

export function settingsSummary(sections: SettingSection[]): string {
  const n = differenceCount(sections);
  if (n === 0) return "Settings match";
  if (n === 1) return "Settings · 1 difference";
  return `Settings · ${n} differences`;
}
