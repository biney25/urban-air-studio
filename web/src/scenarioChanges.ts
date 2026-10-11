import type { Scenario } from "./types";
import { formatValue } from "./geometry";

export function scenarioChanges(baseline?: Scenario, alternative?: Scenario) {
  if (!baseline || !alternative) return null;
  const buildings: string[] = [],
    sources: string[] = [],
    locations: string[] = [];
  for (const row of alternative.buildings) {
    const previous = baseline.buildings.find((item) => item.id === row.id);
    if (!previous) buildings.push(`${row.name}: added`);
    else {
      const edits: string[] = [];
      if (previous.height_m !== row.height_m)
        edits.push(
          `height ${formatValue(previous.height_m, 1)} → ${formatValue(row.height_m, 1)} m`,
        );
      if (JSON.stringify(previous.footprint) !== JSON.stringify(row.footprint))
        edits.push("footprint changed");
      if (edits.length) buildings.push(`${row.name}: ${edits.join("; ")}`);
    }
  }
  for (const row of baseline.buildings)
    if (!alternative.buildings.some((item) => item.id === row.id))
      buildings.push(`${row.name}: removed`);
  for (const row of alternative.sources) {
    const previous = baseline.sources.find((item) => item.id === row.id);
    if (!previous) sources.push(`${row.name}: added`);
    else {
      const edits: string[] = [];
      if (previous.rate_g_s !== row.rate_g_s)
        edits.push(
          `declared rate ${formatValue(previous.rate_g_s, 3)} → ${formatValue(row.rate_g_s, 3)} g/s`,
        );
      if (previous.x !== row.x || previous.y !== row.y || previous.z !== row.z)
        edits.push("position changed");
      if (edits.length) sources.push(`${row.name}: ${edits.join("; ")}`);
    }
  }
  for (const row of baseline.sources)
    if (!alternative.sources.some((item) => item.id === row.id))
      sources.push(`${row.name}: removed`);
  for (const row of alternative.receptors) {
    const previous = baseline.receptors.find((item) => item.id === row.id);
    if (!previous) locations.push(`${row.name}: added`);
    else if (
      previous.x !== row.x ||
      previous.y !== row.y ||
      previous.z !== row.z ||
      previous.protected !== row.protected
    )
      locations.push(`${row.name}: support or protection designation changed`);
  }
  for (const row of baseline.receptors)
    if (!alternative.receptors.some((item) => item.id === row.id))
      locations.push(`${row.name}: removed`);
  return { buildings, sources, locations };
}
