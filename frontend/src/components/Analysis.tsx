"use client";

import type { ReactNode } from "react";
import { StatBreakdown, type StatItem } from "@/components/StatBreakdown";
import type { Analysis, GroupRow } from "@/lib/api";
import { int, money, num, pct } from "@/lib/format";

/** The same cut-off the engine uses for its low-sample warning. */
export const LOW_SAMPLE_TRADES = 30;

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function tone(n: number | null | undefined): "pos" | "neg" | undefined {
  return isNum(n) ? (n >= 0 ? "pos" : "neg") : undefined;
}

/** A signed ratio such as a t-statistic or beta ("+1.24"). */
export function signed(v: unknown, digits = 2): string {
  return isNum(v) ? `${v > 0 ? "+" : ""}${num(v, digits)}` : "–";
}

export function LowSample({ trades }: { trades: number | null | undefined }) {
  if (!isNum(trades) || trades >= LOW_SAMPLE_TRADES) return null;
  return (
    <span className="badge badge-demo" data-testid="low-sample" title="Fewer than 30 closed trades">
      Only {int(trades)} {trades === 1 ? "trade" : "trades"}: too few to trust
    </span>
  );
}

export const EXIT_LABEL: Record<string, string> = {
  stop_loss: "Stop loss",
  take_profit: "Take profit",
  trailing_stop: "Trailing stop",
  max_hold: "Held the full time",
  signal: "Exit signal",
};

const REGIME_LABEL: Record<string, string> = {
  up: "Market rising (benchmark above its 200-day average)",
  down: "Market falling (benchmark below its 200-day average)",
  warmup: "First 200 days (no average yet)",
};

const SKIP_LABEL: Record<string, string> = {
  already_held: "Already held",
  max_positions: "No free position slot",
  insufficient_cash: "Not enough cash when the signal fired",
  zero_quantity: "Size rounded down to zero shares",
  insufficient_cash_at_open: "Not enough cash at the next open",
  outside_range: "Price left the entry range",
  "entry not filled": "Entry price never reached",
};

export function tradeQualityStats(a: Analysis): StatItem[] {
  const t = a.trades;
  return [
    {
      label: "Win rate",
      value: pct(t.win_rate),
      meaning:
        "The share of closed trades that made money after costs. 55% means 55 of every 100 trades finished ahead. A low win rate can still pay if the wins are much bigger than the losses.",
    },
    {
      label: "Average win",
      value: `${money(t.avg_win)} (${pct(t.avg_win_pct, true)})`,
      tone: "pos",
      meaning: "What a winning trade made on average, in dollars and as a percent of what it cost to open.",
    },
    {
      label: "Average loss",
      value: `${money(t.avg_loss)} (${pct(t.avg_loss_pct, true)})`,
      tone: "neg",
      meaning:
        "What a losing trade lost on average, in dollars and as a percent of what it cost to open. A trade that only broke even counts as a loss here.",
    },
    {
      label: "Payoff ratio",
      value: t.payoff_ratio == null ? "–" : `${num(t.payoff_ratio)} to 1`,
      meaning:
        "Average win divided by average loss. 1.5 to 1 means the typical winner is one and a half times the typical loser. Wins that are bigger than losses let a strategy profit with a win rate under 50%.",
    },
    {
      label: "Expectancy per trade",
      value: `${money(t.expectancy)} (${pct(t.expectancy_pct, true)})`,
      tone: tone(t.expectancy),
      meaning:
        "The average result of one trade after costs, in dollars and in percent. Above zero means each trade was worth taking on average. It is the single best check on whether the rules earn money trade by trade.",
    },
    {
      label: "Profit factor",
      value: t.profit_factor == null ? "–" : num(t.profit_factor),
      tone: isNum(t.profit_factor) ? (t.profit_factor >= 1 ? "pos" : "neg") : undefined,
      meaning:
        "Total dollars won divided by total dollars lost. Above 1 the rules made money; 2 means two dollars won for every dollar lost; below 1 the rules lost money. It shows as a dash when there were no losing trades.",
    },
    {
      label: "Trade t-statistic",
      value: signed(t.t_stat),
      meaning:
        "How far the average trade return sits from zero, measured against how much trade results vary and how many trades there were. Around 2 or more is usually read as real; under about 1.5 the average could easily be luck. It needs plenty of trades to mean much.",
    },
    {
      label: "Largest win",
      value: money(t.largest_win),
      tone: isNum(t.largest_win) ? "pos" : undefined,
      meaning: "The best single trade in dollars. If this is most of the total profit, the result leans on one lucky trade.",
    },
    {
      label: "Largest loss",
      value: money(t.largest_loss),
      tone: isNum(t.largest_loss) ? "neg" : undefined,
      meaning: "The worst single trade in dollars.",
    },
    {
      label: "Average hold",
      value: isNum(t.avg_hold) ? `${num(t.avg_hold, 1)} sessions` : "–",
      meaning:
        "How long a trade stayed open on average, counted in bars from the day the buy filled to the day the sell filled (one bar is a day on daily runs).",
    },
    {
      label: "Hold: winners vs losers",
      value: `${isNum(t.avg_hold_win) ? num(t.avg_hold_win, 1) : "–"} vs ${isNum(t.avg_hold_loss) ? num(t.avg_hold_loss, 1) : "–"}`,
      meaning:
        "Average sessions held by trades that won, then by trades that lost. If losers are held much longer than winners, the exit may be too patient with bad trades.",
    },
    {
      label: "Typical worst dip inside a trade",
      value: pct(t.avg_mae),
      tone: isNum(t.avg_mae) ? "neg" : undefined,
      meaning:
        "On average, how far below the buy price a trade fell at its worst point before it closed (MAE, maximum adverse excursion). It tells you where a stop loss can sit without cutting trades that would have worked.",
    },
    {
      label: "Typical best rise inside a trade",
      value: pct(t.avg_mfe),
      tone: isNum(t.avg_mfe) ? "pos" : undefined,
      meaning:
        "On average, how far above the buy price a trade rose at its best point before it closed (MFE, maximum favourable excursion). If this is far above what trades finish with, a profit target or trailing stop may be leaving money behind.",
    },
  ];
}

export function versusStats(a: Analysis, benchName: string): StatItem[] {
  const e = a.edge;
  const r = a.returns;
  const b = a.benchmark;
  const m = a.market;
  const pair = (ours: string, theirs: string | null) => (theirs == null ? ours : `${ours} · ${benchName} ${theirs}`);
  const out: StatItem[] = [
    {
      label: `Edge per trade vs ${benchName}`,
      value: pct(e.mean, true),
      tone: tone(e.mean),
      meaning: `For each trade, its return minus what ${benchName} returned over the very same days (open to open). Averaged over all trades. Above zero means the picks beat simply holding ${benchName} while they were held.`,
    },
    {
      label: `Trades that beat ${benchName}`,
      value: pct(e.beat_rate),
      meaning: `The share of trades that returned more than ${benchName} over the same days.`,
    },
    {
      label: "Edge t-statistic",
      value: signed(e.t_stat),
      meaning:
        "How convincing the edge per trade is: the average edge divided by how much it varies and how many trades there were. Around 2 or more is usually read as real; around 1 or less could easily be luck.",
    },
    {
      label: "Annual return (CAGR)",
      value: pair(pct(r.cagr), b ? pct(b.cagr) : null),
      tone: tone(r.cagr),
      meaning:
        "The steady yearly growth rate that would turn the starting value into the ending value (compound annual growth rate). It puts runs of different lengths on the same scale.",
    },
  ];
  if (m) {
    out.push(
      {
        label: `Beta to ${benchName}`,
        value: num(m.beta),
        meaning: `How much the account moves when ${benchName} moves. 1.0 moves one for one; 0.5 moves half as much. A beta near 1 with no alpha means the strategy is mostly a more expensive way to hold ${benchName}.`,
      },
      {
        label: `Correlation with ${benchName}`,
        value: num(m.correlation),
        meaning: `How closely the account's ups and downs line up with ${benchName}'s. 1 is in lockstep, 0 is unrelated, negative means opposite.`,
      },
      {
        label: "Alpha (per year)",
        value: pct(m.alpha, true),
        tone: tone(m.alpha),
        meaning: `The yearly return left over after removing what simply riding ${benchName}'s moves would explain (using beta). Positive means the strategy added something beyond the market.`,
      },
    );
  }
  if (b) {
    out.push(
      {
        label: "Sharpe",
        value: pair(num(r.sharpe), num(b.sharpe)),
        meaning: `Return for the amount of bouncing, ours then ${benchName}'s. Setting them side by side shows whether the strategy earned its extra swings.`,
      },
      {
        label: "Sortino",
        value: pair(num(r.sortino), num(b.sortino)),
        meaning: `Like Sharpe but only counting the downward swings as risk, ours then ${benchName}'s. It suits strategies that sit in cash, where upward jumps are not the thing to worry about.`,
      },
      {
        label: "Calmar",
        value: pair(num(r.calmar), num(b.calmar)),
        meaning: `Annual return divided by the deepest drawdown, ours then ${benchName}'s. Higher means more yearly growth for the worst dip endured.`,
      },
    );
  }
  return out;
}

export function riskStats(a: Analysis): StatItem[] {
  const d = a.drawdown;
  const r = a.returns;
  return [
    {
      label: "Volatility (per year)",
      value: pct(r.volatility),
      meaning:
        "How much the account's value bounces around, scaled to a year (the standard deviation of each bar's change). Higher means a bumpier ride.",
    },
    {
      label: "Sortino",
      value: num(r.sortino),
      meaning:
        "Return for the amount of downward bouncing only (cash earns nothing). Higher is better. Unlike Sharpe it does not punish big up days.",
    },
    {
      label: "Calmar",
      value: num(r.calmar),
      meaning: "Annual return divided by the deepest drawdown. 1.0 means a year's growth equals the worst dip.",
    },
    {
      label: "Longest drop to recovery",
      value: isNum(d.max_dd_length) ? `${int(d.max_dd_length)} sessions` : d.max_drawdown ? "not recovered" : "–",
      meaning:
        "For the deepest drawdown, how long it took from the peak until the account got back to that peak. 'Not recovered' means it was still below the old high when the run ended.",
    },
    {
      label: "Longest time underwater",
      value: isNum(d.longest_underwater) ? `${int(d.longest_underwater)} sessions` : "–",
      meaning:
        "The longest stretch spent below a previous high, from the peak until a new high (or the end of the run). A shallow drop that lasts two years can be harder to live with than a deep one that recovers in a month.",
    },
    {
      label: "Recovered on",
      value: d.recovery_session ?? (d.max_drawdown ? "not yet" : "–"),
      meaning: "The first session after the deepest drawdown when the account got back to the high it fell from.",
    },
    {
      label: "Time underwater",
      value: pct(d.time_underwater),
      meaning: "The share of sessions the account was below its highest value so far.",
    },
    {
      label: "Drops over 10%",
      value: isNum(d.drawdowns_over_10pct) ? int(d.drawdowns_over_10pct) : "–",
      meaning: "How many separate times the account fell more than 10% below a high before it set a new one.",
    },
  ];
}

export function costStats(a: Analysis): StatItem[] {
  const c = a.costs;
  return [
    {
      label: "Trading costs paid",
      value: money(c.total),
      meaning: "All costs charged on buys and sells in the run, including any on idle cash moved in and out of the benchmark.",
    },
    {
      label: "Costs as a share of starting cash",
      value: pct(c.cost_drag),
      meaning: "The total costs divided by the cash the run started with: how much of the starting capital was spent on trading.",
    },
    {
      label: "Costs as a share of trade profit",
      value: pct(c.share_of_gross_profit),
      meaning:
        "The costs of the closed trades divided by what those trades made before costs. If costs take half of it, slightly worse fills in real trading could wipe the result out.",
    },
    {
      label: "Return before costs",
      value: pct(c.return_before_costs, true),
      meaning: "What the run's total return would have been if trading were free.",
    },
    {
      label: "Turnover per year",
      value: isNum(c.turnover) ? `${num(c.turnover)}x` : "–",
      meaning:
        "How many times per year the average account value was bought and sold. 2x means the equivalent of the whole account changed hands twice a year. Higher turnover means costs matter more.",
    },
  ];
}

export function streakStats(a: Analysis): StatItem[] {
  const s = a.streaks;
  return [
    {
      label: "Most losing trades in a row",
      value: isNum(s.max_consecutive_losing_trades) ? int(s.max_consecutive_losing_trades) : "–",
      meaning: "The longest run of closed trades that each lost money. A practical check on whether you could stick with the strategy through a bad patch.",
    },
    {
      label: "Most losing months in a row",
      value: isNum(s.max_consecutive_losing_months) ? int(s.max_consecutive_losing_months) : "–",
      meaning: "The longest run of calendar months in which the account finished lower than it started.",
    },
    {
      label: "Losing months",
      value: isNum(s.months) ? `${int(s.losing_months)} of ${int(s.months)}` : "–",
      meaning: "How many calendar months ended with a loss, out of all months in the run.",
    },
  ];
}

export function StatGroup({
  title,
  intro,
  stats,
  badge,
  testId,
}: {
  title: string;
  intro?: ReactNode;
  stats: StatItem[];
  badge?: ReactNode;
  testId?: string;
}) {
  return (
    <section className="stack-block" data-testid={testId}>
      <div className="section-head">
        <h2>{title}</h2>
        {badge}
      </div>
      {intro ? <p className="muted small">{intro}</p> : null}
      <StatBreakdown stats={stats} />
    </section>
  );
}

function Tbl({ head, children, empty }: { head: ReactNode; children: ReactNode; empty?: string }) {
  if (empty) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{head}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

const Num = ({ v, kind = "pct", plus = true }: { v: number | null | undefined; kind?: "pct" | "money"; plus?: boolean }) => (
  <td className={`num ${tone(v) ?? ""}`}>{kind === "pct" ? pct(v, plus) : money(v)}</td>
);

export function ByYearTable({ a, benchName }: { a: Analysis; benchName: string }) {
  return (
    <section className="stack-block" data-testid="by-year">
      <h2>Results by year</h2>
      <p className="muted small">
        Whether the result is steady or comes from one lucky year. Trades are counted in the year they were bought.
      </p>
      <Tbl
        empty={a.by_year.length ? undefined : "No full sessions."}
        head={
          <>
            <th>Year</th>
            <th className="num">Return</th>
            <th className="num">{benchName}</th>
            <th className="num">Gap</th>
            <th className="num">Trades</th>
            <th className="num">Win rate</th>
            <th className="num">Edge per trade</th>
          </>
        }
      >
        {a.by_year.map((y) => (
          <tr key={y.year}>
            <td>{y.year}</td>
            <Num v={y.ret} />
            <Num v={y.bench_ret} />
            <Num v={isNum(y.ret) && isNum(y.bench_ret) ? y.ret - y.bench_ret : null} />
            <td className="num">{int(y.trades)}</td>
            <td className="num">{y.trades ? pct(y.win_rate) : "–"}</td>
            <Num v={y.avg_edge} />
          </tr>
        ))}
      </Tbl>
    </section>
  );
}

function groupRows(rows: GroupRow[], label: (k: string) => string) {
  return rows.map((g) => (
    <tr key={g.key}>
      <td>{label(g.key)}</td>
      <td className="num">{int(g.trades)}</td>
      <td className="num">{pct(g.win_rate)}</td>
      <Num v={g.pnl} kind="money" />
      <Num v={g.avg_ret} />
      <Num v={g.avg_edge} />
      <td className="num">{isNum(g.avg_hold) ? num(g.avg_hold, 1) : "–"}</td>
    </tr>
  ));
}

const GROUP_HEAD = (first: string) => (
  <>
    <th>{first}</th>
    <th className="num">Trades</th>
    <th className="num">Win rate</th>
    <th className="num">P&L</th>
    <th className="num">Avg return</th>
    <th className="num">Avg edge</th>
    <th className="num">Avg hold</th>
  </>
);

export function ExitTable({ a }: { a: Analysis }) {
  return (
    <section className="stack-block" data-testid="by-exit">
      <h2>How trades ended</h2>
      <p className="muted small">
        Which exit rule earns its keep. A stop can cut losers short but also cut trades that would have recovered.
      </p>
      <Tbl empty={a.by_exit.length ? undefined : "No closed trades."} head={GROUP_HEAD("Exit")}>
        {groupRows(a.by_exit, (k) => EXIT_LABEL[k] ?? k)}
      </Tbl>
    </section>
  );
}

export function SymbolTable({ a }: { a: Analysis }) {
  return (
    <section className="stack-block" data-testid="by-symbol">
      <h2>Results by symbol</h2>
      <p className="muted small">Best profit first. Look for stocks that drag results down, or one stock carrying everything.</p>
      <Tbl empty={a.by_symbol.length ? undefined : "No closed trades."} head={GROUP_HEAD("Symbol")}>
        {groupRows(a.by_symbol, (k) => k)}
      </Tbl>
    </section>
  );
}

export function RegimeTable({ a, benchName }: { a: Analysis; benchName: string }) {
  return (
    <section className="stack-block" data-testid="by-regime">
      <h2>Rising and falling markets</h2>
      <p className="muted small">
        Results split by whether {benchName} was above or below its 200-day average the day before. A rule that only works
        in rising markets may need a market filter. Trades are placed in the state at the time they were bought.
      </p>
      <Tbl
        empty={a.by_regime.length ? undefined : "Needs the benchmark's price history."}
        head={
          <>
            <th>Market</th>
            <th className="num">Sessions</th>
            <th className="num">Return</th>
            <th className="num">{benchName}</th>
            <th className="num">Trades</th>
            <th className="num">Win rate</th>
            <th className="num">Avg edge</th>
          </>
        }
      >
        {a.by_regime.map((g) => (
          <tr key={g.regime}>
            <td>{REGIME_LABEL[g.regime] ?? g.regime}</td>
            <td className="num">{int(g.sessions)}</td>
            <Num v={g.ret} />
            <Num v={g.bench_ret} />
            <td className="num">{int(g.trades)}</td>
            <td className="num">{g.trades ? pct(g.win_rate) : "–"}</td>
            <Num v={g.avg_edge} />
          </tr>
        ))}
      </Tbl>
    </section>
  );
}

export function FunnelSection({ a }: { a: Analysis }) {
  const f = a.funnel;
  const skipped = Object.entries(f.skipped ?? {}).sort((x, y) => y[1] - x[1]);
  const notFilled = Object.entries(f.not_filled ?? {}).sort((x, y) => y[1] - x[1]);
  const signals = f.signals ?? 0;
  const share = (n: number) => (signals ? pct(n / signals) : "–");
  return (
    <section className="stack-block" data-testid="funnel">
      <h2>Signal funnel</h2>
      <p className="muted small">
        Every time all entry rules passed, what happened. If many signals are skipped for lack of a free slot or cash, more
        slots or smaller positions may help.
      </p>
      {signals ? (
        <Tbl
          head={
            <>
              <th>Step</th>
              <th className="num">Count</th>
              <th className="num">Share of signals</th>
            </>
          }
        >
          <tr>
            <td>Entry rules passed (signals)</td>
            <td className="num">{int(signals)}</td>
            <td className="num">100.00%</td>
          </tr>
          <tr>
            <td>Turned into a buy order</td>
            <td className="num">{int(f.entered)}</td>
            <td className="num">{pct(f.taken_rate)}</td>
          </tr>
          {skipped.map(([k, n]) => (
            <tr key={k}>
              <td>Skipped: {SKIP_LABEL[k] ?? k}</td>
              <td className="num">{int(n)}</td>
              <td className="num">{share(n)}</td>
            </tr>
          ))}
          <tr>
            <td>Buy orders that filled</td>
            <td className="num">{int(f.filled)}</td>
            <td className="num">{share(f.filled ?? 0)}</td>
          </tr>
          {notFilled.map(([k, n]) => (
            <tr key={`n-${k}`}>
              <td>Order dropped at the open: {SKIP_LABEL[k] ?? k}</td>
              <td className="num">{int(n)}</td>
              <td className="num">{share(n)}</td>
            </tr>
          ))}
        </Tbl>
      ) : (
        <p className="muted">No entry signals.</p>
      )}
    </section>
  );
}

/** Year rows by month columns; green for gains, red for losses, deeper colour for bigger moves. */
export function MonthlyGrid({ a }: { a: Analysis }) {
  const byYear = new Map<string, (number | null)[]>();
  for (const m of a.monthly) {
    const [y, mo] = m.month.split("-");
    const row = byYear.get(y) ?? Array.from({ length: 12 }, () => null);
    row[Number(mo) - 1] = m.ret ?? null;
    byYear.set(y, row);
  }
  const years = [...byYear.keys()].sort();
  const yearRet = new Map(a.by_year.map((y) => [y.year, y.ret]));
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const shade = (v: number | null) =>
    v == null
      ? undefined
      : {
          background: `color-mix(in srgb, var(${v >= 0 ? "--good" : "--bad"}) ${Math.round(Math.min(Math.abs(v) / 0.08, 1) * 55 + 8)}%, transparent)`,
        };
  return (
    <section className="stack-block" data-testid="monthly-grid">
      <h2>Monthly returns</h2>
      <p className="muted small">
        The account&apos;s change in each calendar month. Shows when the strategy works and when it does not, faster than any
        single number.
      </p>
      {years.length ? (
        <div className="table-wrap">
          <table className="month-grid">
            <thead>
              <tr>
                <th>Year</th>
                {MON.map((m) => (
                  <th key={m} className="num">
                    {m}
                  </th>
                ))}
                <th className="num">Year</th>
              </tr>
            </thead>
            <tbody>
              {years.map((y) => (
                <tr key={y}>
                  <td>{y}</td>
                  {byYear.get(y)!.map((v, i) => (
                    <td key={i} className="num" style={shade(v)}>
                      {v == null ? "" : (v * 100).toFixed(1)}
                    </td>
                  ))}
                  <td className={`num ${tone(yearRet.get(y)) ?? ""}`}>{pct(yearRet.get(y))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No full months.</p>
      )}
      <p className="caption">Values are percent. The first month starts from the first day&apos;s closing value.</p>
    </section>
  );
}
