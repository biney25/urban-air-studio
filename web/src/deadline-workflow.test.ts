import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import {
  preparationPayload,
  type PreparationContext,
  type HeightDraft,
} from "./sitePreparation";
import {
  proposalCanApply,
  assistantEvidencePath,
  type AssistantProposal,
} from "./assistant";
import { comparisonOverview } from "./comparisonOverview";
import { TaskTrail } from "./components/WorkspaceNav";
import { JsonViewer } from "./components/UI";
import { projectView } from "./viewState";
import { frozenReportSelection, workflowPath } from "./workflowContext";
import { scenarioChanges } from "./scenarioChanges";
import type { Scenario, Comparison, Report, Run } from "./types";

const context: PreparationContext = {
  schema_version: "site-preparation-context-v1",
  project_id: "p",
  context_id: "ctx",
  context_hash: "sha256:" + "a".repeat(64),
  buildings: [
    {
      object_id: "osm1",
      name: "Building 1",
      height_m: null,
      height_basis: "missing",
      source_url: "https://www.openstreetmap.org/way/1",
      footprint: {},
    },
  ],
  unsupported_objects: [],
  context_only_road_count: 3,
  source_snapshot: {},
  licence_review: {},
  illustrative_study_template: {},
  limitations: [],
};
const review: HeightDraft = {
  height: "12",
  basis: "assumed",
  reference: "Explicit trial assumption; no measurement",
  excluded: false,
  reason: "",
};
const make = (patch: Partial<HeightDraft> = {}) =>
  preparationPayload(
    context,
    { osm1: { ...review, ...patch } },
    {},
    true,
    true,
    "geometry_only",
    false,
  );
const scenario: Scenario = {
  id: "s",
  project_id: "p",
  name: "Alternative",
  version: 2,
  content_hash: "hash",
  parent_id: "b",
  created_at: "2026-10-11",
  origin: { latitude: 28, longitude: 77 },
  buildings: [],
  sources: [],
  receptors: [],
  diagnostics: {},
  evidence_kind: "illustrative",
};
const proposal: AssistantProposal = {
  scenario_id: "s",
  expected_version: 2,
  input_hash: "hash",
  operations: [
    { type: "building_update", object_id: "osm1", changes: { height_m: 10 } },
  ],
  validation: {
    valid: true,
    geometry_valid: true,
    programme_status: "pass",
    issues: [],
  },
  requires_confirmation: true,
};

describe("Reviewed site handoff", () => {
  it("keeps exact source identity and explicit assumptions", () => {
    const payload = make();
    expect(payload.expected_context_hash).toBe(context.context_hash);
    expect(payload.heights[0]).toEqual({
      object_id: "osm1",
      height_m: 12,
      height_basis: "assumed",
      reference: review.reference,
      units: "m_AGL",
    });
    expect(payload.study_inputs.sources).toEqual([]);
    expect(payload.study_inputs.receptors).toEqual([]);
  });
  it.each(["", " ", "NaN", "0", "81"])(
    "does not manufacture a height from %s",
    (height) => expect(() => make({ height })).toThrow(),
  );
  it("requires a recorded reference for every retained building", () =>
    expect(() => make({ reference: "" })).toThrow());
  it("preserves unsupported omissions with a reason", () => {
    const unsupported = {
      ...context,
      unsupported_objects: [
        {
          object_id: "relation9",
          reason: "Hole unsupported",
          requires_exclusion: true,
        },
      ],
    };
    expect(() =>
      preparationPayload(
        unsupported,
        { osm1: review },
        {},
        true,
        true,
        "geometry_only",
        false,
      ),
    ).toThrow();
    expect(
      preparationPayload(
        unsupported,
        { osm1: review },
        { relation9: "Unsupported hole; investigate later" },
        true,
        true,
        "geometry_only",
        false,
      ).exclusions,
    ).toEqual([
      { object_id: "relation9", reason: "Unsupported hole; investigate later" },
    ]);
  });
  it("requires an explicit artificial-study acknowledgment", () =>
    expect(() =>
      preparationPayload(
        context,
        { osm1: review },
        {},
        true,
        true,
        "illustrative_point_study",
        false,
      ),
    ).toThrow());
  it("cannot bypass flat-ground and local rights review", () => {
    expect(() =>
      preparationPayload(
        context,
        { osm1: review },
        {},
        false,
        true,
        "geometry_only",
        false,
      ),
    ).toThrow();
    expect(() =>
      preparationPayload(
        context,
        { osm1: review },
        {},
        true,
        false,
        "geometry_only",
        false,
      ),
    ).toThrow();
  });
});

describe("Scoped assistant review", () => {
  it("admits only the exact checked editable version", () =>
    expect(proposalCanApply(proposal, scenario, "baseline")).toBe(true));
  it("rejects frozen baselines and stale hashes/versions", () => {
    expect(proposalCanApply(proposal, scenario, "s")).toBe(false);
    expect(
      proposalCanApply(proposal, { ...scenario, version: 3 }, "baseline"),
    ).toBe(false);
    expect(
      proposalCanApply(
        proposal,
        { ...scenario, content_hash: "different" },
        "baseline",
      ),
    ).toBe(false);
  });
  it("does not treat unchecked programme/geometry as permission", () => {
    expect(
      proposalCanApply(
        { ...proposal, validation: { ...proposal.validation, valid: false } },
        scenario,
        "baseline",
      ),
    ).toBe(false);
    expect(
      proposalCanApply(
        {
          ...proposal,
          validation: { ...proposal.validation, geometry_valid: false },
        },
        scenario,
        "baseline",
      ),
    ).toBe(false);
  });
  it("opens exact run evidence, without an arbitrary destination", () =>
    expect(
      assistantEvidencePath("p", { kind: "run", id: "r", label: "Run" }),
    ).toBe("/projects/p/runs/r"));
});

describe("Readable exact evidence", () => {
  it("does not count unavailable metrics as zero/no harm", () => {
    const comparison = {
      metrics: [
        { name: "Gate", protected: true, valid: true, delta_ug_m3: 2 },
        { name: "Neighbour", protected: false, valid: true, delta_ug_m3: -1 },
        { name: "No data", protected: true, valid: false, delta_ug_m3: null },
        { name: "Rejected", protected: true, valid: false, delta_ug_m3: 0 },
      ],
    } as Comparison;
    expect(comparisonOverview(comparison)).toEqual({
      higher: 1,
      lower: 1,
      unchanged: 0,
      missing: 2,
      protectedWorsening: ["Gate"],
    });
  });
  it("retains the selected alternative on progress-trail jumps", () => {
    const html = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(TaskTrail, {
          projectId: "p",
          active: "design",
          scenarioId: "alternative",
          site: { latitude: 28, longitude: 77, width_km: 0.25 },
        }),
      ),
    );
    expect(html).toContain("/projects/p/study?scenario=alternative");
    expect(html).toContain("/explore?project=p");
    expect(html).toContain("width=0.25");
  });
  it("keeps source-containing JSON inspectable without a browser export bypass", () => {
    const html = renderToStaticMarkup(
      createElement(JsonViewer, {
        data: { source: "restricted" },
        title: "Source evidence",
      }),
    );
    expect(html).toContain("restricted");
    expect(html).not.toContain("Download JSON");
  });
  it("keeps the original assistant route available", () =>
    expect(projectView("/projects/p/assistant").view).toBe("assistant"));
  it("keeps historical study/session/scope selection when navigating workspaces", () => {
    const current = new URLSearchParams({
      scenario: "older-alternative",
      base: "old-base-run",
      alt: "old-alt-run",
      assistant_session: "session",
      scope: "user",
      report: "frozen-report",
    });
    const next = new URL(
      workflowPath("p", "investigate", current),
      "http://local",
    );
    expect(Object.fromEntries(next.searchParams)).toEqual(
      Object.fromEntries(current),
    );
    expect(
      new URL(
        workflowPath("p", "reports", current, { report: null }),
        "http://local",
      ).searchParams.has("report"),
    ).toBe(false);
  });
  it("resolves a frozen report to its exact alternative even when newer runs exist", () => {
    const report = {
      baseline_run_id: "old-base",
      alternative_run_id: "old-alt",
      manifest: {
        run_manifests: [
          { inputs: { id: "base" } },
          { inputs: { id: "older-design" } },
        ],
      },
    } as unknown as Report;
    const runs = [
      { id: "new-alt", scenario_id: "latest-design" },
      { id: "old-alt", scenario_id: "older-design" },
    ] as Run[];
    expect(frozenReportSelection(report, runs)).toEqual({
      baselineRunId: "old-base",
      alternativeRunId: "old-alt",
      scenarioId: "older-design",
    });
    expect(frozenReportSelection(report, [])).toHaveProperty(
      "scenarioId",
      "older-design",
    );
  });
  it("reports missing scenario evidence as unavailable rather than unchanged", () => {
    expect(scenarioChanges(undefined, scenario)).toBeNull();
    expect(scenarioChanges(scenario, scenario)).toEqual({
      buildings: [],
      sources: [],
      locations: [],
    });
  });
  it("links authoritative assistant evidence to its frozen pair while retaining session context", () => {
    const url = new URL(
      assistantEvidencePath(
        "p",
        {
          kind: "comparison",
          id: "metric",
          label: "Exact pair",
          details: {
            baseline_run_id: "frozen-base",
            alternative_run_id: "frozen-alt",
          },
        },
        new URLSearchParams({
          base: "latest-base",
          alt: "latest-alt",
          assistant_session: "session",
        }),
      ),
      "http://local",
    );
    expect(url.searchParams.get("base")).toBe("frozen-base");
    expect(url.searchParams.get("alt")).toBe("frozen-alt");
    expect(url.searchParams.get("assistant_session")).toBe("session");
  });
});
