import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import catalogueFixture from "./fixtures/phase-b-qualification-requirements.json";
import {
  controlledDownloadPath,
  decodeScientificRequirements,
  hasVerifiedNativeRuntime,
  type Readiness,
  type NativeRuntime,
} from "./evidence";
import {
  MethodStatusBanner,
  ScientificGateCatalogue,
  ScientificReviewCard,
} from "./components/EvidenceWorkspace";

// The catalogue is a source-generated contract fixture, not executed science.
const project = "project_" + "1".repeat(32);
const hash = "sha256:" + "2".repeat(64);
const fixture: Readiness = {
  schema_version: "phase-b-readiness-v1",
  project_id: project,
  status: "unqualified",
  detailed_method: {
    qualified: false,
    runtime_available: false,
    distribution: "OpenFOAM Foundation",
    version: "13",
    limitations: [],
  },
  native_runtime: {
    available: true,
    qualified: false,
    runtime_digest: hash,
    runtime_identity: { version: "13" },
  },
  native_executions: [],
  research_preview: {},
  packages: [],
  evidence: [],
  cases: [],
  local_resource_observations: {},
  authorization_method: {} as Readiness["authorization_method"],
  scientific_requirements: catalogueFixture,
  review_packet_url: `/api/projects/${project}/qualification/review-packet`,
};

describe("separate execution and scientific release readiness", () => {
  it("renders actual verified native readiness even when a compiler preparation record says unavailable", () => {
    const html = renderToStaticMarkup(
      createElement(MethodStatusBanner, { data: fixture }),
    );
    expect(html).toContain("Pinned native runtime verified");
    expect(html).toContain("scientific release gates remain unmet");
    expect(html).toContain("No T2 result admitted");
    expect(html).not.toContain("Solver runtime unavailable");
  });
  it.each([
    undefined,
    { available: false },
    { available: "yes" },
    { available: true, qualified: true },
    {
      available: true,
      qualified: false,
      runtime_digest: "caller assertion",
      runtime_identity: {},
    },
    { available: true, qualified: false, runtime_digest: hash },
  ])(
    "cannot infer runtime availability from incomplete or asserted readiness %j",
    (value) => {
      expect(hasVerifiedNativeRuntime(value as NativeRuntime)).toBe(false);
      const html = renderToStaticMarkup(
        createElement(MethodStatusBanner, {
          data: {
            ...fixture,
            detailed_method: {
              ...fixture.detailed_method,
              runtime_available: true,
            },
            native_runtime: value as NativeRuntime,
          },
        }),
      );
      expect(html).toContain("Pinned native runtime is not verified");
    },
  );
  it("shows all 25 release requirements without promoting native diagnostics or uploads", () => {
    const parsed = decodeScientificRequirements(catalogueFixture);
    expect(parsed.gates).toHaveLength(25);
    const html = renderToStaticMarkup(
      createElement(ScientificGateCatalogue, { value: catalogueFixture }),
    );
    expect(html.match(/Not admitted/g) ?? []).toHaveLength(25);
    expect(html).toContain("At least 3 distinct frozen meshes");
    expect(html).toContain("Proposed project starting target");
    expect(html).toContain(
      "without satisfying its complete qualification requirement",
    );
  });
  it.each([
    (v: typeof catalogueFixture) => {
      v.qualified = true;
    },
    (v: typeof catalogueFixture) => {
      v.independent_human_review_required = false;
    },
    (v: typeof catalogueFixture) => {
      v.gates.pop();
    },
    (v: typeof catalogueFixture) => {
      v.gates[1] = v.gates[0];
    },
    (v: typeof catalogueFixture) => {
      v.gates[0].state = "passed";
    },
    (v: typeof catalogueFixture) => {
      v.gates[0].accepted_evidence_basis = ["illustrative"];
    },
    (v: typeof catalogueFixture) => {
      v.gates.find(
        (g) => g.gate_id === "mesh_sensitivity",
      )!.minimum_distinct_mesh_levels = 1;
    },
    (v: typeof catalogueFixture) => {
      v.gates.find(
        (g) => g.proposed_baseline_target,
      )!.proposed_baseline_target!.threshold = NaN;
    },
    (v: typeof catalogueFixture) => {
      const target = v.gates.find(
        (g) => g.proposed_baseline_target,
      )!.proposed_baseline_target!;
      Object.assign(target, { operator: ["lt"] });
    },
  ])(
    "rejects unsupported or corrupted catalogue rather than presenting a release",
    (mutate) => {
      const value = structuredClone(catalogueFixture);
      mutate(value);
      expect(() => decodeScientificRequirements(value)).toThrow(
        "No qualification can be inferred",
      );
      const html = renderToStaticMarkup(
        createElement(ScientificGateCatalogue, { value }),
      );
      expect(html).toContain('role="alert"');
      expect(html).not.toContain("25 scientific qualification requirements");
    },
  );
  it("offers only the exact project protected review manifest and describes pending independent review", () => {
    const html = renderToStaticMarkup(
      createElement(ScientificReviewCard, {
        data: fixture,
        pending: false,
        onDownload: () => {},
      }),
    );
    expect(html).toContain("Download scientific review manifest");
    expect(html).not.toContain("disabled");
    expect(html).toContain("No approved preregistered qualification protocol");
    expect(html).toContain("no scientist’s signature or signoff");
  });
  it.each([
    undefined,
    "https://untrusted.example/review",
    `/api/projects/${project}/qualification/review-packet?qualified=true`,
    "/api/projects/project_other/qualification/review-packet",
  ])(
    "cannot bind a review download to an unsupported destination %s",
    (route) => {
      const html = renderToStaticMarkup(
        createElement(ScientificReviewCard, {
          data: { ...fixture, review_packet_url: route },
          pending: false,
          onDownload: () => {},
        }),
      );
      expect(html).toMatch(/<button[^>]*disabled/);
    },
  );
  it("requires a controlled exact review download path", () => {
    expect(
      controlledDownloadPath(
        `/projects/${project}/qualification/review-packet`,
      ),
    ).toBe(true);
    for (const suffix of [
      "?url=external",
      "/../review-packet",
      "\n",
      "#other",
    ]) {
      expect(
        controlledDownloadPath(
          `/projects/${project}/qualification/review-packet${suffix}`,
        ),
      ).toBe(false);
    }
    expect(
      controlledDownloadPath("/projects/../qualification/review-packet"),
    ).toBe(false);
  });
});
