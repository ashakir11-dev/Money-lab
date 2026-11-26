import { Fragment } from "react";
import { settingsSections, settingsSummary } from "@/lib/settingsView";

export function SettingsPanel({ a, b }: { a: unknown; b: unknown }) {
  const sections = settingsSections(a, b);
  return (
    <details className="settings-panel" data-testid="config-diff">
      <summary>{settingsSummary(sections)}</summary>
      <div className="table-wrap">
        <table>
          <colgroup>
            <col className="settings-label" />
            <col />
            <col />
          </colgroup>
          <thead>
            <tr>
              <th>Setting</th>
              <th>A</th>
              <th>B</th>
            </tr>
          </thead>
          <tbody>
            {sections.map((section) => (
              <Fragment key={section.title}>
                <tr className="settings-section">
                  <th colSpan={3} scope="colgroup">
                    {section.title}
                  </th>
                </tr>
                {section.rows.map((row, index) => (
                  <tr key={`${row.label}-${index}`} className={row.changed ? "changed" : undefined}>
                    <td>{row.label}</td>
                    <td>{row.a}</td>
                    <td>{row.b}</td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
