import type { Building, Field, Scenario, Point } from "./types";
export function polygonArea(points: Point[]): number {
  return (
    Math.abs(
      points.reduce((area, p, i) => {
        const next = points[(i + 1) % points.length];
        return area + p[0] * next[1] - next[0] * p[1];
      }, 0),
    ) / 2
  );
}
export function center(building: Building): Point {
  return building.footprint.reduce<Point>(
    (sum, p) => [
      sum[0] + p[0] / building.footprint.length,
      sum[1] + p[1] / building.footprint.length,
    ],
    [0, 0],
  );
}
export function sceneExtent(s: Scenario): [number, number, number, number] {
  const points = [
    ...s.buildings.flatMap((b) => b.footprint),
    ...s.sources.map<Point>((p) => [p.x, p.y]),
    ...s.receptors.map<Point>((p) => [p.x, p.y]),
  ];
  if (!points.length) return [-100, -100, 100, 100];
  const xs = points.map((p) => p[0]),
    ys = points.map((p) => p[1]);
  return [
    Math.min(...xs) - 35,
    Math.min(...ys) - 35,
    Math.max(...xs) + 35,
    Math.max(...ys) + 35,
  ];
}
export function sampleField(
  field: Field,
  x: number,
  y: number,
): { value: number | null; reason: string | null } {
  const [xmin, ymin, xmax, ymax] = field.extent;
  if (
    !Number.isFinite(x) ||
    !Number.isFinite(y) ||
    x < xmin ||
    x >= xmax ||
    y < ymin ||
    y >= ymax
  )
    return { value: null, reason: "Outside the computed domain" };
  const ix = Math.min(
    field.nx - 1,
    Math.floor(((x - xmin) / (xmax - xmin)) * field.nx),
  );
  const iy = Math.min(
    field.ny - 1,
    Math.floor(((y - ymin) / (ymax - ymin)) * field.ny),
  );
  const i = iy * field.nx + ix;
  return field.mask[i] && field.values[i] !== null
    ? { value: field.values[i], reason: null }
    : { value: null, reason: "Masked solid or unavailable cell" };
}
export function fieldMaximum(fields: (Field | undefined)[]): number {
  let maximum = 1;
  for (const field of fields) {
    if (!field) continue;
    for (let i = 0; i < field.values.length; i++) {
      const value = field.values[i];
      if (field.mask[i] && typeof value === "number" && Number.isFinite(value))
        maximum = Math.max(maximum, value);
    }
  }
  return maximum;
}
export function fieldColor(value: number, max: number): string {
  const t = Math.max(0, Math.min(1, value / max));
  const a = [30, 64, 89],
    b = [245, 185, 95];
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
}
export function formatValue(value: number | null | undefined, decimals = 2) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-IN", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : "—";
}
export function shortId(value: string | undefined) {
  return value ? (value.split("_").at(-1) || value).slice(0, 8) : "—";
}
export function isActive(status: string) {
  return !["complete_unqualified", "failed", "cancelled"].includes(status);
}
export function isSynthetic(scenario: Scenario) {
  if (typeof scenario.synthetic === "boolean") return scenario.synthetic;
  return /synthetic|fictional/i.test(scenario.source_label ?? "");
}
