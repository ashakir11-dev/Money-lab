"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { api, type Snapshot, type Watchlist } from "@/lib/api";
import { int, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { Badge, ErrorBox, Loading, PageHeader, Section, Tile } from "@/components/ui";

const symbolsOf = (s: string) =>
  s
    .split(/[\s,]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);

function spanDays(first?: string, last?: string): string {
  if (!first || !last) return "–";
  const a = Date.parse(first);
  const b = Date.parse(last);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return `${first} → ${last}`;
  const days = Math.round((b - a) / 86400000);
  const years = days / 365.25;
  return years >= 1.5 ? `${years.toFixed(1)} y` : `${days} days`;
}

function TiingoForm({ lists, onDone }: { lists: Watchlist[]; onDone: (s: Snapshot) => void }) {
  const [watchlist, setWatchlist] = useState("");
  const [symbols, setSymbols] = useState("AAPL, MSFT, SPY");
  const [extras, setExtras] = useState("SPY");
  const [start, setStart] = useState("2020-01-01");
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const picked = lists.find((w) => w.name === watchlist);

  function pick(name: string) {
    const w = lists.find((x) => x.name === name);
    setWatchlist(name);
    if (!w) return;
    setLabel((prev) => (prev === "" || prev === watchlist ? w.name : prev));
    setExtras(w.members.includes("SPY") ? "" : "SPY");
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onDone(
        await api.importEquibles(
          picked
            ? { watchlist: picked.name, symbols: symbolsOf(extras), start, end, label, timeframe: "1h" }
            : { symbols: symbolsOf(symbols), start, end, label, timeframe: "1h" },
        ),
      );
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const extraCount = picked
    ? symbolsOf(extras).filter((s) => !picked.members.includes(s)).length
    : 0;
  const fetchCount = picked ? picked.members.length + extraCount : symbolsOf(symbols).length;
  const ready = fetchCount > 0;
  const preview = picked
    ? picked.members.length > 24
      ? `${picked.members.slice(0, 24).join(", ")} … and ${picked.members.length - 24} more`
      : picked.members.join(", ")
    : "";

  return (
    <form onSubmit={submit} data-testid="tiingo-form">
      <p className="muted small">
        Daily prices from Equibles. Needs <code>EQUIBLES_API_KEY</code>. A
        watchlist supplies the symbols; anything else you type is added. The default bar is one hour.
      </p>
      <div className="field">
        <label htmlFor="tiingo-watchlist">Watchlist</label>
        <select id="tiingo-watchlist" data-testid="tiingo-watchlist" value={watchlist} onChange={(e) => pick(e.target.value)}>
          <option value="">Type symbols</option>
          {lists.map((w) => (
            <option key={w.name} value={w.name}>
              {w.name} v{w.version} ({w.members.length})
            </option>
          ))}
        </select>
      </div>
      {picked ? (
        <div className="field">
          <label htmlFor="tiingo-extras">Also include</label>
          <input
            id="tiingo-extras"
            value={extras}
            onChange={(e) => setExtras(e.target.value)}
            placeholder="SPY"
          />
          <div className="help">
            {picked.members.length} symbols from {picked.name} v{picked.version}. SPY is filled in when the list does
            not already contain it, so this snapshot can be compared with the benchmark. Remove it if you do not want
            it fetched.
          </div>
          <div className="muted small">{preview}</div>
        </div>
      ) : (
        <div className="field">
          <label htmlFor="tiingo-symbols">Symbols</label>
          <input id="tiingo-symbols" value={symbols} onChange={(e) => setSymbols(e.target.value)} />
        </div>
      )}
      <div className="fields">
        <div className="field">
          <label htmlFor="tiingo-start">From</label>
          <input id="tiingo-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="tiingo-end">To</label>
          <input id="tiingo-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="tiingo-label">Label (optional)</label>
          <input id="tiingo-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="US large-cap 2020–" />
        </div>
      </div>
      <button className="primary" type="submit" disabled={busy || !ready}>
        {busy ? "Importing…" : `Import ${fetchCount} from Equibles`}
      </button>
      <ErrorBox error={error} title="Import failed" />
    </form>
  );
}

function CsvForm({ onDone }: { onDone: (s: Snapshot) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(undefined);
    try {
      const csv = await file.text();
      onDone(await api.importCsv({ csv, filename: file.name, label }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} data-testid="csv-form">
      <p className="muted small">
        One row per symbol per exchange session. Columns: <code>date, symbol, open, high, low, close, volume</code>.
        Prices should already be split- and dividend-adjusted.
      </p>
      <div className="fields">
        <div className="field">
          <label htmlFor="csv-file">CSV file</label>
          <input id="csv-file" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="field">
          <label htmlFor="csv-label">Label (optional)</label>
          <input id="csv-label" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
      </div>
      <button className="primary" type="submit" disabled={busy || !file}>
        {busy ? "Uploading…" : "Upload CSV"}
      </button>
      <ErrorBox error={error} title="Upload failed" />
    </form>
  );
}

function WatchlistForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await api.saveWatchlist({ name: name.trim(), symbols: symbolsOf(symbols), label });
      setSymbols("");
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} data-testid="watchlist-form">
      <div className="fields">
        <div className="field">
          <label htmlFor="wl-name">Watchlist name</label>
          <input id="wl-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="tech-large" />
        </div>
        <div className="field">
          <label htmlFor="wl-label">Why this list (optional)</label>
          <input id="wl-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="names I actually follow" />
        </div>
      </div>
      <div className="field">
        <label htmlFor="wl-symbols">Members</label>
        <input id="wl-symbols" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="AAPL MSFT NVDA" />
        <div className="help">A run records the version it used. Saving the same name again adds a new version.</div>
      </div>
      <button type="submit" disabled={busy || !name.trim() || !symbolsOf(symbols).length}>
        {busy ? "Saving…" : "Save watchlist"}
      </button>
      <ErrorBox error={error} title="Could not save the watchlist" />
    </form>
  );
}

export default function DataPage() {
  const snaps = useAsync(() => api.snapshots(), []);
  const lists = useAsync(() => api.watchlists(), []);
  const [last, setLast] = useState<Snapshot>();

  const imported = (s: Snapshot) => {
    setLast(s);
    snaps.reload();
  };

  const all = snaps.data ?? [];
  const symbols = new Set(all.flatMap((s) => s.symbols));
  const earliest = all.map((s) => s.first_date).sort()[0];
  const latest = all.map((s) => s.last_date).sort().at(-1);
  const rows = all.reduce((n, s) => n + (s.rows ?? 0), 0);

  return (
    <>
      <PageHeader
        title="Data"
        lead="Frozen price snapshots and watchlists. A run always points at one snapshot, so it can be repeated on the same bars."
      />

      <div className="tiles" style={{ marginBottom: 16 }}>
        <Tile label="Snapshots" value={int(all.length)} />
        <Tile label="Symbols" value={int(symbols.size)} sub={[...symbols].slice(0, 8).join(", ")} />
        <Tile
          label="Range"
          value={
            earliest && latest ? (
              <span className="tile-range">
                {earliest}
                <span>to {latest}</span>
              </span>
            ) : (
              "–"
            )
          }
          sub={spanDays(earliest, latest)}
        />
        <Tile label="Rows" value={int(rows)} />
      </div>

      {last ? (
        <p className="notice">
          Imported <strong>{last.name}</strong>: {int(last.rows)} rows, {last.symbols.length} symbols, {last.first_date}{" "}
          to {last.last_date}.
        </p>
      ) : null}

      <Section title="Snapshots" aside={<button onClick={snaps.reload}>Refresh</button>}>
        <ErrorBox error={snaps.error} title="Could not load snapshots" />
        {snaps.loading && !snaps.data ? <Loading /> : null}
        {snaps.data && !snaps.data.length ? (
          <p className="muted">No price data yet. Import from Equibles or upload a CSV below.</p>
        ) : null}
        {snaps.data && snaps.data.length ? (
          <div className="table-wrap">
            <table data-testid="snapshots-table">
              <thead>
                <tr>
                  <th>Snapshot</th>
                  <th>Source</th>
                  <th>Symbols</th>
                  <th>Range</th>
                  <th className="num">Rows</th>
                  <th>Adjustment</th>
                  <th>Imported</th>
                </tr>
              </thead>
              <tbody>
                {snaps.data.map((s) => (
                  <tr key={s.id}>
                    <td>
                      {s.name}
                      <div className="muted small">
                        <code>{s.id}</code>
                      </div>
                    </td>
                    <td>
                      <Badge kind={s.source_kind}>{s.source_label}</Badge>
                    </td>
                    <td className="small" title={s.symbols.join(", ")}>
                      {s.symbols.length}
                      {s.symbols.length ? ` · ${s.symbols.slice(0, 4).join(", ")}` : ""}
                      {s.symbols.length > 4 ? ` +${s.symbols.length - 4}` : ""}
                    </td>
                    <td className="small">
                      {s.first_date} → {s.last_date}
                      <div className="muted">{spanDays(s.first_date, s.last_date)}</div>
                    </td>
                    <td className="num">{int(s.rows)}</td>
                    <td className="small">{s.adjustment.replaceAll("_", " ")}</td>
                    <td className="small nowrap">{when(s.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Section>

      <Section title="Watchlists">
        <p className="caption">Runs record the version they used. Saving the same name again adds a new version.</p>
        <ErrorBox error={lists.error} title="Could not load watchlists" />
        {lists.data && lists.data.length ? (
          <div className="table-wrap">
            <table data-testid="watchlists-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="num">Version</th>
                  <th>Members</th>
                  <th>Selected</th>
                </tr>
              </thead>
              <tbody>
                {lists.data.map((w) => (
                  <tr key={`${w.name}-${w.version}`}>
                    <td>
                      {w.name}
                      {w.label ? (
                        <div className="muted small clamp-3" title={w.label}>
                          {w.label}
                        </div>
                      ) : null}
                    </td>
                    <td className="num">v{w.version}</td>
                    <td className="small" title={w.members.join(", ")}>
                      {w.members.length}
                      {w.members.length ? ` · ${w.members.slice(0, 6).join(", ")}` : ""}
                      {w.members.length > 6 ? ` +${w.members.length - 6}` : ""}
                    </td>
                    <td className="small nowrap">{when(w.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : lists.data ? (
          <p className="muted">No watchlists yet. You can still type symbols in the builder.</p>
        ) : null}
        <h3>Save a watchlist</h3>
        <WatchlistForm onDone={lists.reload} />
      </Section>

      <div className="grid-2">
        <Section title="Fetch real prices (Equibles)">
          <TiingoForm lists={lists.data ?? []} onDone={imported} />
        </Section>
        <Section title="Upload a CSV">
          <CsvForm onDone={imported} />
        </Section>
      </div>
    </>
  );
}
