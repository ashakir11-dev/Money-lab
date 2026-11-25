"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { api, type Delivery, type DecisionRow, type FillRow, type PaperStatus } from "@/lib/api";
import { int, money, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { DecisionsTable, FillsTable } from "@/components/tables";
import { ErrorBox, Loading, PageHeader, Section, Tile } from "@/components/ui";

function StartForm({ onStarted }: { onStarted: (id: string) => void }) {
  const runs = useAsync(() => api.runs(), []);
  const [runId, setRunId] = useState("");
  const [start, setStart] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const done = (runs.data ?? []).filter((r) => r.status === "completed");
  const chosen = runId || done[0]?.id || "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const acct = await api.startPaper({ run_id: chosen, start: start || null, name });
      onStarted(acct.id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (runs.loading) return <Loading />;
  if (!done.length) return <p className="muted">Save a run first: a paper account freezes a completed run.</p>;
  return (
    <form onSubmit={submit} data-testid="paper-start-form">
      <div className="fields">
        <div className="field">
          <label htmlFor="paper-run">Frozen run</label>
          <select id="paper-run" data-testid="paper-run" value={chosen} onChange={(e) => setRunId(e.target.value)}>
            {done.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} ({r.id})
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="paper-start">Last history session (blank: the data&apos;s last)</label>
          <input id="paper-start" data-testid="paper-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="paper-name">Name (optional)</label>
          <input id="paper-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      </div>
      <button className="primary" type="submit" data-testid="paper-create" disabled={busy || !chosen}>
        {busy ? "Starting…" : "Start paper account"}
      </button>
      <ErrorBox error={error} title="Not started" />
    </form>
  );
}

function DeliveryTable({ rows }: { rows: Delivery[] }) {
  if (!rows.length) return <p className="muted">Nothing new to deliver.</p>;
  return (
    <div className="table-wrap">
      <table data-testid="paper-deliveries">
        <thead>
          <tr>
            <th>Session</th>
            <th>Outcome</th>
            <th>Detail</th>
            <th>Processed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.session}-${i}`} data-status={r.status}>
              <td>{r.session}</td>
              <td>{r.status}</td>
              <td className="small">{r.detail}</td>
              <td className="small">{r.processed.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Account({ id }: { id: string }) {
  const detail = useAsync(() => api.paper(id), [id]);
  const [csv, setCsv] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<unknown>();
  const [last, setLast] = useState<Delivery[]>();
  const [replay, setReplay] = useState<Record<string, unknown>>();

  async function act(what: string, fn: () => Promise<void>) {
    setBusy(what);
    setError(undefined);
    try {
      await fn();
      detail.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy("");
    }
  }

  async function readFile(file: File | undefined) {
    if (file) setCsv(await file.text());
  }

  if (detail.error) return <ErrorBox error={detail.error} title="Could not load the paper account" />;
  if (!detail.data) return <Loading />;
  const st = detail.data.status as unknown as PaperStatus;
  const positions = Object.entries(st.positions);
  return (
    <>
      {st.blocked ? (
        <div className="error" role="alert">
          <strong>Processing blocked.</strong> {st.blocked}
        </div>
      ) : null}
      <div className="tiles" data-testid="paper-status">
        <Tile label="Latest processed session" value={<span data-testid="paper-latest">{st.latest_processed ?? "none yet"}</span>} />
        <Tile label="Next expected" value={<span data-testid="paper-next">{st.next_expected}</span>} />
        <Tile label="Cash" value={<span data-testid="paper-cash">{money(st.cash)}</span>} />
        <Tile label="Equity" value={money(st.equity)} />
        <Tile label="Open positions" value={<span data-testid="paper-positions">{int(positions.length)}</span>} />
      </div>
      <p className="muted small">
        Frozen run <Link href={`/run?id=${encodeURIComponent(st.run_id)}`}>{st.run_id}</Link>, history to {st.start_session}.{" "}
        {int(st.events)} deliveries received, {int(st.duplicates)} duplicates ignored.
      </p>

      <Section title="Deliver new sessions">
        <div className="actions">
          <button
            className="primary"
            data-testid="paper-fetch"
            disabled={!!busy}
            onClick={() => act("fetch", async () => setLast(await api.fetchPaper(id)))}
          >
            {busy === "fetch" ? "Fetching…" : "Fetch new sessions from Tiingo"}
          </button>
        </div>
        <div className="field">
          <label htmlFor="paper-csv">Or paste or load sessions (CSV: date,symbol,open,high,low,close,volume)</label>
          <input type="file" accept=".csv,text/csv" onChange={(e) => readFile(e.target.files?.[0])} />
          <textarea id="paper-csv" data-testid="paper-csv" rows={4} value={csv} onChange={(e) => setCsv(e.target.value)} />
        </div>
        <button
          data-testid="paper-ingest"
          disabled={!!busy || !csv.trim()}
          onClick={() => act("ingest", async () => setLast(await api.deliverPaper(id, csv)))}
        >
          {busy === "ingest" ? "Delivering…" : "Deliver sessions"}
        </button>
        <ErrorBox error={error} title="Delivery failed" />
        {last ? <DeliveryTable rows={last} /> : null}
      </Section>

      <Section title="Missing and conflicting data">
        <p className="small">
          Missing sessions (processing waits for them): {st.missing.join(", ") || "none"}
          <br />
          Received, waiting for an earlier session: {st.waiting.join(", ") || "none"}
          <br />
          Conflicting deliveries (the first accepted version stands): {st.conflicts.length || "none"} · Rejected deliveries:{" "}
          {st.rejected.length || "none"}
        </p>
      </Section>

      <Section title="Positions">
        {positions.length || Object.keys(st.pending_orders).length ? (
          <ul className="small">
            {positions.map(([sym, p]) => (
              <li key={sym}>
                {sym}: {int(p.qty)} shares
              </li>
            ))}
            {Object.entries(st.pending_orders).map(([sym, o]) => (
              <li key={`p-${sym}`}>
                {sym}: order waiting for the next open ({String(o.side ?? "buy")})
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Flat.</p>
        )}
      </Section>

      <Section title="Fills">
        <FillsTable fills={detail.data.fills as unknown as FillRow[]} empty="No fills yet." />
      </Section>
      <Section title="Decisions">
        <DecisionsTable rows={(detail.data.decisions as unknown as DecisionRow[]).slice().reverse()} />
      </Section>

      <Section title="Replay">
        <button
          data-testid="paper-replay"
          disabled={!!busy}
          onClick={() => act("replay", async () => setReplay(await api.replayPaper(id)))}
        >
          Replay recorded inputs and compare
        </button>
        {replay ? (
          replay.match ? (
            <p data-testid="paper-replay-result" data-match="true">
              Replay matches exactly: {String(replay.sessions)} sessions, {String(replay.decisions)} decisions, {String(replay.fills)} fills.
            </p>
          ) : (
            <div className="error" data-testid="paper-replay-result" data-match="false">
              <strong>Replay differs from the live record.</strong>
              <pre className="small">{JSON.stringify(replay, null, 2)}</pre>
            </div>
          )
        ) : null}
      </Section>
    </>
  );
}

function PaperPage() {
  const params = useSearchParams();
  const router = useRouter();
  const accounts = useAsync(() => api.paperAccounts(), []);
  const list = accounts.data ?? [];
  const selected = params.get("id") || list[0]?.id || "";
  const select = (id: string) => router.replace(`/paper?id=${encodeURIComponent(id)}`);

  return (
    <>
      <PageHeader title="Paper trading" lead="Freeze a saved run and feed it new sessions one at a time, as if trading forward." />
      <Section title="Start a paper account">
        <StartForm
          onStarted={(id) => {
            accounts.reload();
            select(id);
          }}
        />
      </Section>
      <ErrorBox error={accounts.error} title="Could not load paper accounts" />
      {list.length ? (
        <Section
          title="Account"
          aside={
            <select data-testid="paper-account" value={selected} onChange={(e) => select(e.target.value)}>
              {list.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name || a.id} · from {a.start_session} · {when(a.created_at)}
                </option>
              ))}
            </select>
          }
        >
          {selected ? <Account key={selected} id={selected} /> : null}
        </Section>
      ) : accounts.loading ? (
        <Loading />
      ) : (
        <p className="muted">No paper accounts yet.</p>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <PaperPage />
    </Suspense>
  );
}
