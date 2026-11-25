/** Named US sector groups. Filtered to whatever the snapshot actually contains. */

export const SECTORS: { id: string; label: string; symbols: string[] }[] = [
  { id: "tech", label: "Technology", symbols: ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AVGO", "AMD", "ADBE", "CRM", "INTC"] },
  { id: "health", label: "Health care", symbols: ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "ABT"] },
  { id: "finance", label: "Financials", symbols: ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "BLK"] },
  { id: "energy", label: "Energy", symbols: ["XOM", "CVX", "COP", "SLB", "EOG", "PSX"] },
  { id: "consumer", label: "Consumer", symbols: ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "COST", "WMT"] },
  { id: "etf", label: "Sector ETFs", symbols: ["XLK", "XLF", "XLV", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE"] },
];

export function sectorSymbols(id: string, available: string[]): string[] {
  const g = SECTORS.find((s) => s.id === id);
  if (!g) return [];
  const have = new Set(available.map((s) => s.toUpperCase()));
  return g.symbols.filter((s) => have.has(s));
}
