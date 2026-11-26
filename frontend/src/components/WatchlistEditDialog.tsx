"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ErrorBox } from "@/components/ui";
import { api, type Watchlist } from "@/lib/api";

const symbolsOf = (s: string) =>
  s
    .split(/[\s,]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);

export function WatchlistEditDialog({
  watchlist,
  onClose,
  onSaved,
}: {
  watchlist: Watchlist;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(watchlist.name);
  const [label, setLabel] = useState(watchlist.label);
  const [symbols, setSymbols] = useState(watchlist.members.join(" "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await api.saveWatchlist({ name: name.trim(), symbols: symbolsOf(symbols), label });
      onSaved();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const ready = name.trim().length > 0 && symbolsOf(symbols).length > 0;

  return (
    <div className="trade-dialog-backdrop">
      <div
        className="trade-dialog watchlist-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="watchlist-edit-title"
        data-testid="watchlist-edit-dialog"
      >
        <header className="trade-dialog-head">
          <h2 id="watchlist-edit-title">
            Edit {watchlist.name} v{watchlist.version}
          </h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <form onSubmit={submit}>
          <div className="fields">
            <div className="field">
              <label htmlFor="edit-wl-name">Watchlist name</label>
              <input
                id="edit-wl-name"
                data-testid="edit-wl-name"
                ref={nameRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="edit-wl-label">Why this list (optional)</label>
              <input
                id="edit-wl-label"
                data-testid="edit-wl-label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="edit-wl-symbols">Members</label>
            <input
              id="edit-wl-symbols"
              data-testid="edit-wl-symbols"
              value={symbols}
              onChange={(e) => setSymbols(e.target.value)}
            />
            <div className="help">
              The same name adds a version. A different name starts a new list, or the next version if that name already
              exists.
            </div>
          </div>
          <button className="primary" type="submit" data-testid="edit-wl-save" disabled={busy || !ready}>
            {busy ? "Saving…" : "Save watchlist"}
          </button>
          <ErrorBox error={error} title="Could not save the watchlist" />
        </form>
      </div>
    </div>
  );
}
