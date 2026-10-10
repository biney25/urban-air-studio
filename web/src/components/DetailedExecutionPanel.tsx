import { useId, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FlaskConical, RefreshCw, Square } from "lucide-react";
import { api, downloadProtected } from "../api";
import { isObject, type DetailedCase } from "../evidence";
import { Badge, Empty, ErrorBox, Loading } from "./UI";

export type ExecutionStatus =
  | "queued"
  | "preparing"
  | "running"
  | "verifying"
  | "diagnostic_only"
  | "complete_unqualified"
  | "failed"
  | "cancelled";

export interface ExecutionJob {
  schema_version: "phase-b-execution-job-v1";
  id: string;
  project_id: string;
  case_id: string;
  title: string;
  status: ExecutionStatus;
  qualified: false;
  mode: "research_diagnostic";
  numerical_verification_passed: boolean;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  attempt: number;
  cancel_requested: boolean;
  frozen: Record<string, unknown>;
  resource_use: Record<string, unknown>;
  error: { code: string; message: string } | null;
  receipt_id: string | null;
  content_hash: string;
}

export interface ExecutionReadiness {
  schema_version: "phase-b-execution-readiness-v1";
  project_id: string;
  available: boolean;
  qualified: false;
  mode: "research_diagnostic";
  runtime: Record<string, unknown>;
  admission: Record<string, unknown>;
}

export interface ExecutionReceipt {
  schema_version: "phase-b-execution-receipt-v1";
  id: string;
  project_id: string;
  execution_id: string;
  case_id: string;
  status: ExecutionStatus;
  qualified: false;
  numerical_verification_passed: boolean;
  diagnostics: Record<string, unknown>;
  resources: Record<string, unknown>;
  native_execution_hash: string;
}

export interface NativeReceptorDiagnostic {
  id: string;
  cell: number;
  C_kg_m3: number;
  C_ug_m3: number;
  background_ug_m3: number;
  local_increment_ug_m3: number;
  U_m_s: [number, number, number];
  speed_m_s: number;
  protected: boolean;
}

export interface NativeFieldDiagnostics {
  schema_version: "phase-b-native-field-diagnostics-v1";
  C_unit: "kg/m3";
  reported_C_unit: "ug/m3";
  temporal_support: "stationary_solver_iteration_end_state_not_measured_interval_mean";
  ug_per_kg: 1_000_000_000;
  terminal_iteration: number;
  qualified: false;
  numerical_verification_passed: boolean;
  receptors: NativeReceptorDiagnostic[];
  mass_balance: Record<string, unknown>;
  stationarity: Record<string, unknown>[];
  unexecuted_required_gates: string[];
}

const statuses: ExecutionStatus[] = [
  "queued",
  "preparing",
  "running",
  "verifying",
  "diagnostic_only",
  "complete_unqualified",
  "failed",
  "cancelled",
];
const active = new Set<ExecutionStatus>([
  "queued",
  "preparing",
  "running",
  "verifying",
]);
const retryable = new Set([
  "RUNTIME_UNAVAILABLE",
  "RUNTIME_CHANGED",
  "WORKER_LOST",
  "ARTIFACT_STORAGE_FAILED",
]);
const hashPattern = /^sha256:[0-9a-f]{64}$/;
const jobIdPattern = /^dexec_[0-9a-f]{32}$/;
const caseIdPattern = /^dcase_[0-9a-f]{32}$/;
const readable = (value: string) => value.replaceAll("_", " ");
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const seconds = (value: unknown) =>
  finite(value) && value >= 0
    ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} s`
    : "Not measured";
const invalid = (): never => {
  throw new Error(
    "The server response does not match the diagnostic execution contract. No scientific result has been admitted.",
  );
};

export function decodeNativeFieldDiagnostics(
  value: unknown,
): NativeFieldDiagnostics {
  const matches = (left: number, right: number) =>
    Math.abs(left - right) <= Math.max(1e-12, Math.abs(right) * 1e-10);
  if (
    !isObject(value) ||
    value.schema_version !== "phase-b-native-field-diagnostics-v1" ||
    value.C_unit !== "kg/m3" ||
    value.reported_C_unit !== "ug/m3" ||
    value.ug_per_kg !== 1e9 ||
    value.temporal_support !==
      "stationary_solver_iteration_end_state_not_measured_interval_mean" ||
    value.qualified !== false ||
    typeof value.numerical_verification_passed !== "boolean" ||
    !finite(value.terminal_iteration) ||
    !Number.isInteger(value.terminal_iteration) ||
    value.terminal_iteration < 0 ||
    !Array.isArray(value.receptors) ||
    value.receptors.length > 1000 ||
    !isObject(value.mass_balance) ||
    !finite(value.mass_balance.relative_imbalance) ||
    value.mass_balance.relative_imbalance < 0 ||
    !Array.isArray(value.stationarity) ||
    !value.stationarity.every(isObject) ||
    !Array.isArray(value.unexecuted_required_gates) ||
    !value.unexecuted_required_gates.every((gate) => typeof gate === "string")
  )
    return invalid();
  const ids = new Set<string>();
  for (const row of value.receptors) {
    if (
      !isObject(row) ||
      typeof row.id !== "string" ||
      !row.id ||
      ids.has(row.id) ||
      !finite(row.cell) ||
      !Number.isInteger(row.cell) ||
      row.cell < 0 ||
      typeof row.protected !== "boolean" ||
      !finite(row.C_kg_m3) ||
      !finite(row.C_ug_m3) ||
      !finite(row.background_ug_m3) ||
      !finite(row.local_increment_ug_m3) ||
      !finite(row.speed_m_s) ||
      row.speed_m_s < 0 ||
      !Array.isArray(row.U_m_s) ||
      row.U_m_s.length !== 3 ||
      !row.U_m_s.every(finite) ||
      !matches(row.C_ug_m3, row.C_kg_m3 * 1e9) ||
      !matches(row.local_increment_ug_m3, row.C_ug_m3 - row.background_ug_m3) ||
      !matches(row.speed_m_s, Math.hypot(...row.U_m_s))
    )
      return invalid();
    ids.add(row.id);
  }
  return value as unknown as NativeFieldDiagnostics;
}

const diagnosticNumber = (value: unknown) =>
  finite(value)
    ? value.toLocaleString(undefined, { maximumSignificantDigits: 6 })
    : "Not supplied";

export function NativeFieldDiagnosticsView({ value }: { value: unknown }) {
  let fields: NativeFieldDiagnostics;
  try {
    fields = decodeNativeFieldDiagnostics(value);
  } catch (error) {
    return <ErrorBox error={error} />;
  }
  const mass = fields.mass_balance.relative_imbalance;
  return (
    <section aria-label="Unqualified native field diagnostics">
      <h4>Native passive-tracer diagnostics</h4>
      <p>
        Simulated native fluid-cell values at solver iteration{" "}
        {fields.terminal_iteration.toLocaleString()}. These are iteration end
        states under stationary inputs, not measured interval means, PM, AQI,
        exposure or health outcomes. Source and geometry assumptions retain the
        frozen case’s evidence class.
      </p>
      <div
        className="table-wrap"
        role="region"
        aria-label="Native passive-tracer data table"
        tabIndex={0}
      >
        <table>
          <caption>
            Unqualified passive tracer. Total, prescribed background and local
            increment in µg/m³; velocity magnitude in m/s. Protected receptors
            retain their own results.
          </caption>
          <thead>
            <tr>
              <th scope="col">Receptor / native cell</th>
              <th scope="col">Total µg/m³</th>
              <th scope="col">Background µg/m³</th>
              <th scope="col">Local increment µg/m³</th>
              <th scope="col">Speed m/s</th>
            </tr>
          </thead>
          <tbody>
            {fields.receptors.map((row) => (
              <tr key={row.id}>
                <th scope="row">
                  {row.id}
                  <small>
                    Cell {row.cell}
                    {row.protected ? " · protected" : ""}
                  </small>
                </th>
                <td>{diagnosticNumber(row.C_ug_m3)}</td>
                <td>{diagnosticNumber(row.background_ug_m3)}</td>
                <td>{diagnosticNumber(row.local_increment_ug_m3)}</td>
                <td>{diagnosticNumber(row.speed_m_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        Recorded relative scalar mass imbalance:{" "}
        <strong>
          {finite(mass) ? `${diagnosticNumber(mass * 100)}%` : "Not supplied"}
        </strong>
        . This guard does not establish benchmark accuracy or a complete
        uncertainty budget.
      </p>
      {fields.unexecuted_required_gates.length ? (
        <details>
          <summary>Outstanding scientific gates</summary>
          <ul className="evidence-limits">
            {fields.unexecuted_required_gates.map((gate) => (
              <li key={gate}>{readable(gate)}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

export function decodeExecutionReadiness(
  value: unknown,
  projectId: string,
): ExecutionReadiness {
  if (
    !isObject(value) ||
    value.schema_version !== "phase-b-execution-readiness-v1" ||
    value.project_id !== projectId ||
    typeof value.available !== "boolean" ||
    value.qualified !== false ||
    value.mode !== "research_diagnostic" ||
    !isObject(value.runtime) ||
    !isObject(value.admission)
  )
    return invalid();
  if (
    value.available &&
    (value.runtime.available !== true ||
      !hashPattern.test(String(value.runtime.runtime_digest)) ||
      !isObject(value.runtime.runtime_identity))
  )
    return invalid();
  return value as unknown as ExecutionReadiness;
}

export function decodeExecutionJob(
  value: unknown,
  projectId: string,
): ExecutionJob {
  if (
    !isObject(value) ||
    value.schema_version !== "phase-b-execution-job-v1" ||
    value.project_id !== projectId ||
    !jobIdPattern.test(String(value.id)) ||
    !caseIdPattern.test(String(value.case_id)) ||
    typeof value.title !== "string" ||
    !statuses.includes(value.status as ExecutionStatus) ||
    value.qualified !== false ||
    value.mode !== "research_diagnostic" ||
    typeof value.numerical_verification_passed !== "boolean" ||
    typeof value.created_at !== "string" ||
    !Number.isFinite(Date.parse(value.created_at)) ||
    !finite(value.attempt) ||
    !Number.isInteger(value.attempt) ||
    value.attempt < 1 ||
    value.attempt > 3 ||
    typeof value.cancel_requested !== "boolean" ||
    !isObject(value.frozen) ||
    !isObject(value.resource_use) ||
    !hashPattern.test(String(value.content_hash)) ||
    !(
      value.error === null ||
      (isObject(value.error) &&
        typeof value.error.code === "string" &&
        typeof value.error.message === "string")
    ) ||
    !(
      value.receipt_id === null ||
      (typeof value.receipt_id === "string" &&
        /^dreceipt_[0-9a-f]{32}$/.test(value.receipt_id))
    )
  )
    return invalid();
  if (
    value.status === "complete_unqualified" &&
    value.numerical_verification_passed !== true
  )
    return invalid();
  return value as unknown as ExecutionJob;
}

export function decodeExecutionReceipt(
  value: unknown,
  projectId: string,
  executionId: string,
  expectedCaseId?: string,
  expectedReceiptId?: string,
): ExecutionReceipt {
  if (
    !isObject(value) ||
    value.schema_version !== "phase-b-execution-receipt-v1" ||
    value.project_id !== projectId ||
    value.execution_id !== executionId ||
    value.qualified !== false ||
    !/^dreceipt_[0-9a-f]{32}$/.test(String(value.id)) ||
    !caseIdPattern.test(String(value.case_id)) ||
    ![
      "diagnostic_only",
      "complete_unqualified",
      "failed",
      "cancelled",
    ].includes(String(value.status)) ||
    typeof value.numerical_verification_passed !== "boolean" ||
    !isObject(value.diagnostics) ||
    !isObject(value.resources) ||
    !hashPattern.test(String(value.native_execution_hash))
  )
    return invalid();
  if (
    (expectedCaseId && value.case_id !== expectedCaseId) ||
    (expectedReceiptId && value.id !== expectedReceiptId) ||
    (value.status === "complete_unqualified" &&
      value.numerical_verification_passed !== true)
  )
    return invalid();
  return value as unknown as ExecutionReceipt;
}

export function hasActiveExecution(jobs: ExecutionJob[] | undefined): boolean {
  return jobs?.some((job) => active.has(job.status)) ?? false;
}

export function canRetryExecution(job: ExecutionJob): boolean {
  const used = job.resource_use.worker_seconds;
  const maximum = job.frozen.maximum_total_seconds;
  return (
    job.status === "failed" &&
    !!job.error &&
    retryable.has(job.error.code) &&
    job.attempt < 3 &&
    !job.cancel_requested &&
    finite(used) &&
    finite(maximum) &&
    used >= 0 &&
    used < maximum
  );
}

export function executionStatusLabel(
  job: Pick<
    ExecutionJob,
    "status" | "cancel_requested" | "numerical_verification_passed"
  >,
): string {
  if (job.cancel_requested && active.has(job.status))
    return "Cancellation requested";
  const labels: Record<ExecutionStatus, string> = {
    queued: "Waiting for diagnostic worker",
    preparing: "Checking frozen inputs",
    running: "Running native diagnostics",
    verifying: "Checking and saving receipts",
    diagnostic_only: "Executed · verification incomplete",
    complete_unqualified:
      "Numerical checks passed · scientifically unqualified",
    failed: "Execution failed · evidence retained",
    cancelled: "Cancelled",
  };
  return labels[job.status];
}

export function ExecutionRuntimeCard({
  readiness,
}: {
  readiness?: ExecutionReadiness;
}) {
  const available = readiness?.available === true;
  const reason =
    readiness && typeof readiness.runtime.reason === "string"
      ? readiness.runtime.reason
      : undefined;
  return (
    <section className="evidence-card" aria-label="Detailed solver runtime">
      <Badge tone={available ? "cyan" : "amber"}>
        {available
          ? "Pinned runtime available"
          : "Detailed runtime unavailable"}
      </Badge>
      <h2>Research diagnostics</h2>
      <p>
        Run the frozen geometry, mesh and neutral flow configuration through the
        pinned native solver. A successful command or converged flow does not
        establish air-quality accuracy.
      </p>
      {reason ? (
        <p>{reason}</p>
      ) : !readiness ? (
        <p>Runtime status has not been supplied by the server.</p>
      ) : null}
      {available ? (
        <details>
          <summary>Verified runtime identity</summary>
          <p>
            <code>{String(readiness.runtime.runtime_digest)}</code>
          </p>
        </details>
      ) : null}
      <p>
        Independent wind and tracer benchmarks, mesh and domain sensitivity,
        measured replay, uncertainty and independent scientific review remain
        separate release gates.
      </p>
    </section>
  );
}

export function ExecutionReceiptView({
  receipt,
}: {
  receipt: ExecutionReceipt;
}) {
  const gates = isObject(receipt.diagnostics.numerical_gates)
    ? Object.entries(receipt.diagnostics.numerical_gates)
    : [];
  const issues = Array.isArray(receipt.diagnostics.issues)
    ? receipt.diagnostics.issues.filter(isObject)
    : [];
  return (
    <section className="evidence-card" aria-label="Native execution receipt">
      <Badge tone="amber">Scientifically unqualified</Badge>
      <h3>Retained diagnostic receipt</h3>
      <p>
        {receipt.numerical_verification_passed
          ? "The worker reports its required numerical checks passed. Independent physical validation and scoped scientific review are still required."
          : "Numerical verification is incomplete or failed. Solver execution alone cannot promote this case to a qualified result."}
      </p>
      {gates.length ? (
        <div
          className="table-wrap"
          role="region"
          aria-label="Recorded worker numerical guards"
          tabIndex={0}
        >
          <table>
            <caption>
              Worker numerical guards; these do not constitute independent
              scientific validation.
            </caption>
            <thead>
              <tr>
                <th scope="col">Guard</th>
                <th scope="col">Recorded outcome</th>
              </tr>
            </thead>
            <tbody>
              {gates.map(([name, passed]) => (
                <tr key={name}>
                  <td>{readable(name)}</td>
                  <td>
                    {passed === true
                      ? "Passed"
                      : passed === false
                        ? "Failed or unexecuted"
                        : "Not a verified outcome"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>The receipt does not supply a complete numerical gate record.</p>
      )}
      {issues.length ? (
        <ul className="evidence-limits">
          {issues.slice(0, 20).map((issue, index) => (
            <li key={index}>
              {typeof issue.message === "string"
                ? issue.message
                : typeof issue.code === "string"
                  ? readable(issue.code)
                  : "Unspecified diagnostic issue"}
            </li>
          ))}
        </ul>
      ) : null}
      {issues.length > 20 ? (
        <p>
          {issues.length - 20} additional issues remain in the full receipt
          bundle.
        </p>
      ) : null}
      {receipt.diagnostics.field_analysis ? (
        <NativeFieldDiagnosticsView
          value={receipt.diagnostics.field_analysis}
        />
      ) : null}
      <p>
        Measured native wall time:{" "}
        <strong>{seconds(receipt.resources.wall_seconds)}</strong>. Timing is
        local evidence, not a calibrated cloud quote.
      </p>
      <details>
        <summary>Immutable native output identity</summary>
        <p>
          <code>{receipt.native_execution_hash}</code>
        </p>
      </details>
    </section>
  );
}

export function preparedCaseCompilerState(
  record: DetailedCase,
  currentBuild: unknown,
): "current" | "historical" | "unverified" {
  const method = record.manifest.method;
  const hashPattern = /^sha256:[0-9a-f]{64}$/;
  if (
    typeof currentBuild !== "string" ||
    !hashPattern.test(currentBuild) ||
    !isObject(method) ||
    typeof method.compiler_build_hash !== "string" ||
    !hashPattern.test(method.compiler_build_hash)
  )
    return "unverified";
  return method.compiler_build_hash === currentBuild ? "current" : "historical";
}

export default function DetailedExecutionPanel({
  projectId,
  cases,
  currentCompilerBuildHash,
  onPrepareCase,
}: {
  projectId: string;
  cases: DetailedCase[];
  currentCompilerBuildHash?: unknown;
  onPrepareCase?: () => void;
}) {
  const client = useQueryClient();
  const caseSelectId = useId();
  const base = `/projects/${encodeURIComponent(projectId)}/detailed-executions`;
  const [caseId, setCaseId] = useState("");
  const [executionId, setExecutionId] = useState("");
  const [notice, setNotice] = useState("");
  const [downloadError, setDownloadError] = useState<unknown>(null);
  const [downloading, setDownloading] = useState(false);
  const pendingKeys = useRef(new Map<string, string>());
  const selectedCase = cases.find((record) => record.id === caseId);
  const readiness = useQuery({
    queryKey: ["detailed-runtime", projectId],
    queryFn: async ({ signal }) =>
      decodeExecutionReadiness(
        await api<unknown>(`${base}/readiness`, undefined, signal),
        projectId,
      ),
    retry: false,
  });
  const jobs = useQuery({
    queryKey: ["detailed-executions", projectId],
    queryFn: async ({ signal }) => {
      const response = await api<unknown>(base, undefined, signal);
      if (!Array.isArray(response) || response.length > 100) return invalid();
      return response.map((record) => decodeExecutionJob(record, projectId));
    },
    refetchInterval: (query) =>
      hasActiveExecution(query.state.data) ? 2000 : false,
  });
  const selected = jobs.data?.find((job) => job.id === executionId);
  const receipt = useQuery({
    queryKey: [
      "detailed-receipt",
      projectId,
      executionId,
      selected?.receipt_id,
    ],
    enabled: !!selected?.receipt_id,
    queryFn: async ({ signal }) =>
      decodeExecutionReceipt(
        await api<unknown>(`${base}/${executionId}/receipt`, undefined, signal),
        projectId,
        executionId,
        selected?.case_id,
        selected?.receipt_id ?? undefined,
      ),
    retry: false,
  });
  const updateJob = async (job: ExecutionJob) => {
    setExecutionId(job.id);
    client.setQueryData<ExecutionJob[]>(
      ["detailed-executions", projectId],
      (previous) => [
        job,
        ...(previous ?? []).filter((old) => old.id !== job.id),
      ],
    );
    await client.invalidateQueries({
      queryKey: ["detailed-executions", projectId],
    });
  };
  const start = useMutation({
    mutationFn: async (record: DetailedCase) => {
      const key = `${projectId}:${record.id}:${record.content_hash}`;
      let requestKey = pendingKeys.current.get(key);
      if (!requestKey) {
        requestKey = crypto.randomUUID().replaceAll("-", "");
        pendingKeys.current.set(key, requestKey);
      }
      const job = decodeExecutionJob(
        await api<unknown>(base, {
          case_id: record.id,
          expected_case_hash: record.content_hash,
          idempotency_key: requestKey,
          title: `Diagnostic · ${record.title}`.slice(0, 120),
          mode: "research_diagnostic",
        }),
        projectId,
      );
      pendingKeys.current.delete(key);
      return job;
    },
    onSuccess: async (job) => {
      await updateJob(job);
      setNotice(
        "Frozen case queued. The separate diagnostic worker will record actual execution and retain failures.",
      );
    },
  });
  const cancel = useMutation({
    mutationFn: async (job: ExecutionJob) =>
      decodeExecutionJob(
        await api<unknown>(`${base}/${job.id}/cancel`, {}),
        projectId,
      ),
    onSuccess: async (job) => {
      await updateJob(job);
      setNotice(
        job.status === "cancelled"
          ? "Execution cancelled."
          : "Cancellation requested. The worker will stop at a controlled boundary.",
      );
    },
  });
  const retry = useMutation({
    mutationFn: async (job: ExecutionJob) =>
      decodeExecutionJob(
        await api<unknown>(`${base}/${job.id}/retry`, {}),
        projectId,
      ),
    onSuccess: async (job) => {
      await updateJob(job);
      setNotice(
        "Infrastructure retry queued with the same frozen case and original time allowance.",
      );
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (
      selectedCase &&
      preparedCaseCompilerState(selectedCase, currentCompilerBuildHash) ===
        "current" &&
      readiness.data?.available
    ) {
      setNotice("");
      start.mutate(selectedCase);
    }
  };
  const download = async (job: ExecutionJob) => {
    setDownloadError(null);
    setDownloading(true);
    try {
      await downloadProtected(
        `${base}/${job.id}/download`,
        `${job.id}-diagnostic-receipt.zip`,
      );
      setNotice(
        "Retained diagnostic receipt downloaded. Scientific qualification remains separate.",
      );
    } catch (error) {
      setDownloadError(error);
    } finally {
      setDownloading(false);
    }
  };
  const busy = start.isPending || cancel.isPending || retry.isPending;
  const casePermitted =
    !!selectedCase && selectedCase.rights.derive && selectedCase.rights.solve;
  const caseCurrent =
    !!selectedCase &&
    preparedCaseCompilerState(selectedCase, currentCompilerBuildHash) ===
      "current";
  return (
    <div className="evidence-form">
      <div className="evidence-section-title">
        <div>
          <h2>Detailed execution</h2>
          <p>
            Run a prepared, immutable case and inspect its actual diagnostics.
            Research outputs remain separate from verified environmental
            predictions.
          </p>
        </div>
      </div>
      {readiness.isPending ? (
        <Loading label="Checking pinned detailed runtime…" />
      ) : readiness.error ? (
        <ErrorBox
          error={readiness.error}
          onRetry={() => void readiness.refetch()}
        />
      ) : (
        <ExecutionRuntimeCard readiness={readiness.data} />
      )}
      <form
        className="evidence-fieldset evidence-form"
        onSubmit={submit}
        aria-label="Start detailed diagnostic"
      >
        <label className="form-label" htmlFor={caseSelectId}>
          Prepared case
        </label>
        <select
          id={caseSelectId}
          value={caseId}
          required
          onChange={(event) => setCaseId(event.target.value)}
          disabled={busy || !cases.length}
        >
          <option value="">Choose a frozen case</option>
          {cases.map((record) => (
            <option
              value={record.id}
              key={record.id}
              disabled={
                preparedCaseCompilerState(record, currentCompilerBuildHash) !==
                "current"
              }
            >
              {record.title} · scenario version {record.scenario_version}
              {preparedCaseCompilerState(record, currentCompilerBuildHash) ===
              "current"
                ? ""
                : " · historical or unverified compiler; prepare a new case"}
            </option>
          ))}
        </select>
        {!cases.length ? (
          <p>
            Prepare a case with explicit physical inputs before running
            diagnostics.
            {onPrepareCase ? (
              <>
                {" "}
                <button
                  type="button"
                  className="text-button"
                  onClick={onPrepareCase}
                >
                  Open case preparation
                </button>
              </>
            ) : null}
          </p>
        ) : null}
        {selectedCase ? (
          <div>
            <p>
              Frozen source: <code>{selectedCase.scenario_hash}</code>
            </p>
            <p>
              Case identity: <code>{selectedCase.content_hash}</code>
            </p>
            {!casePermitted ? (
              <p>
                Declared source permissions do not permit deriving and solving
                this case.
              </p>
            ) : null}
            {!caseCurrent ? (
              <p>
                The frozen compiler build is historical or unverified. Prepare a
                new case with the active compiler to run. Historical artifacts
                and attempts remain available.
              </p>
            ) : null}
          </div>
        ) : null}
        <p>
          Uses the case’s bounded worker allowance. Changing geometry, physics
          or solver settings requires preparing a new case. No cash or cloud
          deployment is requested.
        </p>
        <button
          type="submit"
          className="button primary"
          disabled={
            busy ||
            !casePermitted ||
            !caseCurrent ||
            readiness.data?.available !== true
          }
        >
          <FlaskConical size={16} />
          {start.isPending ? "Queuing diagnostic…" : "Run diagnostic"}
        </button>
      </form>
      {start.error ? <ErrorBox error={start.error} /> : null}
      {cancel.error ? <ErrorBox error={cancel.error} /> : null}
      {retry.error ? <ErrorBox error={retry.error} /> : null}
      {notice ? (
        <p className="evidence-notice" role="status">
          {notice}
        </p>
      ) : null}
      {jobs.isPending ? (
        <Loading label="Loading diagnostic attempts…" />
      ) : jobs.error ? (
        <ErrorBox error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : !jobs.data?.length ? (
        <Empty title="No detailed attempts yet">
          Queued, failed and completed diagnostics will appear here with their
          frozen inputs and retained evidence.
        </Empty>
      ) : (
        <div className="saved-evidence-grid">
          <nav
            className="saved-record-list"
            aria-label="Detailed diagnostic attempts"
          >
            {jobs.data.map((job) => (
              <button
                key={job.id}
                data-testid={`execution-${job.id}`}
                type="button"
                className={`saved-record ${executionId === job.id ? "active" : ""}`}
                aria-pressed={executionId === job.id}
                onClick={() => {
                  setExecutionId(job.id);
                  setDownloadError(null);
                }}
              >
                <FlaskConical size={16} />
                <span>
                  <strong>{job.title}</strong>
                  <small>{executionStatusLabel(job)}</small>
                  <small>
                    Attempt {job.attempt} ·{" "}
                    {seconds(job.resource_use.worker_seconds)}
                  </small>
                </span>
              </button>
            ))}
          </nav>
          {selected ? (
            <article
              className="evidence-record-detail"
              aria-label="Selected diagnostic attempt"
            >
              <Badge tone={selected.status === "failed" ? "red" : "amber"}>
                Scientifically unqualified
              </Badge>
              <h3>{selected.title}</h3>
              <p role="status">{executionStatusLabel(selected)}</p>
              <p>
                Worker allowance consumed:{" "}
                {seconds(selected.resource_use.worker_seconds)}. A lost worker
                may consume its reserved time; the native receipt records
                measured execution. No currency estimate is supplied.
              </p>
              <p>
                Frozen case: <code>{selected.case_id}</code>
              </p>
              {selected.error ? (
                <div className="error-box" role="alert">
                  <div>
                    <strong>{readable(selected.error.code)}</strong>
                    <p>{selected.error.message}</p>
                  </div>
                </div>
              ) : null}
              <div className="evidence-actions">
                {active.has(selected.status) ? (
                  <button
                    type="button"
                    className="button"
                    disabled={busy || selected.cancel_requested}
                    onClick={() => cancel.mutate(selected)}
                  >
                    <Square size={14} />
                    {selected.cancel_requested
                      ? "Cancellation requested"
                      : "Cancel execution"}
                  </button>
                ) : null}
                {canRetryExecution(selected) ? (
                  <button
                    type="button"
                    className="button"
                    disabled={busy || readiness.data?.available !== true}
                    onClick={() => retry.mutate(selected)}
                  >
                    <RefreshCw size={14} />
                    Retry infrastructure failure
                  </button>
                ) : null}
                {selected.receipt_id ? (
                  <button
                    type="button"
                    className="button"
                    disabled={downloading}
                    onClick={() => void download(selected)}
                  >
                    <Download size={14} />
                    {downloading
                      ? "Downloading receipt…"
                      : "Download diagnostic bundle"}
                  </button>
                ) : null}
              </div>
              {selected.status === "failed" && !canRetryExecution(selected) ? (
                <p>
                  Numerical failures need diagnosis and a corrected new case;
                  repeating identical inputs cannot establish qualification.
                </p>
              ) : null}
              {downloadError ? <ErrorBox error={downloadError} /> : null}
              {receipt.isFetching && !receipt.data ? (
                <Loading label="Loading retained native receipt…" />
              ) : receipt.error ? (
                <ErrorBox
                  error={receipt.error}
                  onRetry={() => void receipt.refetch()}
                />
              ) : receipt.data ? (
                <ExecutionReceiptView receipt={receipt.data} />
              ) : null}
            </article>
          ) : (
            <Empty title="Select an attempt">
              Inspect its status, frozen input identity and retained
              diagnostics.
            </Empty>
          )}
        </div>
      )}
    </div>
  );
}
