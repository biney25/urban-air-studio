import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import {
  assertFiniteJson,
  caseConfig,
  caseFields,
  controlledDownloadPath,
  parseEvidenceRequest,
  type EvidenceKind,
} from "./evidence";
import { downloadProtected } from "./api";

const auth = vi.hoisted(() => ({ generation: 1, expire: vi.fn() }));
vi.mock("./auth", () => ({
  sessionGeneration: () => auth.generation,
  expireSession: auth.expire,
  requestHeaders: () =>
    new Headers({ Authorization: "Bearer fictional-memory-token" }),
}));
const core = {
  wind_from_deg: 270,
  reference_speed_m_s: 3,
  reference_height_m: 10,
  roughness_length_m: 0.1,
  kinematic_viscosity_m2_s: 0.000015,
  molecular_diffusivity_m2_s: 0.000001,
  turbulent_schmidt_number: 0.7,
  background_ug_m3: 0,
};
const fixture = (kind: EvidenceKind) =>
  readFileSync(
    new URL(
      `../public/examples/phase-b/${kind}-illustrative.json`,
      import.meta.url,
    ),
    "utf8",
  );

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  auth.generation = 1;
  auth.expire.mockClear();
});
describe("evidence envelope and source classification", () => {
  it.each([
    "inventory",
    "measurement_campaign",
    "replay_protocol",
    "replay_assessment",
  ] as EvidenceKind[])(
    "retains the complete explicitly illustrative %s template",
    (kind) => {
      const request = parseEvidenceRequest(fixture(kind), kind);
      expect(request.evidence_kind).toBe("illustrative");
      expect(request.rights.export).toBe(true);
      expect(Object.keys(request.payload).length).toBeGreaterThan(1);
    },
  );
  it("refuses nonfinite JSON before serialization can silently replace a reading with null", () => {
    expect(() =>
      assertFiniteJson({ readings: [null, -1, { value: Infinity }] }),
    ).toThrow(/finite/);
    expect(() =>
      parseEvidenceRequest(
        fixture("measurement_campaign").replace(
          /"raw_value":\s*-?[0-9.]+/,
          '"raw_value": 1e400',
        ),
        "measurement_campaign",
      ),
    ).toThrow(/finite/);
    expect(() => assertFiniteJson({ readings: [null, -1, 0] })).not.toThrow();
  });
  it("refuses wrong kind, missing metadata, unknown envelope fields and nonboolean permissions", () => {
    const request = JSON.parse(fixture("measurement_campaign"));
    for (const edit of [
      { kind: "replay_protocol" },
      { title: "" },
      { source_label: "" },
      { evidence_kind: "validated" },
      { extra: 1 },
      { rights: { derive: true, solve: 1, export: true } },
      { rights: { derive: true, solve: true, export: true, admin: true } },
    ])
      expect(() =>
        parseEvidenceRequest(
          JSON.stringify({ ...request, ...edit }),
          "measurement_campaign",
        ),
      ).toThrow();
  });
  it("cannot label imported predictions as observed measurements", () => {
    const request = JSON.parse(fixture("replay_assessment"));
    for (const evidence_kind of ["observed", "provider-modelled"])
      expect(() =>
        parseEvidenceRequest(
          JSON.stringify({ ...request, evidence_kind }),
          "replay_assessment",
        ),
      ).toThrow(/simulated or illustrative/);
  });
});
describe("physical case controls", () => {
  it("requires every physical field instead of guessing atmospheric values", () => {
    for (const field of caseFields) {
      const missing = { ...core } as Record<string, number>;
      delete missing[field.key];
      expect(() => caseConfig(missing, "{}")).toThrow(field.label);
    }
    expect(caseConfig(core, "{}")).toEqual(core);
  });
  it.each([
    { wind_from_deg: 360 },
    { background_ug_m3: 10001 },
    { reference_speed_m_s: NaN },
    { reference_height_m: 2, roughness_length_m: 0.1 },
  ])("rejects unsupported or incompatible physical inputs %o", (edit) =>
    expect(() => caseConfig({ ...core, ...edit }, "{}")).toThrow(),
  );
  it.each([
    '{"wind_from_deg": 90}',
    '{"unknown": 2}',
    '{"constructor": 2}',
    '{"base_cell_size_m": "8"}',
    '{"max_cells": 2000.5}',
    '{"max_wall_seconds": true}',
    '{"surface_refinement_level": 4}',
    "[]",
  ])("refuses core overrides and invalid advanced controls %s", (advanced) =>
    expect(() => caseConfig(core, advanced)).toThrow(),
  );
  it("checks source support against the resolved base cell and preserves approved controls", () => {
    expect(() => caseConfig(core, '{"base_cell_size_m": 20}')).toThrow(
      /Source support/,
    );
    expect(
      caseConfig(
        core,
        '{"base_cell_size_m": 4, "source_box_half_width_m": 3, "max_cells": 3000}',
      ).max_cells,
    ).toBe(3000);
  });
});
describe("controlled artifact downloads", () => {
  it.each([
    "/reports/report_1/download?format=html",
    "/reports/report_1/download?format=pdf",
    "/reports/report_1/download?format=json",
    "/projects/project_1/evidence/evidence_1/download",
    "/projects/project_1/detailed-cases/dcase_1/download",
  ])("permits generated controlled destination %s", (path) =>
    expect(controlledDownloadPath(path)).toBe(true),
  );
  it.each([
    "https://attacker.example/x",
    "//attacker.example/x",
    "/reports/../download?format=json",
    "/reports/r/download?format=json&url=x",
    "/projects/p/evidence/e%2F/download",
    "/projects/p/evidence/e/download?path=x",
    "/projects/p/evidence/e/download\n",
    "/api/projects/p/evidence/e/download",
    "/projects/p/evidence/e\\download",
  ])("refuses path manipulation %s", (path) =>
    expect(controlledDownloadPath(path)).toBe(false),
  );
  it("uses in-memory identity headers and no-store, then revokes the generated blob URL", async () => {
    vi.useFakeTimers();
    const clicked = vi.fn(),
      revoke = vi.fn(),
      fetcher = vi
        .fn()
        .mockResolvedValue(
          new Response("{}", {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("document", { createElement: () => ({ click: clicked }) });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fictional");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(revoke);
    await downloadProtected("/projects/p/evidence/e/download", "e.json");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/projects/p/evidence/e/download",
    );
    expect(fetcher.mock.calls[0][1].cache).toBe("no-store");
    expect(fetcher.mock.calls[0][1].headers.get("Authorization")).toBe(
      "Bearer fictional-memory-token",
    );
    expect(clicked).toHaveBeenCalledOnce();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith("blob:fictional");
  });
  it("does not deliver a download after the signed-in identity changes", async () => {
    const create = vi.spyOn(URL, "createObjectURL");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        headers: new Headers({ "Content-Type": "application/json" }),
        blob: async () => {
          auth.generation++;
          return new Blob(["{}"]);
        },
      }),
    );
    await expect(
      downloadProtected("/projects/p/evidence/e/download", "e.json"),
    ).rejects.toMatchObject({ code: "SESSION_CHANGED" });
    expect(create).not.toHaveBeenCalled();
  });
  it("expires identity on unauthorized responses and prevents unsafe filenames before requesting", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response("", { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      downloadProtected("/projects/p/evidence/e/download", "../e.json"),
    ).rejects.toThrow(/filename/);
    expect(fetcher).not.toHaveBeenCalled();
    await expect(
      downloadProtected("/projects/p/evidence/e/download", "e.json"),
    ).rejects.toMatchObject({ code: "DOWNLOAD_FAILED", status: 401 });
    expect(auth.expire).toHaveBeenCalledOnce();
  });
  it.each([
    {
      status: 204,
      body: null,
      type: "application/zip",
      code: "DOWNLOAD_FAILED",
    },
    {
      status: 200,
      body: "{}",
      type: "application/json",
      code: "DOWNLOAD_CONTENT_INVALID",
    },
    {
      status: 200,
      body: "",
      type: "application/zip",
      code: "DOWNLOAD_CONTENT_INVALID",
    },
  ])(
    "does not emit an empty or wrong-type case file %o",
    async ({ status, body, type, code }) => {
      const create = vi.spyOn(URL, "createObjectURL");
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(
            new Response(body, { status, headers: { "Content-Type": type } }),
          ),
      );
      await expect(
        downloadProtected("/projects/p/detailed-cases/c/download", "c.zip"),
      ).rejects.toMatchObject({ code });
      expect(create).not.toHaveBeenCalled();
    },
  );
});
