import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  allocationRequest,
  AllocationForm,
  AllocationSummaryView,
  decodeAllocationContext,
  decodeSourceAllocation,
  inventoryRoads,
  roadLength,
  type AllocationContext,
  type AllocationControls,
} from "./components/TrafficAllocationPanel";
import {
  controlledDownloadPath,
  type EvidenceRecord,
  type SourceAllocation,
} from "./evidence";
import type { Scenario } from "./types";

// All numeric rates/identities in these software fixtures are illustrative.
const project = "project_" + "1".repeat(32),
  hash = "sha256:" + "a".repeat(64);
const context: AllocationContext = {
  scenario_id: "scenario_parent",
  scenario_version: 1,
  scenario_hash: hash,
  coordinate_frame_hash: hash,
  coordinate_frame: { kind: "local_metre_test_fixture" },
  manual_source_count: 1,
  allocation_build_hash: hash,
  geometry_kind: "local_metre_polyline",
  units: "m",
  qualified: false,
  admitted_species: ["passive_tracer"],
  admitted_background: ["independent_inflow"],
};
const inventory: EvidenceRecord = {
  schema_version: "phase-b-evidence-v1",
  kind: "inventory",
  id: "evidence_" + "2".repeat(32),
  project_id: project,
  title: "Illustrative software inventory",
  created_at: "2026-10-10T00:00:00Z",
  content_hash: hash,
  evidence_kind: "illustrative",
  effective_evidence_kind: "illustrative",
  source_label: "software fixture",
  rights: { derive: true, solve: true, export: true },
  effective_rights: { derive: true, solve: true, export: true },
  dependencies: [],
  payload: {
    species: "passive_tracer",
    background: { mode: "independent_inflow", value_ug_m3: 10 },
    segments: [{ id: "road_a", length_m: 8 }],
  },
  summary: { total_rate_g_s: 1, total_range_g_s: [0.8, 1.2] },
};
const controls: AllocationControls = {
  max_element_length_m: 8,
  source_height_m: 2,
  max_source_elements: 200,
};
const roads = [
  {
    segment_id: "road_a",
    coordinates: [
      [-44, 0],
      [-36, 0],
    ] as [number, number][],
  },
];
const allocation: SourceAllocation = {
  schema_version: "phase-b-source-allocation-record-v1",
  id: "allocation_" + "3".repeat(32),
  title: "Illustrative frozen test allocation",
  qualified: false,
  status: "allocated_unexecuted",
  plan_hash: hash,
  project_id: project,
  scenario_id: "scenario_child",
  parent_scenario_id: context.scenario_id,
  inventory_evidence_id: inventory.id,
  content_hash: hash,
  artifact_checksum: hash,
  effective_rights: inventory.rights,
  effective_evidence_kind: "illustrative",
  summary: {
    species: "passive_tracer",
    interval_start_utc: "2026-10-10T00:00:00Z",
    interval_end_utc: "2026-10-10T01:00:00Z",
    total_rate_g_s: 1,
    total_range_g_s: [0.8, 1.2],
    source_count: 1,
    background_ug_m3: 10,
    range_basis:
      "declared_parameter_bounds_not_probability_or_confidence_interval",
    qualified: false,
  },
};
const scenario: Scenario = {
  id: context.scenario_id,
  project_id: project,
  name: "Illustrative parent",
  version: 1,
  content_hash: hash,
  parent_id: null,
  created_at: "2026-10-10T00:00:00Z",
  origin: { longitude: 77, latitude: 28 },
  buildings: [],
  sources: [],
  receptors: [],
  diagnostics: {},
  evidence_kind: "illustrative",
};

describe("explicit checked inventory-to-source UI", () => {
  it("uses the supplied host frame/scenario/inventory identities without JS rehashing or changing originals", () => {
    const before = JSON.stringify({ context, inventory, roads, controls });
    const request = allocationRequest(
      context,
      inventory,
      roads,
      controls,
      true,
    );
    expect(request.geometry.coordinate_frame_hash).toBe(
      context.coordinate_frame_hash,
    );
    expect(request.expected_scenario_hash).toBe(context.scenario_hash);
    expect(request.expected_inventory_hash).toBe(inventory.content_hash);
    expect(request.replace_manual_sources).toBe(true);
    expect(request.geometry.roads).toEqual(roads);
    expect(JSON.stringify({ context, inventory, roads, controls })).toBe(
      before,
    );
  });
  it("requires explicit manual-source replacement and source rights", () => {
    expect(() =>
      allocationRequest(context, inventory, roads, controls, false),
    ).toThrow("Confirm replacement");
    expect(() =>
      allocationRequest(
        context,
        {
          ...inventory,
          effective_rights: { ...inventory.rights, solve: false },
        },
        roads,
        controls,
        true,
      ),
    ).toThrow("must permit");
  });
  it.each([
    { species: "PM2.5" },
    { species: "NO2" },
    {
      background: { mode: "coarse_total_baseline_adjustment", value_ug_m3: 10 },
    },
  ])("refuses incompatible inventory physics %j", (edit) => {
    expect(() =>
      inventoryRoads({
        ...inventory,
        payload: { ...inventory.payload, ...edit },
      }),
    ).toThrow("compatible method");
  });
  it.each([
    [],
    [roads[0], roads[0]],
    [{ ...roads[0], segment_id: "another" }],
    [
      {
        ...roads[0],
        coordinates: [
          [NaN, 0],
          [-36, 0],
        ] as [number, number][],
      },
    ],
    [
      {
        ...roads[0],
        coordinates: [
          [-44, 0],
          [-35, 0],
        ] as [number, number][],
      },
    ],
  ])(
    "refuses omitted, duplicate, mismatched or unsupported roads %j",
    (edited) => {
      expect(() =>
        allocationRequest(context, inventory, edited, controls, true),
      ).toThrow();
    },
  );
  it.each([
    { source_height_m: NaN },
    { source_height_m: 0.2 },
    { max_element_length_m: 0 },
    { max_element_length_m: 101 },
    { max_source_elements: 1.5 },
    { max_element_length_m: 1, max_source_elements: 1 },
  ])("does not guess invalid physical controls %j", (edit) => {
    expect(() =>
      allocationRequest(
        context,
        inventory,
        roads,
        { ...controls, ...edit },
        true,
      ),
    ).toThrow();
  });
  it("keeps the 0.1% geometry support boundary and blank coordinates explicit", () => {
    expect(
      roadLength([
        [0, 0],
        [3, 4],
      ]),
    ).toBe(5);
    expect(
      roadLength([
        [NaN, 0],
        [3, 4],
      ]),
    ).toBeNull();
    expect(() =>
      allocationRequest(
        context,
        inventory,
        [
          {
            ...roads[0],
            coordinates: [
              [-44, 0],
              [-35.991, 0],
            ],
          },
        ],
        controls,
        true,
      ),
    ).toThrow("0.1%");
    const html = renderToStaticMarkup(
      createElement(AllocationForm, {
        context,
        record: inventory,
        scenario,
        pending: false,
        error: null,
        onAllocate: () => {},
      }),
    );
    expect(html).toMatch(
      /data-testid="allocation-road-0-point-0-x"[^>]*value=""/,
    );
    expect(html).toMatch(
      /data-testid="allocation-element-length"[^>]*value=""/,
    );
    expect(html).toMatch(/data-testid="allocation-source-height"[^>]*value=""/);
    expect(html).toMatch(/type="submit"[^>]*disabled/);
    expect(html).toContain("latitude/longitude values are incompatible");
  });
  it.each([
    { qualified: true },
    { units: "degrees" },
    { coordinate_frame_hash: "guessed" },
    { scenario_id: "another" },
    { scenario_version: 1.5 },
    { manual_source_count: true },
  ])("refuses unsafe host context %j", (edit) => {
    expect(() =>
      decodeAllocationContext({ ...context, ...edit }, context.scenario_id),
    ).toThrow("inconsistent");
  });
  it.each([
    { project_id: "another" },
    { qualified: true },
    { id: "../allocation" },
    { effective_evidence_kind: ["illustrative"] },
    { effective_rights: { derive: true, solve: true, export: 1 } },
    { summary: { ...allocation.summary, qualified: true } },
    { summary: { ...allocation.summary, total_rate_g_s: Infinity } },
    { summary: { ...allocation.summary, total_range_g_s: [1.1, 1.2] } },
  ])("rejects corrupted allocation metadata %j", (edit) => {
    expect(() =>
      decodeSourceAllocation({ ...allocation, ...edit }, project),
    ).toThrow("inconsistent");
  });
  it("renders actual retained mass/background/range with unqualified finite-source limits", () => {
    expect(decodeSourceAllocation(allocation, project)).toEqual(allocation);
    const html = renderToStaticMarkup(
      createElement(AllocationSummaryView, { allocation }),
    );
    expect(html).toContain("illustrative · unqualified");
    expect(html).toContain("0.8 – 1.2 g/s");
    expect(html).toContain("10 µg/m³");
    expect(html).toContain("no probability or confidence level inferred");
    expect(html).toContain("Native fluid volume");
    expect(
      controlledDownloadPath(
        `/projects/${project}/source-allocations/${allocation.id}/download`,
      ),
    ).toBe(true);
    expect(
      controlledDownloadPath(
        `/projects/${project}/source-allocations/${allocation.id}/download?path=x`,
      ),
    ).toBe(false);
  });
});
