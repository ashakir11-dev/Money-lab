"use client";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { ErrorBox, Loading, PageHeader } from "@/components/ui";

export default function GatesPage() {
  const gates = useAsync(() => api.gates(), []);
  const list = gates.data ?? [];
  const passed = list.filter((g) => g.passing).length;
  return (
    <>
      <PageHeader
        title="Acceptance gates"
        lead={
          gates.data
            ? `${passed} of ${list.length} gates pass. Re-check from a terminal: uv run moneylab acceptance`
            : undefined
        }
      />
      <ErrorBox error={gates.error} title="Could not load the gates" />
      {gates.loading ? <Loading /> : null}
      <div className="gates" data-testid="gates">
        {list.map((g) => (
          <div className="tile gate" key={g.number} data-testid={`gate-${g.number}`} data-passing={g.passing}>
            <div className="tile-label">Gate {g.number}</div>
            <div className="tile-value" style={{ fontSize: "1rem" }}>
              {g.title} · <span className={g.passing ? "pos" : "muted"}>{g.passing ? "Pass" : "Pending"}</span>
            </div>
            {g.depends_on ? (
              <p className="small">
                <strong>Waiting on:</strong> {g.depends_on}
              </p>
            ) : null}
            <p className="small muted">Evidence: {g.evidence.join(", ")}</p>
          </div>
        ))}
      </div>
    </>
  );
}
