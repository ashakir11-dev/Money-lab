import type { Analysis, RunMetrics } from "@/lib/api";
import { int, money, num, pct, pp, sharpe, versusSpy, versusSpyDollars } from "@/lib/format";

export type Tone = "pos" | "neg";

export type MetricStat = {
  label: string;
  value: string;
  meaning: string;
  tone?: Tone;
};

export type SideMetrics = {
  benchName: string;
  m: RunMetrics;
  b: NonNullable<RunMetrics["benchmark"]>;
  pnl: { total: number; vsBenchmark: number | null; realized: number; unrealized: number };
  inMkt: number | null;
  /** The run's research analysis (null for a run whose snapshot is gone). */
  an?: Analysis | null;
};

export type PairedMetric = {
  key: string;
  label: string;
  meaning: string;
  aText: string;
  bText: string;
  delta: string;
  toneA?: Tone;
  toneB?: Tone;
  changed: boolean;
};

const RETURN =
  "How much the account grew or shrank from the first day of the run to the last, compared with what it was worth at the start. 10% means the account ended one tenth larger than it began.";
const SPY_RETURN =
  "What buying SPY, the S&P 500 ETF, and holding it would have made over this same run, with the same starting cash and trading costs. This is the S&P result these comparisons use.";
const VERSUS_SPY =
  "Whether this run beat the S&P 500. Beat SPY means the account's percent return finished above buy-and-hold SPY. Trailed SPY means it finished below. The gap is in percentage points: 12% against SPY's 9% is Beat SPY by 3.00 pp.";
const DRAWDOWN =
  "The deepest fall from a high in the account to a later low, measured against that high. −20% means that, at the worst moment, the account was a fifth below an earlier peak. This is the worst dip along the way, not the result at the end. A number closer to zero is a shallower dip.";
const SHARPE =
  "A score for how much the account gained for the amount it jumped around from bar to bar. Higher means more gain for the amount of bouncing. It uses each bar's percent change in account value (a bar is one day on daily runs, one hour on hourly runs, and so on), treats cash as earning nothing, and scales the score to a full year. It reads \"undefined\" when those changes never vary, because there is no bouncing to measure.";
const PNL =
  "The ending account value minus the cash the run started with. This is the return, written in dollars instead of a percent.";
const VERSUS_SPY_DOLLARS =
  "The same S&P comparison in dollars: this run's profit minus the profit from buying SPY and holding it with the same starting cash. Beat SPY means the account finished ahead of the S&P in dollars.";
const REALIZED =
  "Dollars already locked in from shares that were bought and later sold, after the trading costs on those buys and sells. This number does not change once the sale is done.";
const UNREALIZED =
  "Dollars still at stake in shares that have not been sold. It is what those shares are worth now, minus what was paid for them, including the cost of buying them. It keeps moving until the shares are sold.";
const COSTS =
  "Trading costs charged on the buys and sells in this run. They have already been taken out of profit and loss, the return, and the final account value.";
const FINAL =
  "What the account was worth on the last day: leftover cash plus the value of any shares still held.";
const TRIPS =
  "How many times a position was bought and then fully sold. A position still open at the end is not included.";
const EXPOSURE =
  "On a typical day, how much of the account was in stocks rather than cash. 100% means the account was fully invested. 0% means it sat entirely in cash.";
const OPEN =
  "How many buys were still unmatched by a sell when the run ended.";
const MIXED_BENCH =
  "Each column uses the benchmark saved with that run. SPY is the S&P 500 ETF. A benchmark with another name was not scored against the S&P.";

function toneOf(n: number | null | undefined): Tone | undefined {
  if (typeof n !== "number") return undefined;
  return n >= 0 ? "pos" : "neg";
}

function gap(side: SideMetrics): number | null {
  return typeof side.m.total_return === "number" && typeof side.b.total_return === "number"
    ? side.m.total_return - side.b.total_return
    : null;
}

function versusMeaning(name: string): string {
  return name === "SPY"
    ? VERSUS_SPY
    : `Whether this run beat ${name}, the benchmark saved with this run. This run was not scored against SPY, so this line is not an S&P result.`;
}

function versusDollarsMeaning(name: string): string {
  return name === "SPY"
    ? VERSUS_SPY_DOLLARS
    : `This run's profit minus the profit from buying ${name} and holding it. This run was not scored against SPY.`;
}

function drawdownMeaning(name: string): string {
  return `The same deepest-fall measure for ${name}, which stays invested the whole time. Set it next to this run's max drawdown to see which path fell harder from its own peak.`;
}

function timeMeaning(name: string): string {
  return `The share of days the account held at least one stock. ${name} is invested every day, so a run that spends time in cash is in the market less often than ${name}.`;
}

/** The report's metric list, one run. */
export function metricStats(side: SideMetrics): MetricStat[] {
  const { benchName, m, b, pnl, inMkt } = side;
  const g = gap(side);
  return [
    { label: "Return", value: pct(m.total_return), tone: toneOf(m.total_return), meaning: RETURN },
    ...(benchName === "SPY"
      ? [{ label: "SPY return", value: pct(b.total_return), tone: toneOf(b.total_return), meaning: SPY_RETURN }]
      : []),
    {
      label: benchName === "SPY" ? "Versus the S&P" : `Versus ${benchName}`,
      value: versusSpy(m.total_return, b.total_return, benchName),
      tone: toneOf(g),
      meaning: versusMeaning(benchName),
    },
    { label: "Max drawdown", value: pct(m.max_drawdown), meaning: DRAWDOWN },
    { label: `${benchName} drawdown`, value: pct(b.max_drawdown), meaning: drawdownMeaning(benchName) },
    { label: "Sharpe", value: sharpe(m.sharpe), meaning: SHARPE },
    { label: "Profit and loss", value: money(pnl.total), tone: toneOf(pnl.total), meaning: PNL },
    {
      label: benchName === "SPY" ? "Versus the S&P in dollars" : `Versus ${benchName} in dollars`,
      value: versusSpyDollars(pnl.vsBenchmark, benchName),
      tone: toneOf(pnl.vsBenchmark),
      meaning: versusDollarsMeaning(benchName),
    },
    { label: "Realized", value: money(pnl.realized), tone: toneOf(pnl.realized), meaning: REALIZED },
    { label: "Unrealized", value: money(pnl.unrealized), tone: toneOf(pnl.unrealized), meaning: UNREALIZED },
    { label: "Costs", value: money(m.costs), meaning: COSTS },
    { label: "Final equity", value: money(m.final_equity), meaning: FINAL },
    { label: "Round trips", value: int(m.trades), meaning: TRIPS },
    { label: "Average exposure", value: pct(m.avg_exposure), meaning: EXPOSURE },
    { label: "Time in market", value: pct(inMkt), meaning: timeMeaning(benchName) },
    { label: "Open positions", value: int(m.open_positions ?? 0), meaning: OPEN },
  ];
}

function signedNum(v: number): string {
  const body = num(v);
  return v > 0 ? `+${body}` : body;
}

function signedInt(v: number): string {
  const body = int(v);
  return v > 0 ? `+${body}` : body;
}

function signedMoney(v: number): string {
  const body = money(Math.abs(v));
  if (v > 0) return `+${body}`;
  if (v < 0) return `−${body}`;
  return body;
}

function bothNumbers(x: number | null | undefined, y: number | null | undefined): [number, number] | null {
  return typeof x === "number" && typeof y === "number" ? [x, y] : null;
}

function row(partial: Omit<PairedMetric, "changed">): PairedMetric {
  return { ...partial, changed: partial.aText !== partial.bText };
}

/** The same report metrics for two runs, with B − A where the numbers subtract. */
export function pairedMetrics(a: SideMetrics, b: SideMetrics): PairedMetric[] {
  const bothSpy = a.benchName === "SPY" && b.benchName === "SPY";
  const returns = bothNumbers(a.m.total_return, b.m.total_return);
  const spyReturns = bothSpy ? bothNumbers(a.b.total_return, b.b.total_return) : null;
  const gaps = bothSpy ? bothNumbers(gap(a), gap(b)) : null;
  const drawdowns = bothNumbers(a.m.max_drawdown, b.m.max_drawdown);
  const benchDrawdowns = bothNumbers(a.b.max_drawdown, b.b.max_drawdown);
  const sharpes = bothNumbers(a.m.sharpe, b.m.sharpe);
  const dollars = bothNumbers(a.pnl.vsBenchmark, b.pnl.vsBenchmark);
  const realized = bothNumbers(a.pnl.realized, b.pnl.realized);
  const unrealized = bothNumbers(a.pnl.unrealized, b.pnl.unrealized);
  const costs = bothNumbers(a.m.costs, b.m.costs);
  const finals = bothNumbers(a.m.final_equity, b.m.final_equity);
  const trips = bothNumbers(a.m.trades, b.m.trades);
  const exposure = bothNumbers(a.m.avg_exposure, b.m.avg_exposure);
  const inMkt = bothNumbers(a.inMkt, b.inMkt);
  const open = bothNumbers(a.m.open_positions ?? 0, b.m.open_positions ?? 0);
  const benchLabel = bothSpy ? "SPY" : "Benchmark";

  return [
    row({
      key: "return",
      label: "Return",
      meaning: RETURN,
      aText: pct(a.m.total_return),
      bText: pct(b.m.total_return),
      delta: returns ? pp(returns[1], returns[0]) : "–",
      toneA: toneOf(a.m.total_return),
      toneB: toneOf(b.m.total_return),
    }),
    row({
      key: "bench-return",
      label: bothSpy ? "SPY return" : "Benchmark return",
      meaning: bothSpy ? SPY_RETURN : MIXED_BENCH,
      aText: a.benchName === "SPY" ? pct(a.b.total_return) : `Benchmark is ${a.benchName}`,
      bText: b.benchName === "SPY" ? pct(b.b.total_return) : `Benchmark is ${b.benchName}`,
      delta: spyReturns ? pp(spyReturns[1], spyReturns[0]) : "–",
      toneA: a.benchName === "SPY" ? toneOf(a.b.total_return) : undefined,
      toneB: b.benchName === "SPY" ? toneOf(b.b.total_return) : undefined,
    }),
    row({
      key: "versus",
      label: bothSpy ? "Versus the S&P" : "Versus benchmark",
      meaning: bothSpy ? VERSUS_SPY : MIXED_BENCH,
      aText: versusSpy(a.m.total_return, a.b.total_return, a.benchName),
      bText: versusSpy(b.m.total_return, b.b.total_return, b.benchName),
      delta: gaps ? pp(gaps[1], gaps[0]) : "–",
      toneA: toneOf(gap(a)),
      toneB: toneOf(gap(b)),
    }),
    row({
      key: "drawdown",
      label: "Max drawdown",
      meaning: DRAWDOWN,
      aText: pct(a.m.max_drawdown),
      bText: pct(b.m.max_drawdown),
      delta: drawdowns ? pp(drawdowns[1], drawdowns[0]) : "–",
    }),
    row({
      key: "bench-drawdown",
      label: bothSpy ? "SPY drawdown" : "Benchmark drawdown",
      meaning: bothSpy ? drawdownMeaning("SPY") : MIXED_BENCH,
      aText: pct(a.b.max_drawdown),
      bText: pct(b.b.max_drawdown),
      delta: benchDrawdowns ? pp(benchDrawdowns[1], benchDrawdowns[0]) : "–",
    }),
    row({
      key: "sharpe",
      label: "Sharpe",
      meaning: SHARPE,
      aText: sharpe(a.m.sharpe),
      bText: sharpe(b.m.sharpe),
      delta: sharpes ? signedNum(sharpes[1] - sharpes[0]) : "–",
    }),
    row({
      key: "pnl",
      label: "Profit and loss",
      meaning: PNL,
      aText: money(a.pnl.total),
      bText: money(b.pnl.total),
      delta: signedMoney(b.pnl.total - a.pnl.total),
      toneA: toneOf(a.pnl.total),
      toneB: toneOf(b.pnl.total),
    }),
    row({
      key: "versus-dollars",
      label: bothSpy ? "Versus the S&P in dollars" : "Versus benchmark in dollars",
      meaning: bothSpy ? VERSUS_SPY_DOLLARS : MIXED_BENCH,
      aText: versusSpyDollars(a.pnl.vsBenchmark, a.benchName),
      bText: versusSpyDollars(b.pnl.vsBenchmark, b.benchName),
      delta: bothSpy && dollars ? signedMoney(dollars[1] - dollars[0]) : "–",
      toneA: toneOf(a.pnl.vsBenchmark),
      toneB: toneOf(b.pnl.vsBenchmark),
    }),
    row({
      key: "realized",
      label: "Realized",
      meaning: REALIZED,
      aText: money(a.pnl.realized),
      bText: money(b.pnl.realized),
      delta: realized ? signedMoney(realized[1] - realized[0]) : "–",
      toneA: toneOf(a.pnl.realized),
      toneB: toneOf(b.pnl.realized),
    }),
    row({
      key: "unrealized",
      label: "Unrealized",
      meaning: UNREALIZED,
      aText: money(a.pnl.unrealized),
      bText: money(b.pnl.unrealized),
      delta: unrealized ? signedMoney(unrealized[1] - unrealized[0]) : "–",
      toneA: toneOf(a.pnl.unrealized),
      toneB: toneOf(b.pnl.unrealized),
    }),
    row({
      key: "costs",
      label: "Costs",
      meaning: COSTS,
      aText: money(a.m.costs),
      bText: money(b.m.costs),
      delta: costs ? signedMoney(costs[1] - costs[0]) : "–",
    }),
    row({
      key: "final",
      label: "Final equity",
      meaning: FINAL,
      aText: money(a.m.final_equity),
      bText: money(b.m.final_equity),
      delta: finals ? signedMoney(finals[1] - finals[0]) : "–",
    }),
    row({
      key: "trips",
      label: "Round trips",
      meaning: TRIPS,
      aText: int(a.m.trades),
      bText: int(b.m.trades),
      delta: trips ? signedInt(trips[1] - trips[0]) : "–",
    }),
    row({
      key: "exposure",
      label: "Average exposure",
      meaning: EXPOSURE,
      aText: pct(a.m.avg_exposure),
      bText: pct(b.m.avg_exposure),
      delta: exposure ? pp(exposure[1], exposure[0]) : "–",
    }),
    row({
      key: "time",
      label: "Time in market",
      meaning: bothSpy ? timeMeaning("SPY") : MIXED_BENCH,
      aText: pct(a.inMkt),
      bText: pct(b.inMkt),
      delta: inMkt ? pp(inMkt[1], inMkt[0]) : "–",
    }),
    row({
      key: "open",
      label: "Open positions",
      meaning: OPEN,
      aText: int(a.m.open_positions ?? 0),
      bText: int(b.m.open_positions ?? 0),
      delta: open ? signedInt(open[1] - open[0]) : "–",
    }),
    ...analysisRows(a, b, bothSpy ? "SPY" : "the benchmark").map(row),
  ].map((item) => ({ ...item, label: item.key === "bench-drawdown" ? `${benchLabel} drawdown` : item.label }));
}

type Kind = "pct" | "ratio" | "int" | "sessions";

function fmtKind(v: number | null | undefined, kind: Kind): string {
  if (kind === "pct") return pct(v);
  if (kind === "int") return int(v);
  if (kind === "sessions") return typeof v === "number" ? `${int(v)} sessions` : "–";
  return typeof v === "number" ? num(v) : "–";
}

function deltaKind(x: number | null | undefined, y: number | null | undefined, kind: Kind): string {
  const both = bothNumbers(x, y);
  if (!both) return "–";
  if (kind === "pct") return pp(both[1], both[0]);
  if (kind === "ratio") return signedNum(both[1] - both[0]);
  return signedInt(both[1] - both[0]);
}

/** Research-analysis rows (trade quality, edge versus the benchmark, risk) for Compare. */
function analysisRows(a: SideMetrics, b: SideMetrics, bn: string): Omit<PairedMetric, "changed">[] {
  const aa = a.an;
  const ab = b.an;
  if (!aa || !ab) return [];
  const few = (n: number | null | undefined) => (typeof n === "number" && n < 30 ? " (few)" : "");
  const spec: [string, string, string, number | null | undefined, number | null | undefined, Kind][] = [
    ["cagr", "Annual return (CAGR)", "The return per year, compounded, so runs over different periods compare on one scale.", aa.returns.cagr, ab.returns.cagr, "pct"],
    ["win-rate", "Win rate", "The share of closed trades that made money after costs.", aa.trades.win_rate, ab.trades.win_rate, "pct"],
    ["payoff", "Payoff ratio", "The average winning trade divided by the average losing trade, in dollars.", aa.trades.payoff_ratio, ab.trades.payoff_ratio, "ratio"],
    ["expectancy", "Expectancy per trade", "The average return of a closed trade after costs. Above zero means a typical trade adds money.", aa.trades.expectancy_pct, ab.trades.expectancy_pct, "pct"],
    ["profit-factor", "Profit factor", "Dollars won on winning trades divided by dollars lost on losing trades. Below 1 loses money.", aa.trades.profit_factor, ab.trades.profit_factor, "ratio"],
    ["edge", `Edge per trade vs ${bn}`, `Each trade's return minus what ${bn} did over the same dates, averaged. It separates good entries from a rising market.`, aa.edge.mean, ab.edge.mean, "pct"],
    ["beat-rate", `Trades that beat ${bn}`, `The share of trades that did better than ${bn} over the same dates.`, aa.edge.beat_rate, ab.edge.beat_rate, "pct"],
    ["edge-t", "Edge t-statistic", "How sure we can be the edge is not luck. Roughly, beyond +2 or −2 is unlikely to be chance.", aa.edge.t_stat, ab.edge.t_stat, "ratio"],
    ["sortino", "Sortino", "Like Sharpe, but only falls count as risk, which suits runs that sit in cash.", aa.returns.sortino, ab.returns.sortino, "ratio"],
    ["calmar", "Calmar", "Annual return divided by the deepest drawdown. Higher means more return per unit of worst-case pain.", aa.returns.calmar, ab.returns.calmar, "ratio"],
    ["volatility", "Volatility (per year)", "How much the account value swings, scaled to a year.", aa.returns.volatility, ab.returns.volatility, "pct"],
    ["beta", `Beta to ${bn}`, `How much the account moves when ${bn} moves 1%. Near 1 means mostly market exposure.`, aa.market?.beta, ab.market?.beta, "ratio"],
    ["alpha", "Alpha (per year)", `Return per year not explained by moving with ${bn}.`, aa.market?.alpha, ab.market?.alpha, "pct"],
    ["underwater", "Longest time underwater", "The longest stretch spent below an earlier high, in sessions.", aa.drawdown.longest_underwater, ab.drawdown.longest_underwater, "sessions"],
    ["drops", "Drops over 10%", "How many separate times the account fell more than 10% from a high.", aa.drawdown.drawdowns_over_10pct, ab.drawdown.drawdowns_over_10pct, "int"],
    ["turnover", "Turnover per year", "How many times a year the account's value is traded. Higher turnover means costs matter more.", aa.costs.turnover, ab.costs.turnover, "ratio"],
    ["cost-share", "Costs share of trade profit", "Trading costs as a share of what trades made before costs. A high share means the strategy is fragile to worse fills.", aa.costs.share_of_gross_profit, ab.costs.share_of_gross_profit, "pct"],
    ["streak", "Most losing trades in a row", "The longest run of losing trades back to back.", aa.streaks.max_consecutive_losing_trades, ab.streaks.max_consecutive_losing_trades, "int"],
  ];
  const rows = spec.map(([key, label, meaning, va, vb, kind]) => ({
    key,
    label,
    meaning,
    aText: fmtKind(va, kind),
    bText: fmtKind(vb, kind),
    delta: deltaKind(va, vb, kind),
    toneA: kind === "pct" && key !== "volatility" && key !== "cost-share" ? toneOf(va) : undefined,
    toneB: kind === "pct" && key !== "volatility" && key !== "cost-share" ? toneOf(vb) : undefined,
  }));
  rows.splice(1, 0, {
    key: "closed-trades",
    label: "Closed trades",
    meaning: "Trades bought and fully sold. Under 30 is marked (few): trade statistics from so few trades should not be trusted.",
    aText: `${int(aa.trades.count)}${few(aa.trades.count)}`,
    bText: `${int(ab.trades.count)}${few(ab.trades.count)}`,
    delta: deltaKind(aa.trades.count, ab.trades.count, "int"),
    toneA: undefined,
    toneB: undefined,
  });
  return rows;
}
