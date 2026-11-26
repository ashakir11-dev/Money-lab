"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export type SectionTone = "setup" | "signal" | "entry" | "exit" | "size" | "positions" | "capital";

function Mark({ tone }: { tone: SectionTone }) {
  const common = { viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.6 } as const;
  if (tone === "signal") return <svg {...common}><path d="M1 10c2-6 3 6 5 0s3 6 5 0 2-4 4-1" /></svg>;
  if (tone === "entry") return <svg {...common}><path d="M2 8h10M9 4l4 4-4 4" /></svg>;
  if (tone === "exit") return <svg {...common}><path d="M6 3H3v10h3M6 8h8M11 5l3 3-3 3" /></svg>;
  if (tone === "size") return <svg {...common}><rect x="2" y="9" width="3" height="5" /><rect x="6.5" y="5" width="3" height="9" /><rect x="11" y="2" width="3" height="12" /></svg>;
  if (tone === "positions") return <svg {...common}><rect x="2" y="3" width="5" height="4" /><rect x="9" y="3" width="5" height="4" /><rect x="2" y="9" width="5" height="4" /><rect x="9" y="9" width="5" height="4" /></svg>;
  if (tone === "capital") return <svg {...common}><rect x="2" y="4" width="12" height="9" rx="1.5" /><path d="M2 7h12" /></svg>;
  return <svg {...common}><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M2 6h12" /></svg>;
}

/** The ! on a block. Explains the block without leaving the explanation on the card. */
export function BlockHelp({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const noteId = useId();

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!text) return null;
  return (
    <span className="block-help" ref={root}>
      <button
        type="button"
        className="block-help-btn"
        aria-expanded={open}
        aria-controls={noteId}
        aria-label={`What ${label} means`}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        !
      </button>
      {open ? (
        <span className="block-help-pop" id={noteId} role="note">
          {text}
        </span>
      ) : null}
    </span>
  );
}

export type BlockIconName =
  | "pulse"
  | "cross"
  | "level"
  | "compare"
  | "arrow"
  | "range"
  | "dip"
  | "stop"
  | "target"
  | "trail"
  | "clock"
  | "bars"
  | "grid"
  | "cash"
  | "cost"
  | "rank";

export function BlockIcon({ name }: { name: BlockIconName }) {
  const common = { viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.6 } as const;
  if (name === "cross" || name === "trail") return <svg {...common}><path d="M2 12l4-4 3 2 5-6" /></svg>;
  if (name === "level") return <svg {...common}><path d="M2 10h12M8 10V4" /></svg>;
  if (name === "compare") return <svg {...common}><path d="M3 5h10M3 11h10M6 3v4M10 9v4" /></svg>;
  if (name === "arrow") return <svg {...common}><path d="M2 8h10M9 5l4 3-4 3" /></svg>;
  if (name === "range") return <svg {...common}><path d="M4 4v8M12 4v8M4 8h8" /></svg>;
  if (name === "dip" || name === "stop") return <svg {...common}><path d="M8 3v7M5 7l3 4 3-4" /></svg>;
  if (name === "target") return <svg {...common}><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3.5L10.5 10" /></svg>;
  if (name === "clock") return <svg {...common}><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3.5L10.5 10" /></svg>;
  if (name === "bars") return <svg {...common}><rect x="2" y="9" width="3" height="5" /><rect x="6.5" y="5" width="3" height="9" /><rect x="11" y="2" width="3" height="12" /></svg>;
  if (name === "grid") return <svg {...common}><rect x="2" y="3" width="5" height="4" /><rect x="9" y="3" width="5" height="4" /><rect x="2" y="9" width="5" height="4" /><rect x="9" y="9" width="5" height="4" /></svg>;
  if (name === "cash") return <svg {...common}><rect x="2" y="4" width="12" height="9" rx="1.5" /><path d="M2 7h12" /></svg>;
  if (name === "cost") return <svg {...common}><path d="M3 12l4-8 2 4 4-3" /></svg>;
  if (name === "rank") return <svg {...common}><path d="M3 4h10M3 8h7M3 12h4" /></svg>;
  return <svg {...common}><path d="M1 10c2-6 3 6 5 0s3 6 5 0 2-4 4-1" /></svg>;
}

/** Closed: one short row. Open: the same row, then the controls. */
export function ViewBlock({
  icon,
  summary,
  help,
  open,
  onOpen,
  onRemove,
  removeLabel,
  testId,
  children,
}: {
  icon: BlockIconName;
  summary: string;
  help: string;
  open: boolean;
  onOpen: () => void;
  onRemove?: () => void;
  removeLabel?: string;
  testId?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`view-block${open ? " is-open" : ""}`} data-testid={testId} onClick={onOpen}>
      <div className="view-face">
        <span className="view-icon" aria-hidden>
          <BlockIcon name={icon} />
        </span>
        <span className="cond-sentence view-summary" title={summary}>
          {summary}
        </span>
      </div>
      <BlockHelp label={summary} text={help} />
      {open && onRemove ? (
        <button
          type="button"
          className="link view-remove"
          aria-label={removeLabel ?? "Remove"}
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
        >
          Remove
        </button>
      ) : null}
      {open ? (
        <div className="view-edit" onClick={(event) => event.stopPropagation()}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

/** A selectable chip. The button class is only `on` when selected. */
export function ChoiceBlock({
  selected,
  testId,
  label,
  help,
  icon,
  onClick,
  children,
}: {
  selected: boolean;
  testId?: string;
  label: string;
  help?: string;
  icon: BlockIconName;
  onClick: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={`choice-block${selected && children ? " is-open" : ""}`}>
      <button
        type="button"
        data-testid={testId}
        className={selected ? "on" : undefined}
        onClick={(event) => {
          event.stopPropagation();
          onClick();
        }}
      >
        <span className="view-icon" aria-hidden>
          <BlockIcon name={icon} />
        </span>
        <span className="view-summary">{label}</span>
      </button>
      {help ? <BlockHelp label={label} text={help} /> : null}
      {selected && children ? <div className="view-edit">{children}</div> : null}
    </div>
  );
}

/** A category card. The heading, actions, and chips stay inside one block. */
export function SectionWrap({
  tone,
  title,
  count,
  action,
  children,
}: {
  tone: SectionTone;
  title: string;
  count?: number;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`block-section tone-${tone}`} data-testid={`section-${tone}`}>
      <header className="block-section-head">
        <span className="block-section-mark" aria-hidden>
          <Mark tone={tone} />
        </span>
        <h2>{title}</h2>
        {count != null ? <span className="block-section-count">{count}</span> : null}
        {action}
      </header>
      {children}
    </section>
  );
}

