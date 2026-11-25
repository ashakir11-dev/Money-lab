import type { DecisionRow, EquityRow, FillRow, RunMetrics } from "./api";

export type RoundTrip = {
  symbol: string;
  entrySession: string;
  exitSession: string;
  qty: number;
  entryPrice: number;
  exitPrice: number;
  costs: number;
  pnl: number;
  holdSessions: number | null;
};

export type Pnl = {
  realized: number;
  unrealized: number;
  costs: number;
  total: number;
  vsBenchmark: number | null;
};

export type FillBreakdown = {
  buys: number;
  sells: number;
  symbols: string[];
  open: number;
  costs: number;
  held: string[];
  notionalBuys: number;
  notionalSells: number;
};

type Lot = { session: string; qty: number; price: number; cost: number };

function lotsFromFills(fills: FillRow[]): { remaining: Map<string, Lot[]>; trips: RoundTrip[] } {
  const remaining = new Map<string, Lot[]>();
  const trips: RoundTrip[] = [];
  for (const f of fills) {
    const sym = String(f.symbol);
    const book = remaining.get(sym) ?? [];
    if (f.side === "buy") {
      book.push({ session: f.session, qty: f.qty, price: f.price, cost: f.cost });
      remaining.set(sym, book);
      continue;
    }
    if (f.side !== "sell") continue;
    let left = f.qty;
    const sellCostPer = f.qty ? f.cost / f.qty : 0;
    while (left > 0 && book.length) {
      const lot = book[0];
      const take = Math.min(lot.qty, left);
      const buyCost = lot.qty ? (lot.cost / lot.qty) * take : 0;
      const sellCost = sellCostPer * take;
      trips.push({
        symbol: sym,
        entrySession: lot.session,
        exitSession: f.session,
        qty: take,
        entryPrice: lot.price,
        exitPrice: f.price,
        costs: buyCost + sellCost,
        pnl: take * (f.price - lot.price) - buyCost - sellCost,
        holdSessions: typeof f.sessions_waited === "number" ? f.sessions_waited : null,
      });
      lot.qty -= take;
      lot.cost -= buyCost;
      left -= take;
      if (lot.qty <= 1e-9) book.shift();
    }
    remaining.set(sym, book);
  }
  return { remaining, trips };
}

export function roundTrips(fills: FillRow[]): RoundTrip[] {
  return lotsFromFills(fills).trips;
}

export function fillBreakdown(fills: FillRow[]): FillBreakdown {
  const buys = fills.filter((f) => f.side === "buy");
  const sells = fills.filter((f) => f.side === "sell");
  const { remaining } = lotsFromFills(fills);
  const held = [...remaining.entries()].filter(([, lots]) => lots.some((l) => l.qty > 0)).map(([s]) => s);
  const symbols = [...new Set(fills.map((f) => String(f.symbol)))];
  return {
    buys: buys.length,
    sells: sells.length,
    symbols,
    open: held.length,
    costs: fills.reduce((s, f) => s + (f.cost || 0), 0),
    held,
    notionalBuys: buys.reduce((s, f) => s + (f.gross ?? f.qty * f.price), 0),
    notionalSells: sells.reduce((s, f) => s + (f.gross ?? f.qty * f.price), 0),
  };
}

export function pnlSummary(
  fills: FillRow[],
  equity: EquityRow[],
  metrics: RunMetrics,
  startCash: number,
): Pnl {
  const { remaining, trips } = lotsFromFills(fills);
  const realized = trips.reduce((s, t) => s + t.pnl, 0);
  const last = equity[equity.length - 1];
  const positionsValue = last?.positions_value ?? 0;
  let basis = 0;
  for (const lots of remaining.values()) {
    for (const lot of lots) basis += lot.qty * lot.price + lot.cost;
  }
  const unrealized = positionsValue - basis;
  const costs = fills.reduce((s, f) => s + (f.cost || 0), 0);
  const finalEq = metrics.final_equity ?? last?.equity ?? startCash;
  const total = finalEq - startCash;
  const benchFinal = last?.benchmark_equity;
  const vsBenchmark = typeof benchFinal === "number" ? total - (benchFinal - startCash) : null;
  return { realized, unrealized, costs, total, vsBenchmark };
}

export function reasonCounts(rows: DecisionRow[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const key = String(row.reason ?? row.action ?? "unknown");
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

export function marksFromFills(fills: FillRow[]): { session: string; side: "buy" | "sell" }[] {
  return fills
    .filter((f) => f.side === "buy" || f.side === "sell")
    .map((f) => ({ session: f.session, side: f.side as "buy" | "sell" }));
}

export function timeInMarket(equity: EquityRow[]): number | null {
  if (equity.length < 1) return null;
  const inM = equity.filter((e) => (e.positions ?? 0) > 0).length;
  return inM / equity.length;
}

export function drawdownSeries(values: (number | null | undefined)[]): (number | null)[] {
  let peak = -Infinity;
  return values.map((v) => {
    if (typeof v !== "number") return null;
    if (v > peak) peak = v;
    return peak > 0 ? v / peak - 1 : 0;
  });
}

export function exposureSeries(equity: EquityRow[]): (number | null)[] {
  return equity.map((e) => (e.equity ? e.positions_value / e.equity : null));
}
