/**
 * Typed client for the Money-lab research API. Every type comes from the generated
 * OpenAPI schema (src/lib/api-schema.ts, `npm run gen:api`); no trading maths here.
 *
 * In production the dashboard is served by FastAPI from the same address, so the base
 * is "". For `npm run dev`, set NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000.
 */
import type { components } from "./api-schema";

type S = components["schemas"];
export type Health = S["Health"];
export type Component = S["ComponentOut"];
export type Param = S["ParamOut"];
export type Defaults = S["Defaults"];
export type Snapshot = S["SnapshotOut"];
export type Watchlist = S["WatchlistOut"];
export type WatchlistIn = S["WatchlistIn"];
export type EquiblesImport = S["EquiblesImport"];
export type TiingoImport = S["TiingoImport"];
export type CsvImport = S["CsvImport"];
export type StrategyConfig = S["StrategyConfig"];
export type Condition = S["Condition"];
export type ExitRule = S["ExitRule"];
export type RunSubmit = S["RunSubmit"];
export type Job = S["JobOut"];
export type RunSummary = S["RunSummary"];
export type RunDetail = S["RunDetail"];
export type EquityRow = S["EquityRow"];
export type FillRow = S["FillRow"];
export type TradeChart = S["TradeChartOut"];
export type DecisionPage = S["DecisionPage"];
export type Compare = S["CompareOut"];
export type SettingDiff = S["SettingDiff"];
export type PaperAccount = S["PaperAccountOut"];
export type PaperDetail = S["PaperDetail"];
export type PaperStart = S["PaperStart"];
export type Gate = S["GateOut"];
export type PaperStatus = {
  paper_id: string;
  name: string;
  run_id: string;
  start_session: string;
  latest_processed: string | null;
  processed_sessions: number;
  next_expected: string;
  cash: number;
  equity: number;
  positions: Record<string, Record<string, unknown>>;
  pending_orders: Record<string, Record<string, unknown>>;
  missing: string[];
  waiting: string[];
  conflicts: Record<string, unknown>[];
  rejected: Record<string, unknown>[];
  duplicates: number;
  events: number;
  blocked: string | null;
};
export type Delivery = { session?: string | null; status: string; detail?: string; processed: string[] };

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(describeDetail(status, detail));
  }
}

function describeDetail(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".")
            : "";
          return loc ? `${loc}: ${(d as { msg: string }).msg}` : (d as { msg: string }).msg;
        }
        return JSON.stringify(d);
      })
      .join("; ");
  }
  return `Request failed (${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Cannot reach the Money-lab server. Is `uv run moneylab serve` running?");
  }
  const text = await res.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

function qs(params: Record<string, string | number | string[] | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
    else u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export const api = {
  health: () => request<Health>("/api/health"),
  components: () => request<Component[]>("/api/components"),
  defaults: () => request<Defaults>("/api/defaults"),
  validate: (body: RunSubmit) =>
    post<{ resolved: Record<string, unknown>; summary: string }>("/api/validate", body),
  snapshots: () => request<Snapshot[]>("/api/snapshots"),
  importCsv: (body: CsvImport) => post<Snapshot>("/api/snapshots/csv", body),
  importEquibles: (body: EquiblesImport) => post<Snapshot>("/api/snapshots/equibles", body),
  importTiingo: (body: TiingoImport) => post<Snapshot>("/api/snapshots/tiingo", body),
  watchlists: () => request<Watchlist[]>("/api/watchlists"),
  saveWatchlist: (body: WatchlistIn) => post<Watchlist>("/api/watchlists", body),
  runs: () => request<RunSummary[]>("/api/runs"),
  submitRun: (body: RunSubmit) => post<Job>("/api/runs", body),
  duplicate: (id: string) => post<Job>(`/api/runs/${encodeURIComponent(id)}/duplicate`),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  equity: (id: string) => request<EquityRow[]>(`/api/runs/${encodeURIComponent(id)}/equity`),
  fills: (id: string) => request<FillRow[]>(`/api/runs/${encodeURIComponent(id)}/fills`),
  chart: (id: string, decisionId: number) =>
    request<TradeChart>(`/api/runs/${encodeURIComponent(id)}/chart${qs({ decision_id: decisionId })}`),
  decisions: (
    id: string,
    q: { offset?: number; limit?: number; action?: string[]; symbol?: string; session?: string } = {},
  ) => request<DecisionPage>(`/api/runs/${encodeURIComponent(id)}/decisions${qs(q)}`),
  compare: (a: string, b: string) => request<Compare>(`/api/compare${qs({ a, b })}`),
  paperAccounts: () => request<PaperAccount[]>("/api/paper"),
  startPaper: (body: PaperStart) => post<PaperAccount>("/api/paper", body),
  paper: (id: string) => request<PaperDetail>(`/api/paper/${encodeURIComponent(id)}`),
  deliverPaper: (id: string, csv: string) =>
    post<Delivery[]>(`/api/paper/${encodeURIComponent(id)}/sessions`, { csv }),
  fetchPaper: (id: string) => post<Delivery[]>(`/api/paper/${encodeURIComponent(id)}/fetch`),
  replayPaper: (id: string) => post<Record<string, unknown>>(`/api/paper/${encodeURIComponent(id)}/replay`),
  gates: () => request<Gate[]>("/api/gates"),
};

/** Poll a job every second until it completes or fails. */
export async function waitForJob(id: string, onUpdate?: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api.job(id);
    onUpdate?.(j);
    if (j.status === "complete" || j.status === "failed") return j;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// Shapes inside free-form JSON fields (first_difference, decision rows, metrics) -------
export type ConditionResult = {
  condition?: string;
  observed?: number | null;
  threshold?: number | null;
  passed?: boolean | null;
  [k: string]: unknown;
};

export type DecisionRow = {
  decision_id?: number;
  session?: string;
  symbol?: string;
  action?: string;
  reason?: string;
  qty?: number | null;
  est_cost?: number | null;
  available?: number | null;
  conditions?: ConditionResult[] | null;
  values?: Record<string, number | null> | null;
  fill?: Partial<FillRow>;
  [k: string]: unknown;
};

export type FirstDifference = {
  session: string;
  symbol: string;
  a: DecisionRow;
  b: DecisionRow;
  impact: {
    next_session?: string;
    cash_a?: number;
    cash_b?: number;
    final_equity_a?: number;
    final_equity_b?: number;
    final_equity_delta?: number;
  };
};

export type BenchmarkMetrics = {
  symbol?: string;
  invested_session?: string;
  price?: number;
  shares?: number;
  cost?: number;
  cash_left?: number;
  total_return?: number | null;
  max_drawdown?: number | null;
  sharpe?: number | null;
};

export type RunMetrics = {
  total_return?: number | null;
  max_drawdown?: number | null;
  sharpe?: number | null;
  sharpe_assumptions?: string;
  trades?: number;
  open_positions?: number;
  costs?: number;
  avg_exposure?: number;
  final_equity?: number;
  benchmark?: BenchmarkMetrics;
};
