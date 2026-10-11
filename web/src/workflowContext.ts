import type { Report, Run } from "./types";

/** Preserve a chosen study's identity when moving between customer workspaces. */
export function workflowSearch(
  current?: URLSearchParams,
  changes: Record<string, string | null | undefined> = {},
) {
  const next = new URLSearchParams(current);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) next.delete(key);
    else if (value !== undefined && value !== "") next.set(key, value);
  }
  return next;
}

export function workflowPath(
  projectId: string,
  route: string,
  current?: URLSearchParams,
  changes: Record<string, string | null | undefined> = {},
) {
  const [pathname, explicit] = route.split("?", 2);
  const next = workflowSearch(current, changes);
  next.delete("project");
  if (explicit)
    new URLSearchParams(explicit).forEach((value, key) => next.set(key, value));
  const query = next.toString();
  return `/projects/${encodeURIComponent(projectId)}/${pathname}${query ? `?${query}` : ""}`;
}

export function frozenReportSelection(report: Report, runs: Run[]) {
  const manifests = report.manifest.run_manifests as
    { inputs?: { id?: string } }[] | undefined;
  return {
    baselineRunId: report.baseline_run_id,
    alternativeRunId: report.alternative_run_id,
    scenarioId:
      runs.find((run) => run.id === report.alternative_run_id)?.scenario_id ??
      manifests?.[1]?.inputs?.id ??
      "",
  };
}
