import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { DetailedCase } from "./evidence";
import DetailedExecutionPanel, {
  canRetryExecution,
  decodeExecutionJob,
  decodeExecutionReadiness,
  decodeExecutionReceipt,
  ExecutionReceiptView,
  decodeNativeFieldDiagnostics,
  NativeFieldDiagnosticsView,
  ExecutionRuntimeCard,
  executionStatusLabel,
  hasActiveExecution,
  preparedCaseCompilerState,
  type ExecutionJob,
  type ExecutionReadiness,
  type ExecutionReceipt,
} from "./components/DetailedExecutionPanel";

const project = "project_" + "a".repeat(32);
const id = "dexec_" + "b".repeat(32);
const caseId = "dcase_" + "c".repeat(32);
const receiptId = "dreceipt_" + "d".repeat(32);
const hash = "sha256:" + "1".repeat(64);
const readiness: ExecutionReadiness = {
  schema_version: "phase-b-execution-readiness-v1",
  project_id: project,
  available: true,
  qualified: false,
  mode: "research_diagnostic",
  runtime: {
    available: true,
    runtime_digest: hash,
    runtime_identity: { version: "13" },
  },
  admission: {
    qualified_tier_available: false,
    caller_attestation_allowed: false,
  },
};
const job: ExecutionJob = {
  schema_version: "phase-b-execution-job-v1",
  id,
  project_id: project,
  case_id: caseId,
  title: "Illustrative bounded diagnostic",
  status: "queued",
  qualified: false,
  mode: "research_diagnostic",
  numerical_verification_passed: false,
  created_at: "2026-10-10T00:00:00Z",
  attempt: 1,
  cancel_requested: false,
  frozen: { maximum_total_seconds: 100 },
  resource_use: { worker_seconds: 0, billed_currency: null },
  error: null,
  receipt_id: null,
  content_hash: hash,
};
const receipt: ExecutionReceipt = {
  schema_version: "phase-b-execution-receipt-v1",
  id: receiptId,
  project_id: project,
  execution_id: id,
  case_id: caseId,
  status: "diagnostic_only",
  qualified: false,
  numerical_verification_passed: false,
  diagnostics: {
    numerical_verification_passed: false,
    numerical_gates: { mesh_connectivity: true, mass_balance: false },
    issues: [
      {
        code: "ABL_FAILED",
        message: "The atmospheric profile drifted beyond the declared gate.",
      },
    ],
  },
  resources: { wall_seconds: 3.2 },
  native_execution_hash: hash,
};

const compilerCase: DetailedCase = {
  schema_version: "phase-b-case-v1",
  id: caseId,
  project_id: project,
  title: "Current frozen case",
  scenario_id: "scenario_fixture",
  scenario_version: 1,
  scenario_hash: hash,
  created_at: "2026-10-10T00:00:00Z",
  content_hash: hash,
  manifest: { method: { compiler_build_hash: hash } },
  rights: { derive: true, solve: true, export: true },
  file_count: 1,
  file_bytes: 10,
  bundle_checksum: hash,
  status: "prepared_unexecuted",
  qualified: false,
};

describe("immutable current and historical compiler cases", () => {
  it("distinguishes current from historical builds while keeping records immutable", () => {
    expect(preparedCaseCompilerState(compilerCase, hash)).toBe("current");
    expect(
      preparedCaseCompilerState(compilerCase, "sha256:" + "2".repeat(64)),
    ).toBe("historical");
    expect(compilerCase.status).toBe("prepared_unexecuted");
    expect(compilerCase.qualified).toBe(false);
  });
  it.each([undefined, "caller assertion", true])(
    "never infers readiness from an unsupported active build %s",
    (value) => {
      expect(preparedCaseCompilerState(compilerCase, value)).toBe("unverified");
    },
  );
  it("labels and disables old case selection alongside a current case without promoting runtime readiness", () => {
    const old = {
      ...compilerCase,
      id: "dcase_" + "e".repeat(32),
      title: "Retained old case",
      manifest: { method: { compiler_build_hash: "sha256:" + "2".repeat(64) } },
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    client.setQueryData(["detailed-runtime", project], {
      ...readiness,
      available: false,
      runtime: {},
    });
    client.setQueryData(["detailed-executions", project], []);
    const html = renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(DetailedExecutionPanel, {
          projectId: project,
          cases: [compilerCase, old],
          currentCompilerBuildHash: hash,
        }),
      ),
    );
    expect(html).toMatch(new RegExp(`<option value="${old.id}" disabled`));
    expect(html).toContain(
      "historical or unverified compiler; prepare a new case",
    );
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/);
    expect(html).toContain("Detailed runtime unavailable");
  });
});
const fields = {
  schema_version: "phase-b-native-field-diagnostics-v1",
  C_unit: "kg/m3",
  reported_C_unit: "ug/m3",
  ug_per_kg: 1e9,
  temporal_support:
    "stationary_solver_iteration_end_state_not_measured_interval_mean",
  terminal_iteration: 1000,
  qualified: false,
  numerical_verification_passed: false,
  mass_balance: { relative_imbalance: 0.00018356237709987733 },
  stationarity: [{ id: "downwind", passed: true }],
  unexecuted_required_gates: [
    "three_mesh_domain_closure_sensitivity",
    "independent_scientific_review",
  ],
  receptors: [
    {
      id: "downwind",
      cell: 234,
      C_kg_m3: 3e-8,
      C_ug_m3: 30,
      background_ug_m3: 10,
      local_increment_ug_m3: 20,
      U_m_s: [3, 4, 0],
      speed_m_s: 5,
      protected: true,
    },
  ],
};

describe("detailed diagnostic admission and presentation", () => {
  it("shows actual unqualified passive-tracer totals and signed increments with exact units and time support", () => {
    expect(decodeNativeFieldDiagnostics(fields).receptors[0].C_ug_m3).toBe(30);
    const html = renderToStaticMarkup(
      createElement(NativeFieldDiagnosticsView, { value: fields }),
    );
    expect(html).toContain("Native passive-tracer diagnostics");
    expect(html).toContain("Total µg/m³");
    expect(html).toContain("Background µg/m³");
    expect(html).toContain("Local increment µg/m³");
    expect(html).toContain("Speed m/s");
    expect(html).toContain("Cell 234 · protected");
    expect(html).toContain("not measured interval means, PM, AQI");
    expect(html).toContain("0.0183562%");
    expect(html).toContain("independent scientific review");
  });

  it.each([
    { C_unit: "ppm" },
    { reported_C_unit: "AQI" },
    { ug_per_kg: 1e6 },
    { qualified: true },
    { temporal_support: "hourly_observed_mean" },
    { terminal_iteration: true },
    { mass_balance: { relative_imbalance: -0.01 } },
    { receptors: [{ ...fields.receptors[0], C_ug_m3: 300 }] },
    { receptors: [{ ...fields.receptors[0], local_increment_ug_m3: 30 }] },
    { receptors: [{ ...fields.receptors[0], speed_m_s: 3 }] },
    { receptors: [{ ...fields.receptors[0], C_kg_m3: Infinity }] },
    { receptors: [{ ...fields.receptors[0], U_m_s: [3, 4] }] },
    { receptors: [fields.receptors[0], fields.receptors[0]] },
  ])(
    "refuses scientific field support, unit or quantity inconsistencies %j",
    (change) => {
      expect(() =>
        decodeNativeFieldDiagnostics({ ...fields, ...change }),
      ).toThrow("contract");
      const html = renderToStaticMarkup(
        createElement(NativeFieldDiagnosticsView, {
          value: { ...fields, ...change },
        }),
      );
      expect(html).toContain('role="alert"');
      expect(html).not.toContain("<table>");
    },
  );

  it("retains signed native local increments instead of clipping away adverse or roundoff results", () => {
    const signed = {
      ...fields,
      receptors: [
        {
          ...fields.receptors[0],
          C_kg_m3: 9e-9,
          C_ug_m3: 9,
          local_increment_ug_m3: -1,
        },
      ],
    };
    expect(
      decodeNativeFieldDiagnostics(signed).receptors[0].local_increment_ug_m3,
    ).toBe(-1);
    expect(
      renderToStaticMarkup(
        createElement(NativeFieldDiagnosticsView, { value: signed }),
      ),
    ).toContain(">-1</td>");
  });

  it("shows a verified runtime without implying scientific qualification", () => {
    expect(decodeExecutionReadiness(readiness, project)).toEqual(readiness);
    const html = renderToStaticMarkup(
      createElement(ExecutionRuntimeCard, { readiness }),
    );
    expect(html).toContain("Pinned runtime available");
    expect(html).toContain("does not establish air-quality accuracy");
    expect(html).toContain(
      "independent scientific review remain separate release gates",
    );
    expect(html).toContain(hash);
  });

  it("shows missing/unavailable status without fabricating a running solver", () => {
    const html = renderToStaticMarkup(createElement(ExecutionRuntimeCard));
    expect(html).toContain("Detailed runtime unavailable");
    expect(html).toContain("has not been supplied");
    const unavailable = {
      ...readiness,
      available: false,
      runtime: { available: false, reason: "Hash verification failed closed" },
    };
    expect(
      renderToStaticMarkup(
        createElement(ExecutionRuntimeCard, { readiness: unavailable }),
      ),
    ).toContain("Hash verification failed closed");
  });

  it.each([
    { qualified: true },
    { mode: "professional" },
    { project_id: "another-project" },
    {
      runtime: { available: false, runtime_digest: hash, runtime_identity: {} },
    },
    {
      runtime: {
        available: true,
        runtime_digest: "claimed",
        runtime_identity: {},
      },
    },
    {
      runtime: {
        available: true,
        runtime_digest: hash,
        runtime_identity: null,
      },
    },
  ])("rejects forged or inconsistent readiness %j", (change) => {
    expect(() =>
      decodeExecutionReadiness({ ...readiness, ...change }, project),
    ).toThrow("contract");
  });

  it("keeps successful native commands visibly unqualified and preserves failed guards", () => {
    expect(
      decodeExecutionReceipt(receipt, project, id, caseId, receiptId),
    ).toEqual(receipt);
    const html = renderToStaticMarkup(
      createElement(ExecutionReceiptView, { receipt }),
    );
    expect(html).toContain("Scientifically unqualified");
    expect(html).toContain("Numerical verification is incomplete or failed");
    expect(html).toContain("Failed or unexecuted");
    expect(html).toContain("profile drifted");
    expect(html).toContain("3.2 s");
    expect(html).not.toContain("accuracy achieved");
  });

  it("separates local numerical verification from physical validation and review", () => {
    const checked: ExecutionReceipt = {
      ...receipt,
      status: "complete_unqualified",
      numerical_verification_passed: true,
    };
    const html = renderToStaticMarkup(
      createElement(ExecutionReceiptView, { receipt: checked }),
    );
    expect(html).toContain("Scientifically unqualified");
    expect(html).toContain(
      "Independent physical validation and scoped scientific review are still required",
    );
    expect(
      executionStatusLabel({
        ...job,
        status: "complete_unqualified",
        numerical_verification_passed: true,
      }),
    ).toContain("scientifically unqualified");
  });

  it.each([
    { qualified: true },
    { project_id: "another" },
    { execution_id: "dexec_" + "e".repeat(32) },
    { native_execution_hash: "not-a-hash" },
    { status: "complete_unqualified", numerical_verification_passed: false },
    { status: "queued" },
    { diagnostics: null },
  ])("rejects unsupported receipt or scope %j", (change) => {
    expect(() =>
      decodeExecutionReceipt({ ...receipt, ...change }, project, id),
    ).toThrow("contract");
  });

  it("binds visible receipt to the selected immutable case and stored receipt ID", () => {
    expect(() =>
      decodeExecutionReceipt(
        receipt,
        project,
        id,
        "dcase_" + "e".repeat(32),
        receiptId,
      ),
    ).toThrow("contract");
    expect(() =>
      decodeExecutionReceipt(
        receipt,
        project,
        id,
        caseId,
        "dreceipt_" + "e".repeat(32),
      ),
    ).toThrow("contract");
  });

  it.each([
    { qualified: true },
    { numerical_verification_passed: "yes" },
    { status: "completed" },
    { status: "complete_unqualified", numerical_verification_passed: false },
    { attempt: true },
    { attempt: 4 },
    { attempt: 1.5 },
    { project_id: "another" },
    { content_hash: "asserted" },
    { id: "../../private" },
    { receipt_id: "https://untrusted.test/receipt" },
    { error: { code: 1 } },
  ])("rejects unsupported diagnostic job %j", (change) => {
    expect(() => decodeExecutionJob({ ...job, ...change }, project)).toThrow(
      "contract",
    );
  });

  it("polls only actual active states and preserves cancellation request wording", () => {
    expect(hasActiveExecution(undefined)).toBe(false);
    expect(hasActiveExecution([job])).toBe(true);
    expect(hasActiveExecution([{ ...job, status: "diagnostic_only" }])).toBe(
      false,
    );
    expect(
      executionStatusLabel({
        ...job,
        status: "running",
        cancel_requested: true,
      }),
    ).toBe("Cancellation requested");
    expect(executionStatusLabel({ ...job, status: "failed" })).toBe(
      "Execution failed · evidence retained",
    );
  });

  it("allows bounded infrastructure retry but not numerical repetition or exhausted allowance", () => {
    const failed: ExecutionJob = {
      ...job,
      status: "failed",
      error: { code: "WORKER_LOST", message: "Lease expired" },
    };
    expect(canRetryExecution(failed)).toBe(true);
    expect(
      canRetryExecution({
        ...failed,
        error: {
          code: "NUMERICAL_DIAGNOSTIC_FAILED",
          message: "Mass balance failed",
        },
      }),
    ).toBe(false);
    expect(canRetryExecution({ ...failed, attempt: 3 })).toBe(false);
    expect(canRetryExecution({ ...failed, cancel_requested: true })).toBe(
      false,
    );
    expect(
      canRetryExecution({ ...failed, resource_use: { worker_seconds: 100 } }),
    ).toBe(false);
    expect(canRetryExecution({ ...failed, resource_use: {} })).toBe(false);
    expect(canRetryExecution({ ...failed, frozen: {} })).toBe(false);
  });

  it("escapes diagnostic messages and treats unknown guard values as unverified", () => {
    const contaminated = {
      ...receipt,
      diagnostics: {
        numerical_gates: { scalar: "passed" },
        issues: [{ message: "<script>claimed</script>" }],
      },
    };
    const html = renderToStaticMarkup(
      createElement(ExecutionReceiptView, { receipt: contaminated }),
    );
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("Not a verified outcome");
  });

  it("requires a prepared case and server readiness before enabling execution", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const html = renderToStaticMarkup(
      createElement(
        QueryClientProvider,
        { client },
        createElement(DetailedExecutionPanel, {
          projectId: project,
          cases: [],
        }),
      ),
    );
    expect(html).toContain("Start detailed diagnostic");
    expect(html).toContain("Prepare a case with explicit physical inputs");
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled/);
    expect(html).toContain("No cash or cloud deployment is requested");
    const label = html.match(
      /<label[^>]*for="([^"]+)"[^>]*>Prepared case<\/label>/,
    );
    expect(label).not.toBeNull();
    expect(html).toContain(`<select id="${label![1]}"`);
  });
});
