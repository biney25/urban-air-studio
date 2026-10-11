import type { Comparison } from "./types";
export function comparisonOverview(comparison: Comparison) {
  const supported = comparison.metrics.filter(
    (metric) =>
      metric.valid !== false &&
      metric.delta_ug_m3 !== null &&
      Number.isFinite(metric.delta_ug_m3),
  );
  return {
    lower: supported.filter((metric) => metric.delta_ug_m3! < 0).length,
    higher: supported.filter((metric) => metric.delta_ug_m3! > 0).length,
    unchanged: supported.filter((metric) => metric.delta_ug_m3 === 0).length,
    missing: comparison.metrics.length - supported.length,
    protectedWorsening: supported
      .filter((metric) => metric.protected && metric.delta_ug_m3! > 0)
      .map((metric) => metric.name),
  };
}
