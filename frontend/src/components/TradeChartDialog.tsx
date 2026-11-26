"use client";

import { useEffect, useRef } from "react";
import { TradeChartView } from "@/components/TradeChartView";
import { ConditionsTable } from "@/components/tables";
import { ErrorBox, Loading } from "@/components/ui";
import { api, type ConditionResult } from "@/lib/api";
import { money } from "@/lib/format";
import { useAsync } from "@/lib/hooks";

export function TradeChartDialog({
  runId,
  decisionId,
  title,
  onClose,
}: {
  runId: string;
  decisionId: number;
  title: string;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const chart = useAsync(() => api.chart(runId, decisionId), [runId, decisionId]);
  useEffect(() => {
    closeRef.current?.focus();
  }, [decisionId]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const data = chart.loading ? undefined : chart.data;
  const noBars = !!data && data.candles.every((c) => c == null);
  const conditions = (data?.signal.conditions ?? []) as ConditionResult[];
  const timeframe =
    data?.timeframe === "1d"
      ? "1 day"
      : data?.timeframe === "1h"
        ? "1 hour"
        : data?.timeframe === "1min"
          ? "1 minute"
          : data?.timeframe
            ? data.timeframe.replace("min", " minutes")
            : "";

  return (
    <div className="trade-dialog-backdrop">
      <div className="trade-dialog" role="dialog" aria-modal="true" aria-labelledby="trade-chart-title">
        <header className="trade-dialog-head">
          <h2 id="trade-chart-title">{title}</h2>
          <button ref={closeRef} type="button" onClick={onClose}>
            Close
          </button>
        </header>
        {chart.loading ? <Loading what="Loading chart" /> : null}
        {chart.error && !chart.loading ? <ErrorBox error={chart.error} title="Could not load this chart" /> : null}
        {data ? (
          <div className="trade-dialog-grid">
            <div>{noBars ? <p>No bars in this window.</p> : <TradeChartView chart={data} />}</div>
            <aside className="trade-readout">
              <dl className="trade-facts">
                <dt>Timeframe</dt>
                <dd>{timeframe}</dd>
                <dt>Filled</dt>
                <dd>
                  {data.fill.session.replace("T", " ")} · {data.fill.side} · {money(data.fill.price)}
                </dd>
                <dt>Signal</dt>
                <dd>{data.signal.session.replace("T", " ")}</dd>
                <dt>Reason</dt>
                <dd>{data.signal.reason}</dd>
              </dl>
              {conditions.length ? (
                <div className="table-wrap">
                  <ConditionsTable conditions={conditions} />
                </div>
              ) : null}
            </aside>
          </div>
        ) : null}
      </div>
    </div>
  );
}
