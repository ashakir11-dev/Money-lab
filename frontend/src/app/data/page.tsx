"use client";

import { useState, type FormEvent } from "react";
import { api, type Snapshot } from "@/lib/api";
import { int, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { Badge, ErrorBox, Loading, PageHeader, Section } from "@/components/ui";

const symbolsOf = (s: string) =>
  s
    .split(/[\s,]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);

function TiingoForm({ onDone }: { onDone: (s: Snapshot) => void }) {
  const [symbols, setSymbols] = useState("AAPL, MSFT, SPY");
  const [start, setStart] = useState("2020-01-01");
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onDone(await api.importTiingo({ symbols: symbolsOf(symbols), start, end, label }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} data-testid="tiingo-form">
      <p className="muted small">
        Downloads split- and dividend-adjusted daily prices. The server needs a <code>TIINGO_API_KEY</code>. Include the benchmark (SPY) in
        the list.
      </p>
      <div className="field">
        <label htmlFor="tiingo-symbols">Symbols</label>
        <input id="tiingo-symbols" value={symbols} onChange={(e) => setSymbols(e.target.value)} />
      </div>
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
          <input id="tiingo-label" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
      </div>
      <button className="primary" type="submit" disabled={busy}>
        {busy ? "Importing…" : "Import from Tiingo"}
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
        Columns: <code>date, symbol, open, high, low, close, volume</code>. Prices should already be adjusted.
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
          <label htmlFor="wl-name">Name</label>
          <input id="wl-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="tech-large" />
        </div>
        <div className="field">
          <label htmlFor="wl-label">Label (optional)</label>
          <input id="wl-label" value={label} onChange={(e) => setLabel(e.target.value)} />
        </div>
      </div>
      <div className="field">
        <label htmlFor="wl-symbols">Symbols</label>
        <input id="wl-symbols" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="AAPL MSFT NVDA" />
      </div>
      <button type="submit" disabled={busy || !name.trim() || !symbolsOf(symbols).length}>
        {busy ? "Saving…" : "Save watchlist"}
      </button>
      <p className="muted small">Saving under an existing name adds a new version; runs keep the version they used.</p>
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

  return (
    <>
      <PageHeader
        title="Data"
        lead="Price snapshots are frozen once imported, so a run can always be repeated on exactly the same prices."
      />

      {last ? (
        <p className="notice">
          Imported <strong>{last.label}</strong>: {int(last.rows)} rows, {last.symbols.length} symbols, {last.first_date} to {last.last_date}.
        </p>
      ) : null}

      <Section title="Snapshots" aside={<button onClick={snaps.reload}>Refresh</button>}>
        <ErrorBox error={snaps.error} title="Could not load snapshots" />
        {snaps.loading && !snaps.data ? <Loading /> : null}
        {snaps.data && !snaps.data.length ? <p className="muted">No price data yet. Import some below.</p> : null}
        {snaps.data && snaps.data.length ? (
          <div className="table-wrap">
            <table data-testid="snapshots-table">
              <thead>
                <tr>
                  <th>Snapshot</th>
                  <th>Source</th>
                  <th>Symbols</th>
                  <th>Dates</th>
                  <th className="num">Rows</th>
                  <th>Adjustment</th>
                  <th>Imported</th>
                </tr>
              </thead>
              <tbody>
                {snaps.data.map((s) => (
                  <tr key={s.id}>
                    <td>
                      {s.label}
                      <div className="muted small">
                        <code>{s.id}</code>
                      </div>
                    </td>
                    <td>
                      <Badge kind={s.source_kind}>{s.source_label}</Badge>
                    </td>
                    <td className="small">{s.symbols.join(", ")}</td>
                    <td className="small">
                      {s.first_date} to {s.last_date}
                    </td>
                    <td className="num">{int(s.rows)}</td>
                    <td className="small">{s.adjustment}</td>
                    <td className="small">{when(s.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Section>

      <div className="grid-2">
        <Section title="Import from Tiingo">
          <TiingoForm onDone={imported} />
        </Section>
        <Section title="Upload a CSV">
          <CsvForm onDone={imported} />
        </Section>
      </div>

      <Section title="Watchlists">
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
                      {w.label ? <div className="muted small">{w.label}</div> : null}
                    </td>
                    <td className="num">v{w.version}</td>
                    <td className="small">{w.members.join(", ")}</td>
                    <td className="small">{when(w.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : lists.data ? (
          <p className="muted">No watchlists yet.</p>
        ) : null}
        <h3 style={{ marginTop: 16 }}>Save a watchlist</h3>
        <WatchlistForm onDone={lists.reload} />
      </Section>
    </>
  );
}
