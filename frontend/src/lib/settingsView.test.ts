import assert from "node:assert/strict";
import test from "node:test";
import { differenceCount, settingsSections, settingsSummary } from "./settingsView.ts";

function threshold(value: number) {
  return {
    kind: "threshold",
    params: { op: "<", value },
    indicator: { id: "rsi", version: "1.0.0", params: { period: 14 } },
  };
}

function base(over: Record<string, unknown> = {}) {
  return {
    name: "RSI",
    universe: ["AAPL", "MSFT"],
    benchmark: "SPY",
    timeframe: "1d",
    start: "2018-01-02",
    end: "2024-12-31",
    entry_combine: "all",
    entry: [threshold(30)],
    entry_fill: { kind: "next_open", below_pct: null, above_pct: null, dip_pct: null },
    exit: { kind: "holding_period", version: "1.0.0", params: { sessions: 10 }, conditions: [] },
    exits: [{ kind: "holding_period", version: "1.0.0", params: { sessions: 10 }, conditions: [] }],
    sizing: { id: "percent_of_equity", version: "1.0.0", params: {} },
    rank_by: null,
    portfolio: {
      starting_cash: 100000,
      target_allocation: 0.1,
      max_positions: 5,
      cost_bps_per_side: 5,
      long_only: true,
      whole_shares: true,
      one_position_per_symbol: true,
    },
    execution: { engine_version: "0.4.0" },
    ...over,
  };
}

function row(sections: ReturnType<typeof settingsSections>, title: string, label: string) {
  const section = sections.find((s) => s.title === title);
  assert.ok(section, title);
  const found = section.rows.find((r) => r.label === label);
  assert.ok(found, `${title} / ${label}`);
  return found;
}

test("identical runs have seven sections and no shaded rows", () => {
  const sections = settingsSections(base(), base());
  assert.deepEqual(
    sections.map((s) => s.title),
    ["Setup", "Signal", "Entry", "Exit", "Size", "Positions", "Capital"],
  );
  assert.equal(differenceCount(sections), 0);
  assert.equal(settingsSummary(sections), "Settings match");
  assert.equal(row(sections, "Signal", "Condition 1").a, "rsi(period=14) < 30");
  assert.equal(row(sections, "Exit", "Hold cap").a, "Hold 10 sessions");
  assert.equal(row(sections, "Capital", "Cost per side").a, "5 bps");
  assert.equal(sections.find((s) => s.title === "Capital")?.rows.some((r) => r.label === "Long only"), false);
});

test("a changed entry threshold shades only that condition", () => {
  const sections = settingsSections(base(), base({ entry: [threshold(35)] }));
  const condition = row(sections, "Signal", "Condition 1");
  assert.equal(condition.a, "rsi(period=14) < 30");
  assert.equal(condition.b, "rsi(period=14) < 35");
  assert.equal(condition.changed, true);
  assert.equal(row(sections, "Setup", "Name").changed, false);
  assert.equal(differenceCount(sections), 1);
  assert.equal(settingsSummary(sections), "Settings · 1 difference");
});

test("a condition only one run has shows none on the other side", () => {
  const sections = settingsSections(base(), base({ entry: [threshold(30), threshold(40)] }));
  const extra = row(sections, "Signal", "Condition 2");
  assert.equal(extra.a, "none");
  assert.equal(extra.b, "rsi(period=14) < 40");
  assert.equal(extra.changed, true);
});

test("names are shown and shaded when they differ", () => {
  const name = row(settingsSections(base(), base({ name: "RSI wider" })), "Setup", "Name");
  assert.equal(name.a, "RSI");
  assert.equal(name.b, "RSI wider");
  assert.equal(name.changed, true);
});

test("older runs missing entry fill, exits, and sizing match the defaults", () => {
  const old = base();
  delete (old as { entry_fill?: unknown }).entry_fill;
  delete (old as { exits?: unknown }).exits;
  delete (old as { sizing?: unknown }).sizing;
  delete (old as { entry_combine?: unknown }).entry_combine;
  const sections = settingsSections(old, base());
  assert.equal(row(sections, "Entry", "Fill").a, "Next open");
  assert.equal(row(sections, "Entry", "Fill").changed, false);
  assert.equal(row(sections, "Signal", "Combine").a, "All conditions must pass");
  assert.equal(row(sections, "Signal", "Combine").changed, false);
  assert.equal(row(sections, "Size", "Method").a, "Percent of equity");
  assert.equal(row(sections, "Size", "Method").changed, false);
  assert.equal(row(sections, "Exit", "Hold cap").changed, false);
  assert.equal(differenceCount(sections), 0);
});

test("a dip against next open shows the dip percent on one side only", () => {
  const dip = base({
    entry_fill: { kind: "dip", below_pct: null, above_pct: null, dip_pct: 0.02 },
  });
  const sections = settingsSections(base(), dip);
  assert.equal(row(sections, "Entry", "Fill").b, "Dip");
  const pct = row(sections, "Entry", "Dip");
  assert.equal(pct.a, "none");
  assert.equal(pct.b, "2.00%");
  assert.equal(pct.changed, true);
  assert.equal(sections.find((s) => s.title === "Entry")?.rows.some((r) => r.label === "Below"), false);
});

test("a stop is one price-exit row and is not a hold cap", () => {
  const stop = {
    kind: "stop_loss",
    version: "1.0.0",
    params: { pct: 0.08 },
    conditions: [],
  };
  const wider = { ...stop, params: { pct: 0.1 } };
  const sections = settingsSections(
    base({ exit: stop, exits: [stop] }),
    base({ exit: wider, exits: [wider] }),
  );
  const price = row(sections, "Exit", "Price exit 1");
  assert.equal(price.a, "Stop 8.00%");
  assert.equal(price.b, "Stop 10.00%");
  assert.equal(price.changed, true);
  assert.equal(row(sections, "Exit", "Hold cap").a, "No hold cap");
  assert.equal(sections.find((s) => s.title === "Exit")?.rows.some((r) => r.a.includes("holding")), false);
});

test("a signal exit lists its condition and an empty hold cap", () => {
  const signal = {
    kind: "signal",
    version: "1.0.0",
    params: {},
    conditions: [
      {
        kind: "threshold",
        params: { op: ">", value: 70 },
        indicator: { id: "rsi", params: { period: 14 } },
      },
    ],
  };
  const sections = settingsSections(base(), base({ exit: signal, exits: [signal] }));
  const cond = row(sections, "Exit", "Exit condition 1");
  assert.equal(cond.a, "none");
  assert.equal(cond.b, "rsi(period=14) > 70");
  assert.equal(cond.changed, true);
  assert.equal(row(sections, "Exit", "Hold cap").b, "No hold cap");
});

test("ranking, equity cap, and a differing fixed flag", () => {
  const ranked = base({
    rank_by: {
      indicator: { id: "rsi", params: { period: 14 } },
      order: "ascending",
    },
    portfolio: {
      ...base().portfolio,
      long_only: false,
    },
  });
  const sections = settingsSections(base(), ranked);
  assert.equal(row(sections, "Positions", "Ranking").a, "By symbol, A to Z");
  assert.equal(row(sections, "Positions", "Ranking").b, "rsi(period=14), Lowest first");
  assert.equal(row(sections, "Size", "Equity cap").a, "10.00%");
  assert.equal(row(sections, "Size", "Equity cap").changed, false);
  const flag = row(sections, "Capital", "Long only");
  assert.equal(flag.a, "yes");
  assert.equal(flag.b, "no");
  assert.equal(flag.changed, true);
});

test("a watchlist is shown only when a run has one", () => {
  const withList = base({ watchlist: { name: "tech", version: 3, label: "Tech", members: ["AAPL"], created_at: "2024-01-01" } });
  const shown = row(settingsSections(base(), withList), "Setup", "Watchlist");
  assert.equal(shown.a, "none");
  assert.equal(shown.b, "tech v3");
  assert.equal(shown.changed, true);
  assert.equal(settingsSections(base(), base()).some((s) => s.rows.some((r) => r.label === "Watchlist")), false);
});

test("an entry signal is listed on Entry and stays off Signal", () => {
  const extra = {
    kind: "threshold",
    params: { op: ">", value: 70 },
    indicator: { id: "rsi", params: { period: 14 } },
  };
  const withSignal = base({
    entry: [...base().entry, extra],
    entry_signals: [extra],
  });
  const sections = settingsSections(base(), withSignal);
  assert.equal(row(sections, "Signal", "Condition 1").changed, false);
  assert.equal(
    sections.find((s) => s.title === "Signal")?.rows.some((r) => r.label === "Condition 2"),
    false,
  );
  assert.equal(row(sections, "Entry", "Entry signal 1").a, "none");
  assert.equal(row(sections, "Entry", "Entry signal 1").b, "rsi(period=14) > 70");
});

test("an unreadable condition falls back to JSON", () => {
  const broken = base({ entry: [{ kind: "nope" }] });
  const sections = settingsSections(broken, broken);
  assert.equal(row(sections, "Signal", "Condition 1").a, '{"kind":"nope"}');
  assert.equal(differenceCount(sections), 0);
});
