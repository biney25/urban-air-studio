import { describe, expect, it } from "vitest";
import { completedStudyRuns, comparisonForPair, previewMatches, sourceFactors, type ResponsePreview } from "./responseStudies";
import type { Comparison, Run, Scenario } from "./types";
describe("bounded response study controls", () => {
  it("keeps an explicit unchanged control and zero-emission assumption", () => expect(sourceFactors("0, 75, 100")).toEqual([0, .75, 1]));
  it.each(["", "10,", "50,50", "50,050", "201", "-1", "Infinity", "true", "1e2", "20,40,60,80"])("rejects ambiguous or unbounded rates %s", (v) => expect(() => sourceFactors(v)).toThrow());
  it("invalidates a preview when parent, brief or selected sources change", () => {
    const scenario = { id: "s1", version: 2, content_hash: "h1" } as Scenario;
    const preview = { name: "Study", parent_scenario_id: "s1", parent_version: 2, parent_hash: "h1", brief_hash: "b1", candidates: [{factor: .5, source_rates: [{source_id: "src"}]}] } as ResponsePreview;
    expect(previewMatches(preview, scenario, "b1", [.5], ["src"], "Study")).toBe(true);
    expect(previewMatches(preview, {...scenario, content_hash: "h2"}, "b1", [.5], ["src"], "Study")).toBe(false);
    expect(previewMatches(preview, {...scenario, version: 3}, "b1", [.5], ["src"], "Study")).toBe(false);
    expect(previewMatches(preview, scenario, "b1", [.5], ["src"], "Renamed")).toBe(false);
    expect(previewMatches(preview, scenario, "b2", [.5], ["src"], "Study")).toBe(false);
    expect(previewMatches(preview, scenario, "b1", [.75], ["src"], "Study")).toBe(false);
    expect(previewMatches(preview, scenario, "b1", [.5], ["other"], "Study")).toBe(false);
  });
  it("never borrows another scenario or failed run as a study result", () => {
    const rows = [{id:"r1",scenario_id:"s1",status:"failed"},{id:"r2",scenario_id:"s2",status:"complete_unqualified"},{id:"r3",scenario_id:"s1",status:"complete_unqualified"}] as Run[];
    expect(completedStudyRuns(rows, "s1").map((r) => r.id)).toEqual(["r3"]);
  });
  it("hides an old comparison immediately after either selected run changes", () => {
    const comparison = {baseline_run_id:"b", alternative_run_id:"a"} as Comparison;
    expect(comparisonForPair(comparison,"b","a")).toBe(comparison);
    expect(comparisonForPair(comparison,"new","a")).toBeUndefined();
    expect(comparisonForPair(comparison,"b","new")).toBeUndefined();
  });
});
