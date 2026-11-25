const DASH = "–";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function money(v: unknown): string {
  return isNum(v)
    ? `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : DASH;
}

export function num(v: unknown, digits = 2): string {
  return isNum(v)
    ? v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : DASH;
}

export function int(v: unknown): string {
  return isNum(v) ? Math.round(v).toLocaleString("en-US") : DASH;
}

/** A fraction (0.0123) as a percentage with 2 decimals ("1.23%"). */
export function pct(v: unknown, signed = false): string {
  if (!isNum(v)) return DASH;
  const s = (v * 100).toFixed(2);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

/** Difference of two fractions in percentage points ("+1.23 pp"). */
export function pp(a: unknown, b: unknown): string {
  if (!isNum(a) || !isNum(b)) return DASH;
  const d = (a - b) * 100;
  return `${d > 0 ? "+" : ""}${d.toFixed(2)} pp`;
}

export function sharpe(v: unknown): string {
  return v === null || v === undefined ? "undefined" : num(v);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** A value of any kind, for settings and decision tables. */
export function plain(v: unknown): string {
  if (v === null || v === undefined) return DASH;
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : num(v, 4).replace(/0+$/, "").replace(/\.$/, "");
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export const ACTION_LABEL: Record<string, string> = {
  enter: "buy order",
  exit: "sell order",
  skip: "skipped",
  no_signal: "no signal",
  ineligible: "not eligible",
  none: "nothing recorded",
};
