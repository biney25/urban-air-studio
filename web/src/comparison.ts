import type { Comparison, Field } from "./types";

export function comparisonMatchesSelection(
  data: Comparison | undefined,
  requestedProject: string | undefined,
  projectId: string,
  baselineRunId: string,
  alternativeRunId: string,
): data is Comparison {
  return (
    !!data &&
    requestedProject === projectId &&
    data.baseline_run_id === baselineRunId &&
    data.alternative_run_id === alternativeRunId
  );
}

export function matchedSliceIssue(a: Field, b: Field): string | null {
  if (
    a.units !== b.units ||
    a.height_m !== b.height_m ||
    a.time_support !== b.time_support
  )
    return "The slices have different units, height or time support. A matched field view is unavailable.";
  if (
    a.nx !== b.nx ||
    a.ny !== b.ny ||
    a.extent.some((v, i) => v !== b.extent[i])
  )
    return "These native display grids differ. Receptor metrics remain available; field resampling is not qualified.";
  return null;
}
