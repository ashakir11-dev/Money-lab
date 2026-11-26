import { settingsSections, settingsSummary } from "@/lib/settingsView";

export function SettingsPanel({ a, b }: { a: unknown; b: unknown }) {
  const sections = settingsSections(a, b);
  return (
    <details className="settings-panel" data-testid="config-diff">
      <summary>{settingsSummary(sections)}</summary>
      {sections.map((section) => (
        <section key={section.title}>
          <h3>{section.title}</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Setting</th>
                  <th>A</th>
                  <th>B</th>
                </tr>
              </thead>
              <tbody>
                {section.rows.map((row, index) => (
                  <tr key={`${row.label}-${index}`} className={row.changed ? "changed" : undefined}>
                    <td>{row.label}</td>
                    <td>{row.a}</td>
                    <td>{row.b}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </details>
  );
}
