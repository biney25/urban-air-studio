import { describe, expect, it } from "vitest";
import { decodeResult } from "./api";
import { comparisonMatchesSelection, matchedSliceIssue } from "./comparison";
import {
  fieldMaximum,
  isSynthetic,
  polygonArea,
  sampleField,
  shortId,
} from "./geometry";
import { projectView } from "./viewState";
import type { Comparison, Result, Scenario } from "./types";

function validResult(): Result {
  return {
    field: {
      schema_version: "fields-v1",
      units: "ug/m3",
      nx: 2,
      ny: 2,
      height_m: 2.5,
      extent: [0, 0, 20, 20],
      values: [0, 12, null, 6],
      mask: [true, true, false, true],
      layout: "row_major_y_then_x",
      support: "native_cell_centres; piecewise_constant",
      time_support: "end_state_at_duration_s",
    },
    metrics: [
      {
        receptor_id: "gate",
        name: "Gate",
        valid: true,
        value_ug_m3: 6,
        requested_height_m: 1.7,
        cell_centre: [15, 15, 2.5],
      },
    ],
    diagnostics: {},
    evidence_kind: "simulated",
    qualification: "research_preview",
    limitations: ["No observational validation."],
  };
}

describe("field interpretation contract", () => {
  it("retains a valid zero separately from solid/no data", () => {
    const field = decodeResult(validResult()).field;
    expect(sampleField(field, 1, 1)).toEqual({ value: 0, reason: null });
    expect(sampleField(field, 1, 11).value).toBeNull();
    expect(sampleField(field, 11, 1).value).toBe(12);
  });
  it.each([
    [20, 1],
    [1, 20],
    [-0.1, 1],
    [1, NaN],
  ])("refuses an outside/native upper-bound sample (%s, %s)", (x, y) => {
    expect(sampleField(validResult().field, x, y).reason).toBe(
      "Outside the computed domain",
    );
  });
  it.each([
    ["schema_version", "fields-v2"],
    ["units", "ppm"],
    ["nx", 1.5],
    ["ny", 0],
    ["extent", [0, 0, 0, 20]],
    ["height_m", NaN],
    ["height_m", -1],
    ["layout", "x_then_y"],
    ["time_support", "hourly_average"],
    ["values", [0, 12]],
    ["values", [0, Infinity, null, 6]],
    ["mask", [1, true, false, true]],
    ["values", [0, 12, 0, 6]],
    ["values", [-1, 12, null, 6]],
  ])("blocks invalid %s before rendering", (key, value) => {
    const result = validResult();
    Object.assign(result.field, { [key]: value });
    expect(() => decodeResult(result)).toThrow(/has not been rendered/);
  });
  it("rejects sparse field arrays and a metric disguised as a number", () => {
    const result = validResult();
    delete result.field.values[1];
    expect(() => decodeResult(result)).toThrow();
    const wrong = validResult();
    Object.assign(wrong.metrics[0], { value_ug_m3: "6" });
    expect(() => decodeResult(wrong)).toThrow();
    const sparseBounds = validResult();
    delete sparseBounds.field.extent[1];
    expect(() => decodeResult(sparseBounds)).toThrow();
  });
  it("computes a shared maximum without spreading a million-cell array", () => {
    const field = validResult().field;
    field.values = Array(250_000).fill(2);
    field.mask = Array(250_000).fill(true);
    field.values[200_000] = 25;
    expect(fieldMaximum([field, validResult().field])).toBe(25);
  });
});

describe("paired comparison state", () => {
  const data = { baseline_run_id: "b", alternative_run_id: "a" } as Comparison;
  it("requires exact run IDs and project identity before exposing old results", () => {
    expect(comparisonMatchesSelection(data, "p", "p", "b", "a")).toBe(true);
    expect(comparisonMatchesSelection(data, "p", "other", "b", "a")).toBe(
      false,
    );
    expect(comparisonMatchesSelection(data, "p", "p", "b", "new")).toBe(false);
    expect(comparisonMatchesSelection(data, undefined, "p", "b", "a")).toBe(
      false,
    );
  });
  it("permits changed-solid masks but refuses unmatched display grids", () => {
    const a = validResult().field,
      b = validResult().field;
    b.mask = [true, false, false, true];
    b.values = [0, null, null, 6];
    expect(matchedSliceIssue(a, b)).toBeNull();
    b.extent = [0, 0, 30, 20];
    expect(matchedSliceIssue(a, b)).toMatch(/grids differ/);
    b.extent = a.extent;
    b.height_m = 5;
    expect(matchedSliceIssue(a, b)).toMatch(/height/);
  });
});

describe("site-independent UI state", () => {
  it("distinguishes prefixed project, scenario and run identities in compact displays", () => {
    expect(shortId("scenario_8ba57acf3345")).toBe("8ba57acf");
    expect(shortId("scenario_129ea8176678")).toBe("129ea817");
    expect(shortId("project_23fa23918d5e")).toBe("23fa2391");
    expect(shortId(undefined)).toBe("—");
  });
  it("uses explicit input provenance, including real imports with illustrative assumptions", () => {
    const imported = {
      synthetic: false,
      source_label: "synthetic-labelled user file",
      evidence_kind: "illustrative",
    } as Scenario;
    expect(isSynthetic(imported)).toBe(false);
    expect(isSynthetic({ ...imported, synthetic: true })).toBe(true);
  });
  it("computes footprint area independent of vertex orientation", () => {
    const square: [number, number][] = [
      [0, 0],
      [2, 0],
      [2, 3],
      [0, 3],
    ];
    expect(polygonArea(square)).toBe(6);
    expect(polygonArea([...square].reverse())).toBe(6);
  });
  it.each([
    "assistant",
    "optimisations/search",
    "review",
    "settings",
  ])("keeps the gated %s route explicit", (route) => {
    expect(projectView(`/projects/p/${route}`).view).toBe(route.split("/")[0]);
  });
  it("preserves blueprint scenario/result routes and handles unknown paths", () => {
    expect(projectView("/projects/p/validation").view).toBe("evidence");
    expect(projectView("/projects/p/evidence").view).toBe("evidence");
    expect(projectView("/projects/p/scenarios/s").view).toBe("design");
    expect(projectView("/projects/p/runs/r").view).toBe("result");
    expect(projectView("/projects/p/experiments/new").view).toBe("study");
    expect(projectView("/projects/p/no-such-screen").view).toBe("unknown");
  });
});
