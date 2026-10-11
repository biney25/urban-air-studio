import type { Comparison, Run, Scenario } from "./types";

export interface ResponseCandidate {
  factor: number;
  name: string;
  scenario_id?: string;
  unchanged_control?: boolean;
  source_rates: { source_id: string; name?: string; before_g_s?: number; after_g_s?: number; rate_g_s?: number }[];
  validation: { valid: boolean; status?: string; issues?: { message: string }[] };
}
export interface ResponsePreview {
  id: string;
  name: string;
  content_hash: string;
  parent_scenario_id: string;
  parent_hash: string;
  parent_version: number;
  brief_hash: string;
  candidates: ResponseCandidate[];
  requires_confirmation: true;
  qualified: false;
}
export interface ResponseStudy {
  id: string;
  name: string;
  created_at: string;
  parent_scenario_id: string;
  parent_hash: string;
  brief_hash: string;
  candidates: ResponseCandidate[];
  qualified: false;
}
export function sourceFactors(text: string): number[] {
  const parts = text.split(",").map((v) => v.trim());
  if (!parts.length || parts.length > 3 || parts.some((v) => !/^\d+(?:\.\d+)?$/.test(v)))
    throw new Error("Enter one to three percentages separated by commas, for example 50, 75, 100.");
  const values = parts.map((v) => Number(v) / 100);
  if (values.some((v) => !Number.isFinite(v) || v < 0 || v > 2) || new Set(values).size !== values.length)
    throw new Error("Use different percentages between 0 and 200. These change source rates, not predicted pollution.");
  return values;
}
export function previewMatches(preview: ResponsePreview | undefined, scenario: Scenario | undefined, briefHash: string | undefined, factors: number[], sourceIds: string[], name: string): boolean {
  if (!preview || !scenario || !briefHash || preview.parent_scenario_id !== scenario.id || preview.parent_hash !== scenario.content_hash || preview.parent_version !== scenario.version || preview.brief_hash !== briefHash || preview.name !== name)
    return false;
  if (preview.candidates.length !== factors.length || !preview.candidates.every((c, i) => c.factor === factors[i])) return false;
  return preview.candidates.every((c) => c.source_rates.length === sourceIds.length && c.source_rates.every((r) => sourceIds.includes(r.source_id)));
}
export function completedStudyRuns(runs: Run[], scenarioId: string): Run[] {
  return runs.filter((run) => run.scenario_id === scenarioId && run.status === "complete_unqualified");
}
export function comparisonForPair(comparison: Comparison | undefined, base: string, alternative: string): Comparison | undefined {
  return comparison?.baseline_run_id === base && comparison.alternative_run_id === alternative ? comparison : undefined;
}
