"use client";

import type { ReactNode } from "react";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="muted" role="status">
      {what}…
    </p>
  );
}

export function ErrorBox({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="error" role="alert">
      <strong>{title}.</strong> {msg}
    </div>
  );
}

export function PageHeader({ title, lead, children }: { title: string; lead?: string; children?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {lead ? <p className="lead">{lead}</p> : null}
      </div>
      {children ? <div className="page-actions">{children}</div> : null}
    </header>
  );
}

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function Tile({ label, value, sub, title }: { label: string; value: ReactNode; sub?: ReactNode; title?: string }) {
  return (
    <div className="tile" title={title}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub ? <div className="tile-sub">{sub}</div> : null}
    </div>
  );
}

export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function JobStatus({ status, error }: { status?: string; error?: string | null }) {
  if (!status) return null;
  const text: Record<string, string> = {
    queued: "Queued…",
    running: "Running…",
    complete: "Done",
    failed: `Failed: ${error ?? "unknown error"}`,
  };
  return (
    <span data-testid="job-status" data-status={status} className={`job job-${status}`}>
      {text[status] ?? status}
    </span>
  );
}
