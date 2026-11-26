/** Family membership for duplicated runs. Matches `moneylab.lineage`. */

export type LineageRun = {
  id: string;
  parent_id?: string | null;
  name: string;
  created_at?: string;
};

const SUFFIX = /(?: \(copy\)| \(duplicate\)| v\d+)$/;
const VERSION = / v(\d+)$/;

export function rootId(runs: readonly LineageRun[], runId: string): string {
  const byId = new Map(runs.map((run) => [run.id, run]));
  const seen = new Set<string>();
  let current = runId;
  while (byId.has(current) && !seen.has(current)) {
    seen.add(current);
    const parent = byId.get(current)?.parent_id;
    if (!parent || !byId.has(parent)) return current;
    current = parent;
  }
  return runId;
}

export function familyOf<T extends LineageRun>(runs: readonly T[], runId: string): T[] {
  const root = rootId(runs, runId);
  return runs.filter((run) => rootId(runs, run.id) === root);
}

export function baseName(name: string): string {
  const stripped = name.replace(SUFFIX, "");
  return stripped || name;
}

export function nextVersionName(runs: readonly LineageRun[], sourceId: string): string {
  const root = rootId(runs, sourceId);
  const name = runs.find((run) => run.id === root)?.name ?? "Strategy";
  return `${baseName(name)} v${familyOf(runs, sourceId).length + 1}`;
}

export function versionNumber(runs: readonly LineageRun[], runId: string): number {
  const run = runs.find((item) => item.id === runId);
  const match = run?.name.match(VERSION);
  if (match) return Number(match[1]);
  const members = familyOf(runs, runId).slice().sort((a, b) => {
    const created = (a.created_at ?? "").localeCompare(b.created_at ?? "");
    return created !== 0 ? created : a.id.localeCompare(b.id);
  });
  const index = members.findIndex((item) => item.id === runId);
  return index >= 0 ? index + 1 : 1;
}

export function families<T extends LineageRun>(runs: readonly T[]): { rootId: string; runs: T[] }[] {
  const roots = new Set(runs.map((run) => rootId(runs, run.id)));
  const groups = [...roots].map((root) => ({
    rootId: root,
    runs: runs
      .filter((run) => rootId(runs, run.id) === root)
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.id.localeCompare(b.id)),
  }));
  const newest = (group: { runs: readonly LineageRun[] }) =>
    group.runs.reduce((max, run) => ((run.created_at ?? "") > max ? (run.created_at ?? "") : max), "");
  groups.sort((a, b) => newest(b).localeCompare(newest(a)) || a.rootId.localeCompare(b.rootId));
  return groups;
}

export function withRun<T extends LineageRun>(runs: T[], run: T): T[] {
  return runs.some((item) => item.id === run.id) ? runs : [...runs, run];
}
