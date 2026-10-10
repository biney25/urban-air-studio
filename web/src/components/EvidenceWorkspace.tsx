import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileCheck2, FlaskConical, Plus, Trash2 } from "lucide-react";
import { api, downloadProtected } from "../api";
import {
  advancedCaseFields,
  caseConfig,
  caseFields,
  decodeScientificRequirements,
  hasVerifiedNativeRuntime,
  isObject,
  newFleet,
  newMetadata,
  newSegment,
  parseEvidenceRequest,
  type DetailedCase,
  type AuthorizationMethod,
  type EvidenceKind,
  type EvidenceMetadata,
  type EvidenceRecord,
  type EvidenceRequest,
  type EvidenceSummary,
  type Readiness,
  type ScientificGate,
  type RoadSegment,
} from "../evidence";
import type { Scenario } from "../types";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  NumericInput,
  SectionHeading,
} from "./UI";
import DetailedExecutionPanel from "./DetailedExecutionPanel";
import TrafficAllocationPanel from "./TrafficAllocationPanel";

type Pane =
  | "overview"
  | "inventory"
  | "measurement_campaign"
  | "replay_protocol"
  | "case"
  | "execution"
  | "saved";
const panes: [Pane, string][] = [
  ["overview", "Method & gates"],
  ["inventory", "Traffic inputs"],
  ["measurement_campaign", "Measurement QC"],
  ["replay_protocol", "Held-out protocol"],
  ["case", "Case preparation"],
  ["execution", "Detailed execution"],
  ["saved", "Saved records"],
];
const words = (value: unknown) =>
  typeof value === "string" ? value.replaceAll("_", " ") : "Not declared";
const number = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumSignificantDigits: 6 })
    : "—";
const strings = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((x): x is string => typeof x === "string")
    : [];
const objects = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value) ? value.filter(isObject) : [];

export default function EvidenceWorkspace({
  projectId,
  scenario,
  scenarios,
  onScenario,
}: {
  projectId: string;
  scenario?: Scenario;
  scenarios: Scenario[];
  onScenario: (id: string) => void;
}) {
  const client = useQueryClient();
  const [pane, setPane] = useState<Pane>("overview");
  const [selectedId, setSelectedId] = useState("");
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [notice, setNotice] = useState("");
  const [downloadError, setDownloadError] = useState<unknown>(null);
  const [reviewDownloading, setReviewDownloading] = useState(false);
  const readiness = useQuery({
    queryKey: ["qualification", projectId],
    queryFn: ({ signal }) =>
      api<Readiness>(`/projects/${projectId}/qualification`, undefined, signal),
  });
  const record = useQuery({
    queryKey: ["evidence", projectId, selectedId],
    queryFn: ({ signal }) =>
      api<EvidenceRecord>(
        `/projects/${projectId}/evidence/${selectedId}`,
        undefined,
        signal,
      ),
    enabled: !!selectedId,
  });
  const save = useMutation({
    mutationFn: (request: EvidenceRequest) =>
      api<EvidenceRecord>(`/projects/${projectId}/evidence`, request),
    onSuccess: async (record) => {
      client.setQueryData(["evidence", projectId, record.id], record);
      await client.invalidateQueries({
        queryKey: ["qualification", projectId],
      });
      setSelectedId(record.id);
      setSelectedCaseId("");
      setPane("saved");
      setNotice(
        `Saved ${record.title}. The input and derived summary are frozen; method qualification is unchanged.`,
      );
    },
  });
  const prepare = useMutation({
    mutationFn: (body: {
      title: string;
      scenario_id: string;
      expected_version: number;
      config: Record<string, unknown>;
    }) => api<DetailedCase>(`/projects/${projectId}/detailed-cases`, body),
    onSuccess: async (record) => {
      await client.invalidateQueries({
        queryKey: ["qualification", projectId],
      });
      setSelectedCaseId(record.id);
      setSelectedId("");
      setPane("saved");
      setNotice(
        "Case files prepared and frozen. No detailed solver was executed.",
      );
    },
  });
  async function download(kind: "evidence" | "detailed-cases", id: string) {
    setDownloadError(null);
    try {
      await downloadProtected(
        `/projects/${projectId}/${kind}/${id}/download`,
        `${id}.${kind === "evidence" ? "json" : "zip"}`,
      );
    } catch (error) {
      setDownloadError(error);
    }
  }
  async function downloadReviewPacket() {
    setDownloadError(null);
    setReviewDownloading(true);
    try {
      await downloadProtected(
        `/projects/${projectId}/qualification/review-packet`,
        `${projectId}-scientific-review.json`,
      );
      setNotice(
        "Project review manifest downloaded. It records evidence and remaining gates; independent scientific approval is pending.",
      );
    } catch (error) {
      setDownloadError(error);
    } finally {
      setReviewDownloading(false);
    }
  }
  if (readiness.isPending)
    return <Loading label="Loading project methods and evidence…" />;
  if (readiness.error)
    return (
      <div className="content-page">
        <ErrorBox
          error={readiness.error}
          onRetry={() => {
            void readiness.refetch();
          }}
        />
      </div>
    );
  const data = readiness.data;
  const method = data.detailed_method;
  const selectedCase = data.cases.find((c) => c.id === selectedCaseId);
  return (
    <div className="content-page evidence-workspace">
      <SectionHeading
        eyebrow="Evidence / detailed method"
        title="Build the evidence behind a decision"
        description="Keep inputs, measurements and model preparation traceable before a detailed result can be admitted."
      />
      <MethodStatusBanner data={data} />
      <nav className="evidence-nav" aria-label="Evidence tasks">
        {panes.map(([id, label]) => (
          <button
            key={id}
            aria-current={pane === id ? "page" : undefined}
            className={pane === id ? "active" : ""}
            onClick={() => {
              setPane(id);
              setNotice("");
              if (id === "overview") void readiness.refetch();
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {notice ? (
        <p className="evidence-notice" role="status">
          {notice}
        </p>
      ) : null}
      {downloadError ? <ErrorBox error={downloadError} /> : null}
      {pane === "overview" ? (
        <>
          <div className="evidence-overview">
            <section className="evidence-card">
              <span className="eyebrow">Detailed method</span>
              <h2>Prepared is not qualified</h2>
              <p>
                Geometry and numerical controls can be frozen now. Runtime
                execution, independent benchmarks, mesh sensitivity, held-out
                observations and independent review must supply their own
                evidence.
              </p>
              <ul>
                {strings(method.limitations).map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
              <RawDetails data={method} title="Detailed method identity" />
            </section>
            <section className="evidence-card">
              <span className="eyebrow">Existing research preview</span>
              <h2>Retain the failed gates</h2>
              <p>
                The potential-flow preview remains unqualified. Its default mesh
                is not convergence-qualified; small design differences may be
                numerical or native-cell sampling effects. Saved evidence does
                not promote it.
              </p>
              <RawDetails
                data={data.research_preview}
                title="Research preview diagnostics"
              />
              <ul>
                {objects(data.research_preview.failed_gates).map((g, i) => (
                  <li key={i}>
                    <strong>Failed:</strong> {words(g.id)}
                    {strings(g.reasons).length
                      ? ` · ${strings(g.reasons).join("; ")}`
                      : ""}
                  </li>
                ))}
              </ul>
              <RawDetails
                data={data.local_resource_observations}
                title="Measured local resource observations"
              />
              <p className="micro">
                Local preview timings do not estimate detailed solver runtime,
                city performance or cloud costs.
              </p>
            </section>
            <SourcePermissionCard method={data.authorization_method} />
            <ScientificReviewCard
              data={data}
              pending={reviewDownloading}
              onDownload={() => void downloadReviewPacket()}
            />
          </div>
          <section aria-labelledby="phase-b-gates">
            <div className="evidence-section-title">
              <h2 id="phase-b-gates">Phase B acceptance gates</h2>
              <span>{data.packages.length} tracked packages</span>
            </div>
            <div className="gate-grid">
              {data.packages.map((p) => (
                <details className="gate-card" key={p.id}>
                  <summary>
                    <span className="gate-id">{p.id}</span>
                    <strong>{p.title}</strong>
                    <Badge tone="amber">{words(p.state)}</Badge>
                  </summary>
                  <p>Remaining evidence</p>
                  <ul>
                    {p.remaining.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </details>
              ))}
            </div>
          </section>
          <ScientificGateCatalogue value={data.scientific_requirements} />
        </>
      ) : null}
      {pane === "inventory" ? (
        <>
          <InventoryForm
            onSave={(r) => save.mutate(r)}
            pending={save.isPending}
            error={save.error}
          />
          <TrafficAllocationPanel
            projectId={projectId}
            records={data.evidence}
            scenario={scenario}
            scenarios={scenarios}
            onScenario={onScenario}
          />
        </>
      ) : null}
      {pane === "measurement_campaign" ? (
        <EvidenceUpload
          kind="measurement_campaign"
          records={data.evidence}
          onSave={(r) => save.mutate(r)}
          pending={save.isPending}
          error={save.error}
        />
      ) : null}
      {pane === "replay_protocol" ? (
        <ReplayWorkspace
          records={data.evidence}
          onSave={(r) => save.mutate(r)}
          pending={save.isPending}
          error={save.error}
        />
      ) : null}
      {pane === "case" ? (
        <CaseForm
          scenario={scenario}
          scenarios={scenarios}
          onScenario={onScenario}
          onPrepare={(b) => prepare.mutate(b)}
          pending={prepare.isPending}
          error={prepare.error}
        />
      ) : null}
      {pane === "execution" ? (
        <DetailedExecutionPanel
          projectId={projectId}
          cases={data.cases}
          currentCompilerBuildHash={method.compiler_build_hash}
          onPrepareCase={() => setPane("case")}
        />
      ) : null}
      {pane === "saved" ? (
        <>
          <div className="evidence-section-title">
            <h2>Immutable project records</h2>
            <span>
              {data.evidence.length} evidence records · {data.cases.length}{" "}
              prepared cases
            </span>
          </div>
          {!data.evidence.length && !data.cases.length ? (
            <Empty title="No evidence saved yet">
              Add a traffic inventory, a measurement campaign or a held-out
              protocol. A prepared case also appears here with its frozen
              scenario.
            </Empty>
          ) : (
            <div className="saved-evidence-grid">
              <section
                className="saved-record-list"
                aria-label="Saved artifacts"
              >
                {data.evidence.map((e) => (
                  <button
                    className={`saved-record ${e.id === selectedId ? "active" : ""}`}
                    key={e.id}
                    onClick={() => {
                      setSelectedId(e.id);
                      setSelectedCaseId("");
                    }}
                    aria-pressed={e.id === selectedId}
                  >
                    <FileCheck2 size={19} aria-hidden="true" />
                    <span>
                      <strong>{e.title}</strong>
                      <small>
                        {words(e.kind)} · {e.effective_evidence_kind}
                      </small>
                      <code>{e.id}</code>
                    </span>
                  </button>
                ))}
                {data.cases.map((c) => (
                  <button
                    className={`saved-record ${c.id === selectedCaseId ? "active" : ""}`}
                    key={c.id}
                    onClick={() => {
                      setSelectedCaseId(c.id);
                      setSelectedId("");
                    }}
                    aria-pressed={c.id === selectedCaseId}
                  >
                    <FlaskConical size={19} aria-hidden="true" />
                    <span>
                      <strong>{c.title}</strong>
                      <small>Frozen input bundle · unqualified</small>
                      <code>{c.id}</code>
                    </span>
                  </button>
                ))}
              </section>
              <section
                className="evidence-record-detail"
                aria-label="Selected saved artifact"
              >
                {selectedId ? (
                  record.isPending ? (
                    <Loading label="Checking saved evidence…" />
                  ) : record.error ? (
                    <ErrorBox error={record.error} />
                  ) : record.data ? (
                    <RecordDetail
                      record={record.data}
                      onDownload={() => {
                        void download("evidence", record.data.id);
                      }}
                    />
                  ) : null
                ) : selectedCase ? (
                  <>
                    <h2>{selectedCase.title}</h2>
                    <Badge tone="amber">
                      Frozen input bundle · unqualified
                    </Badge>
                    <p>
                      This record freezes compiler inputs and case files. Native
                      execution attempts and their numerical diagnostics are
                      tracked separately in Detailed execution.
                    </p>
                    <button
                      className="button small"
                      type="button"
                      onClick={() => setPane("execution")}
                    >
                      View native attempts
                    </button>
                    <p>
                      Frozen scenario <code>{selectedCase.scenario_id}</code>
                    </p>
                    <p>
                      Scenario hash <code>{selectedCase.scenario_hash}</code>
                    </p>
                    <p>
                      Artifact hash <code>{selectedCase.content_hash}</code>
                    </p>
                    <RawDetails
                      title="Frozen case manifest"
                      data={selectedCase.manifest}
                    />
                    <button
                      className="button primary"
                      disabled={!selectedCase.rights.export}
                      onClick={() => {
                        void download("detailed-cases", selectedCase.id);
                      }}
                    >
                      <Download size={15} /> Download case ZIP
                    </button>
                    {!selectedCase.rights.export ? (
                      <p className="micro">
                        The frozen scenario's source permissions restrict case
                        export.
                      </p>
                    ) : null}
                  </>
                ) : (
                  <Empty title="Select a saved record">
                    Read its derived summary, raw lineage and frozen identity.
                  </Empty>
                )}
              </section>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

export function MethodStatusBanner({ data }: { data: Readiness }) {
  const available = hasVerifiedNativeRuntime(data.native_runtime);
  return (
    <div className="method-banner" data-testid="detailed-method-status">
      <FlaskConical size={24} aria-hidden="true" />
      <div>
        <strong>Detailed method · unqualified</strong>
        <p>
          {words(data.detailed_method.distribution)}{" "}
          {words(data.detailed_method.version)} ·{" "}
          {available
            ? "Pinned native runtime verified; scientific release gates remain unmet"
            : "Pinned native runtime is not verified"}
          . Case preparation freezes input files. Native attempts retain their
          own receipts.
        </p>
      </div>
      <Badge tone="amber">No T2 result admitted</Badge>
    </div>
  );
}

export function ScientificReviewCard({
  data,
  pending,
  onDownload,
}: {
  data: Readiness;
  pending: boolean;
  onDownload: () => void;
}) {
  const attempts = (data.native_executions ?? []).filter(
    (row) =>
      row.qualified === false &&
      row.artifact_readback_verified === true &&
      row.results_eligible_for_comparison === false &&
      row.evidence_basis ===
        "byte_verified_native_diagnostic_not_preregistered_scientific_gate",
  );
  const retainedFailures = attempts.filter(
    (row) => row.status === "failed",
  ).length;
  const canRequest =
    data.review_packet_url ===
    `/api/projects/${data.project_id}/qualification/review-packet`;
  return (
    <section className="evidence-card" aria-label="Scientific review readiness">
      <span className="eyebrow">Scientific release</span>
      <h2>Review the complete evidence trail</h2>
      <Badge tone="amber">Independent review pending</Badge>
      <p>
        {attempts.length} byte-verified native diagnostic receipts ·{" "}
        {retainedFailures} retained failed native attempts. Recorded execution
        is separate from approved scientific evidence. Case compilation and
        imported measurements do not qualify a model.
      </p>
      <p>
        No approved preregistered qualification protocol or validated release
        envelope is established. The manifest links frozen inputs, source
        permissions, runtime identities, native attempts and retained preview
        failures for review.
      </p>
      <button
        className="button"
        type="button"
        disabled={pending || !canRequest}
        onClick={onDownload}
      >
        <Download size={15} aria-hidden="true" />
        {pending
          ? "Downloading review manifest…"
          : "Download scientific review manifest"}
      </button>
      <p className="micro">
        The server checks export permission for every linked source and
        artifact. The manifest provides no scientist’s signature or signoff.
      </p>
      {!canRequest ? (
        <p>The server has not supplied a protected review manifest route.</p>
      ) : null}
      <RawDetails
        title="Scientific admission state"
        data={data.scientific_qualification}
      />
    </section>
  );
}

const gateTitles: Record<string, string> = {
  runtime_identity: "Pinned native runtime",
  mesh_quality: "Mesh quality and fluid connectivity",
  source_assignment: "Native source mass assignment",
  receptor_support: "Native receptor support",
  abl_homogeneity: "Atmospheric boundary-layer homogeneity",
  obstacle_wind_validation: "Independent obstacle wind validation",
  array_wind_validation: "Independent array wind validation",
  scalar_no_source: "No-source scalar test",
  scalar_uniform: "Uniform scalar test",
  scalar_manufactured: "Manufactured scalar solution",
  scalar_positivity: "Scalar positivity",
  scalar_mass_balance: "Scalar mass conservation",
  flow_scalar_stationarity: "Flow and scalar stationarity",
  inventory_background: "Emission inventory and background support",
  campaign_qc: "Measurement campaign quality",
  mesh_sensitivity: "Three-level mesh sensitivity",
  domain_sensitivity: "Domain sensitivity",
  closure_sensitivity: "Turbulence closure sensitivity",
  tracer_validation: "Independent tracer validation",
  heldout_replay: "Held-out measured replay",
  uncertainty_budget: "Complete uncertainty budget",
  paired_no_change: "Paired no-change control",
  paired_source_control: "Paired source-change control",
  protected_receptor_no_harm: "Protected receptor no-harm evidence",
  resource_calibration: "Representative resource calibration",
};
export function scientificTargetText(gate: ScientificGate): string {
  const target = gate.proposed_baseline_target;
  if (!target) return "A method-specific target must be preregistered.";
  const operator = { lt: "<", le: "≤", ge: "≥", gt: ">", eq: "=" }[
    target.operator
  ];
  return `${words(target.name)} ${operator} ${target.threshold} ${target.unit}. Proposed project starting target.`;
}
export function ScientificGateCatalogue({ value }: { value: unknown }) {
  let catalogue;
  try {
    catalogue = decodeScientificRequirements(value);
  } catch (error) {
    return <ErrorBox error={error} />;
  }
  return (
    <section aria-label="Scientific qualification catalogue">
      <div className="evidence-section-title">
        <h2>25 scientific qualification requirements</h2>
        <Badge tone="amber">No release admitted</Badge>
      </div>
      <p>
        These are release requirements awaiting an approved frozen protocol and
        trusted gate receipts. A native diagnostic may exercise a check without
        satisfying its complete qualification requirement. Proposed thresholds
        require scoped scientific review.
      </p>
      <div className="gate-grid">
        {catalogue.gates.map((gate) => (
          <details className="gate-card" key={gate.gate_id}>
            <summary>
              <span className="gate-id">{gate.package}</span>
              <strong>{gateTitles[gate.gate_id] ?? words(gate.gate_id)}</strong>
              <Badge tone="amber">Not admitted</Badge>
            </summary>
            <p>
              Required evidence:{" "}
              {gate.accepted_evidence_basis.map(words).join(", ")}.
            </p>
            <p>{scientificTargetText(gate)}</p>
            {gate.gate_id === "mesh_sensitivity" ? (
              <p>
                At least {gate.minimum_distinct_mesh_levels} distinct frozen
                meshes; repeated runs on one mesh do not substitute.
              </p>
            ) : null}
            <p className="micro">
              No trusted receipt under an approved qualification protocol is
              admitted for this gate.
            </p>
          </details>
        ))}
      </div>
    </section>
  );
}

export function SourcePermissionCard({
  method,
}: {
  method?: AuthorizationMethod;
}) {
  const active = method?.state === "active" && method.available === true;
  return (
    <section
      className="evidence-card source-permission-card"
      data-testid="source-permission-checks"
    >
      <div className="evidence-section-title">
        <div>
          <span className="eyebrow">Source permissions</span>
          <h2>Source permission checks</h2>
        </div>
        <Badge tone={active ? "cyan" : "amber"}>
          {active ? "Cedar · active locally" : "Permission engine unavailable"}
        </Badge>
      </div>
      {method ? (
        <>
          <p>
            Cedar {method.engine_version} evaluates permission to derive, solve
            and export on the local server before preparing or downloading
            files. Frozen source and campaign dependencies are checked too. An
            unavailable engine rejects these gated actions.
          </p>
          <p className="micro">
            {method.binding} {method.binding_version} · community binding by{" "}
            {method.binding_maintainer}. {method.binding_support}.
          </p>
          <RawDetails
            title="Authorization engine and policy identity"
            data={method}
          />
        </>
      ) : (
        <p>
          The server has not supplied its permission-engine status. Reload the
          method view to check availability.
        </p>
      )}
      <p className="micro">
        Permission enforcement does not verify source ownership, establish model
        validity or demonstrate live AWS hosting.
      </p>
    </section>
  );
}

function RawDetails({ title, data }: { title: string; data: unknown }) {
  return (
    <details className="json-viewer">
      <summary>{title}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}
function MetadataForm({
  value,
  onChange,
  predictions = false,
}: {
  value: EvidenceMetadata;
  onChange: (v: EvidenceMetadata) => void;
  predictions?: boolean;
}) {
  return (
    <fieldset className="evidence-fieldset">
      <legend>Identity, provenance and permissions</legend>
      <div className="form-grid">
        <label className="form-label">
          Record title
          <input
            required
            maxLength={120}
            value={value.title}
            onChange={(e) => onChange({ ...value, title: e.target.value })}
          />
        </label>
        <label className="form-label">
          Evidence kind
          <select
            aria-label="Evidence kind"
            value={value.evidence_kind}
            onChange={(e) =>
              onChange({
                ...value,
                evidence_kind: e.target
                  .value as EvidenceMetadata["evidence_kind"],
              })
            }
          >
            {(predictions
              ? ["illustrative", "simulated"]
              : ["illustrative", "observed", "provider-modelled", "simulated"]
            ).map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="form-label evidence-wide">
          Source and lineage label
          <textarea
            required
            maxLength={1000}
            rows={2}
            value={value.source_label}
            onChange={(e) =>
              onChange({ ...value, source_label: e.target.value })
            }
          />
        </label>
        <div className="evidence-wide permission-options">
          {(["derive", "solve", "export"] as const).map((key) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={value.rights[key]}
                onChange={(e) =>
                  onChange({
                    ...value,
                    rights: { ...value.rights, [key]: e.target.checked },
                  })
                }
              />
              {key === "derive"
                ? "Permission to derive"
                : key === "solve"
                  ? "Permission to solve"
                  : "Permission to export"}
            </label>
          ))}
        </div>
        <p className="micro evidence-wide">
          Declare permissions from the source terms. These declarations do not
          certify ownership, measurements or method quality.
        </p>
      </div>
    </fieldset>
  );
}
function InventoryForm({
  onSave,
  pending,
  error,
}: {
  onSave: (r: EvidenceRequest) => void;
  pending: boolean;
  error: unknown;
}) {
  const [metadata, setMetadata] = useState(newMetadata);
  const [species, setSpecies] = useState("passive_tracer");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [segments, setSegments] = useState<RoadSegment[]>([newSegment()]);
  const [backgroundMode, setBackgroundMode] = useState("independent_inflow");
  const [background, setBackground] = useState(NaN);
  const [localBaseline, setLocalBaseline] = useState(NaN);
  const [uncertainty, setUncertainty] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  function segment(index: number, next: RoadSegment) {
    setSegments((old) => old.map((s, i) => (i === index ? next : s)));
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    try {
      // Refuse nonfinite inputs before JSON.stringify could turn them into null.
      const payload = {
        species,
        interval_start: start,
        interval_end: end,
        segments,
        background: {
          mode: backgroundMode,
          value_ug_m3: background,
          includes_local_emissions: backgroundMode !== "independent_inflow",
          local_baseline_ug_m3:
            backgroundMode === "independent_inflow" ? null : localBaseline,
        },
        uncertainty_note: uncertainty,
      };
      const request = { ...metadata, kind: "inventory" as const, payload };
      if (
        Object.values(payload.background).some(
          (v) => typeof v === "number" && !Number.isFinite(v),
        ) ||
        segments.some(
          (s) =>
            !Number.isFinite(s.length_m) ||
            s.fleet.some((f) =>
              Object.values(f).some(
                (v) => typeof v === "number" && !Number.isFinite(v),
              ),
            ),
        )
      )
        throw new Error("Complete all finite numerical inputs before saving.");
      onSave(request);
    } catch (error) {
      setLocalError(error);
    }
  }
  return (
    <form className="evidence-form" onSubmit={submit}>
      <div className="evidence-section-title">
        <div>
          <h2>Traffic emission inventory</h2>
          <p>
            Activity × segment length × emission factor. Rates are derived by
            the server in g/s and kg/s, without assigning sources to a solver.
          </p>
        </div>
        <TemplateButton kind="inventory" />
      </div>
      <MetadataForm value={metadata} onChange={setMetadata} />
      <fieldset className="evidence-fieldset">
        <legend>Species and averaging interval</legend>
        <div className="form-grid">
          <label className="form-label">
            Species
            <select
              value={species}
              onChange={(e) => setSpecies(e.target.value)}
            >
              {["passive_tracer", "PM2.5", "PM10", "NO", "NO2", "CO", "O3"].map(
                (s) => (
                  <option key={s}>{s}</option>
                ),
              )}
            </select>
          </label>
          <label className="form-label">
            Interval start · ISO 8601 with timezone
            <input
              required
              placeholder="2026-10-09T08:00:00+05:30"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label className="form-label">
            Interval end · ISO 8601 with timezone
            <input
              required
              placeholder="2026-10-09T09:00:00+05:30"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
        </div>
      </fieldset>
      {segments.map((s, i) => (
        <fieldset className="evidence-fieldset" key={i}>
          <legend>Road segment {i + 1}</legend>
          <div className="form-grid">
            <label className="form-label">
              Segment ID
              <input
                required
                maxLength={120}
                value={s.id}
                onChange={(e) => segment(i, { ...s, id: e.target.value })}
              />
            </label>
            <NumericInput
              label="Segment length"
              value={s.length_m}
              min={0.001}
              max={100000}
              unit="m"
              onChange={(v) => segment(i, { ...s, length_m: v })}
            />
          </div>
          {s.fleet.map((f, j) => (
            <div className="fleet-entry" key={j}>
              <div className="evidence-section-title">
                <h3>Fleet category {j + 1}</h3>
                {s.fleet.length > 1 ? (
                  <button
                    type="button"
                    className="text-button"
                    aria-label={`Remove fleet category ${j + 1} from segment ${i + 1}`}
                    onClick={() =>
                      segment(i, {
                        ...s,
                        fleet: s.fleet.filter((_, k) => k !== j),
                      })
                    }
                  >
                    <Trash2 size={14} /> Remove
                  </button>
                ) : null}
              </div>
              <div className="form-grid">
                <label className="form-label">
                  Category name
                  <input
                    required
                    maxLength={120}
                    value={f.category}
                    onChange={(e) =>
                      segment(i, {
                        ...s,
                        fleet: s.fleet.map((old, k) =>
                          k === j ? { ...old, category: e.target.value } : old,
                        ),
                      })
                    }
                  />
                </label>
                {(
                  [
                    [
                      "vehicles_per_hour",
                      "Vehicle activity",
                      "vehicles/h",
                      100000,
                    ],
                    [
                      "factor_g_per_vehicle_km",
                      "Emission factor",
                      "g/vehicle/km",
                      1000,
                    ],
                    [
                      "count_uncertainty_fraction",
                      "Count uncertainty fraction",
                      "0–1",
                      1,
                    ],
                    [
                      "factor_uncertainty_fraction",
                      "Factor uncertainty fraction",
                      "0–1",
                      1,
                    ],
                  ] as const
                ).map(([key, label, unit, max]) => (
                  <NumericInput
                    key={key}
                    label={label}
                    value={f[key]}
                    min={0}
                    max={max}
                    unit={unit}
                    onChange={(v) =>
                      segment(i, {
                        ...s,
                        fleet: s.fleet.map((old, k) =>
                          k === j ? { ...old, [key]: v } : old,
                        ),
                      })
                    }
                  />
                ))}
                <label className="form-label">
                  Emission factor source
                  <input
                    required
                    maxLength={1000}
                    value={f.factor_source}
                    onChange={(e) =>
                      segment(i, {
                        ...s,
                        fleet: s.fleet.map((old, k) =>
                          k === j
                            ? { ...old, factor_source: e.target.value }
                            : old,
                        ),
                      })
                    }
                  />
                </label>
              </div>
            </div>
          ))}
          <div className="evidence-actions">
            <button
              type="button"
              className="button small"
              disabled={s.fleet.length >= 30}
              onClick={() =>
                segment(i, { ...s, fleet: [...s.fleet, newFleet()] })
              }
            >
              <Plus size={14} /> Add fleet category
            </button>
            {segments.length > 1 ? (
              <button
                type="button"
                className="text-button"
                aria-label={`Remove road segment ${i + 1}`}
                onClick={() =>
                  setSegments((old) => old.filter((_, k) => k !== i))
                }
              >
                Remove segment
              </button>
            ) : null}
          </div>
        </fieldset>
      ))}
      <button
        type="button"
        className="button small"
        disabled={segments.length >= 200}
        onClick={() => setSegments((old) => [...old, newSegment()])}
      >
        <Plus size={14} /> Add road segment
      </button>
      <fieldset className="evidence-fieldset">
        <legend>Background and uncertainty</legend>
        <div className="form-grid">
          <label className="form-label">
            Background accounting
            <select
              value={backgroundMode}
              onChange={(e) => setBackgroundMode(e.target.value)}
            >
              <option value="independent_inflow">
                Independent inflow · excludes these local emissions
              </option>
              <option value="coarse_total_baseline_adjustment">
                Coarse total · includes a declared local baseline
              </option>
            </select>
          </label>
          <NumericInput
            label="Background concentration"
            value={background}
            min={0}
            max={100000}
            unit="µg/m³"
            onChange={setBackground}
          />
          {backgroundMode !== "independent_inflow" ? (
            <NumericInput
              label="Local baseline already included"
              value={localBaseline}
              min={0}
              max={background}
              unit="µg/m³"
              onChange={setLocalBaseline}
            />
          ) : null}
          <p className="micro evidence-wide">
            Independent inflow must exclude the same local emissions. A coarse
            total needs compatible local-baseline adjustment; adding the full
            local simulation again would double count it.
          </p>
          <label className="form-label evidence-wide">
            Uncertainty and support note
            <textarea
              required
              rows={3}
              maxLength={1000}
              value={uncertainty}
              onChange={(e) => setUncertainty(e.target.value)}
            />
          </label>
        </div>
      </fieldset>
      {localError || error ? <ErrorBox error={localError || error} /> : null}
      <button
        className="button primary"
        disabled={pending || !metadata.rights.derive}
      >
        {pending ? "Saving inventory…" : "Save inventory & calculate rates"}
      </button>
      {!metadata.rights.derive ? (
        <p className="micro">
          Permission to derive is required to calculate this summary.
        </p>
      ) : null}
    </form>
  );
}

function TemplateButton({ kind }: { kind: EvidenceKind }) {
  return (
    <a
      className="button small"
      href={`/examples/phase-b/${kind}-illustrative.json`}
      download={`${kind}-illustrative.json`}
    >
      <Download size={14} /> Illustrative request template
    </a>
  );
}
function ReplayWorkspace(props: {
  records: EvidenceSummary[];
  onSave: (r: EvidenceRequest) => void;
  pending: boolean;
  error: unknown;
}) {
  const [kind, setKind] = useState<"replay_protocol" | "replay_assessment">(
    "replay_protocol",
  );
  return (
    <>
      <nav className="evidence-nav" aria-label="Held-out replay steps">
        <button
          aria-current={kind === "replay_protocol" ? "step" : undefined}
          className={kind === "replay_protocol" ? "active" : ""}
          onClick={() => setKind("replay_protocol")}
        >
          Reserve protocol
        </button>
        <button
          aria-current={kind === "replay_assessment" ? "step" : undefined}
          className={kind === "replay_assessment" ? "active" : ""}
          onClick={() => setKind("replay_assessment")}
        >
          Score held-out means
        </button>
      </nav>
      <EvidenceUpload key={kind} kind={kind} {...props} />
    </>
  );
}
function EvidenceUpload({
  kind,
  records,
  onSave,
  pending,
  error,
}: {
  kind: "measurement_campaign" | "replay_protocol" | "replay_assessment";
  records: EvidenceSummary[];
  onSave: (r: EvidenceRequest) => void;
  pending: boolean;
  error: unknown;
}) {
  const [metadata, setMetadata] = useState(newMetadata);
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [localError, setLocalError] = useState<unknown>(null);
  const [binding, setBinding] = useState("");
  const assessment = kind === "replay_assessment";
  const bindings = records.filter(
    (r) => r.kind === (assessment ? "replay_protocol" : "measurement_campaign"),
  );
  const bindingKey = assessment
    ? "protocol_evidence_id"
    : "campaign_evidence_id";
  function load(value: string, name = "Pasted request") {
    setLocalError(null);
    try {
      const request = parseEvidenceRequest(value, kind);
      setMetadata({
        title: request.title,
        evidence_kind: request.evidence_kind,
        source_label: request.source_label,
        rights: request.rights,
      });
      setText(value);
      setFileName(name);
    } catch (error) {
      setText(value);
      setLocalError(error);
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    try {
      const request = parseEvidenceRequest(text, kind);
      const body = {
        ...request,
        ...metadata,
        payload:
          kind !== "measurement_campaign" && binding
            ? { ...request.payload, [bindingKey]: binding }
            : request.payload,
      };
      onSave(parseEvidenceRequest(JSON.stringify(body), kind));
    } catch (error) {
      setLocalError(error);
    }
  }
  const campaign = kind === "measurement_campaign";
  return (
    <form className="evidence-form" onSubmit={submit}>
      <div className="evidence-section-title">
        <div>
          <h2>
            {campaign
              ? "Measurement campaign QC"
              : assessment
                ? "Score a frozen held-out replay"
                : "Reserve held-out evaluation"}
          </h2>
          <p>
            {campaign
              ? "Import raw readings with physical instrument identity. Flags and exclusions remain alongside the raw values."
              : assessment
                ? "Upload interval-mean predictions with exact observation IDs and frozen method identity. Scoring does not qualify a method."
                : "Bind a protocol to a frozen campaign and reserve whole locations plus disjoint contiguous time blocks."}
          </p>
        </div>
        <TemplateButton kind={kind} />
      </div>
      <details className="evidence-card" open>
        <summary>Required data and meaning</summary>
        {campaign ? (
          <ul>
            <li>
              Each instrument: unique instrument and upstream IDs, physical
              location/species, coordinates and inlet height,
              serial/configuration, calibration version and uncertainty,
              collocation record, maintenance and signed clock offset.
            </li>
            <li>
              Each observation: stable ID, instrument binding, explicit
              timezone-aware start/end, raw value and ug/m3, mg/m3, ppb or ppm,
              provider QA. Gas mixing ratios need temperature and pressure.
            </li>
            <li>
              Missing calibration or collocation stays flagged. Overlapping
              physical-instrument intervals are quarantined; raw rows and
              negative noise remain visible. Upload does not certify a campaign.
            </li>
          </ul>
        ) : assessment ? (
          <ul>
            <li>
              Bind an actual saved protocol. Its campaign and effective
              permissions remain frozen dependencies.
            </li>
            <li>
              Declare species, ug/m3 units, interval_mean support, method ID and
              a sha256 method-build hash. Each prediction needs an observation
              ID, timezone-aware interval and finite concentration.
            </li>
            <li>
              Phase A preview fields are end states at the frozen duration. They
              cannot be relabelled as interval means or uploaded directly as
              replay predictions.
            </li>
            <li>
              The server checks held-out locations, intervals and QC, retains
              near-zero and negative-noise exclusions, and reports
              location-level metrics. Proposed targets remain distinct from
              independent scientific qualification.
            </li>
          </ul>
        ) : (
          <ul>
            <li>
              Use an actual saved campaign ID; the template's evidence-fixture
              binding must be replaced.
            </li>
            <li>
              Calibration and evaluation locations must be disjoint and resolve
              to that campaign. Reserve whole physical instruments; time blocks
              must not overlap.
            </li>
            <li>
              Propose wind NRMSE, concentration FAC2 and near-zero floor
              thresholds with uncertainty rationale. These are project goals,
              not achieved accuracy.
            </li>
            <li>
              Saving a protocol does not prove preregistration or execute a
              held-out evaluation.
            </li>
          </ul>
        )}
      </details>
      <label className="form-label">
        Upload complete request JSON
        <input
          type="file"
          accept=".json,application/json"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            if (file.size > 8_000_000) {
              setLocalError(new Error("Choose a JSON file under 8 MB."));
              return;
            }
            void file
              .text()
              .then((value) => load(value, file.name))
              .catch(setLocalError);
          }}
        />
      </label>
      <label className="form-label">
        Request JSON
        <textarea
          required
          rows={12}
          value={text}
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            if (text && !fileName) load(text);
          }}
        />
      </label>
      <p className="micro">
        {fileName ? `${fileName} · ` : ""}Complete envelope: title, kind,
        evidence_kind, source_label, rights and payload. The server validates
        the complete typed payload before saving.
      </p>
      {kind !== "measurement_campaign" ? (
        <label className="form-label">
          {assessment
            ? "Bind to saved held-out protocol"
            : "Bind to saved measurement campaign"}
          <select
            aria-label={
              assessment
                ? "Bind to saved held-out protocol"
                : "Bind to saved measurement campaign"
            }
            value={binding}
            onChange={(e) => setBinding(e.target.value)}
          >
            <option value="">Use {bindingKey} from request JSON</option>
            {bindings.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title} · {c.evidence_kind} · {c.id}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <MetadataForm
        value={metadata}
        onChange={setMetadata}
        predictions={assessment}
      />
      <p className="micro">
        The labelled metadata above is the metadata saved with this payload.
        Source classifications remain explicit; illustrative readings never
        become observed evidence.
      </p>
      {localError || error ? <ErrorBox error={localError || error} /> : null}
      <button
        className="button primary"
        disabled={pending || !metadata.rights.derive}
      >
        {pending
          ? "Checking and saving…"
          : campaign
            ? "Save campaign & inspect QC"
            : assessment
              ? "Save replay assessment & inspect scores"
              : "Save held-out protocol"}
      </button>
      {!metadata.rights.derive ? (
        <p className="micro">
          Permission to derive is required to process this evidence.
        </p>
      ) : null}
    </form>
  );
}

function CaseForm({
  scenario,
  scenarios,
  onScenario,
  onPrepare,
  pending,
  error,
}: {
  scenario?: Scenario;
  scenarios: Scenario[];
  onScenario: (id: string) => void;
  onPrepare: (b: {
    title: string;
    scenario_id: string;
    expected_version: number;
    config: Record<string, unknown>;
  }) => void;
  pending: boolean;
  error: unknown;
}) {
  const [title, setTitle] = useState("");
  const [core, setCore] = useState<Record<string, number>>({});
  const [advanced, setAdvanced] = useState("{}");
  const [localError, setLocalError] = useState<unknown>(null);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!scenario) return;
    setLocalError(null);
    try {
      onPrepare({
        title,
        scenario_id: scenario.id,
        expected_version: scenario.version,
        config: caseConfig(core, advanced),
      });
    } catch (error) {
      setLocalError(error);
    }
  }
  if (!scenario)
    return (
      <Empty title="A scenario is required">
        Import site geometry or select a saved scenario before preparing
        detailed method files.
      </Empty>
    );
  return (
    <form className="evidence-form" onSubmit={submit}>
      <div className="evidence-section-title">
        <div>
          <h2>Prepare a reproducible detailed case</h2>
          <p>
            Freeze checked metre geometry, atmospheric conditions and controls
            into a downloadable case ZIP. This action does not start a solver.
          </p>
        </div>
        <Badge tone="amber">Neutral passive tracer only</Badge>
      </div>
      <div className="form-grid">
        <label className="form-label">
          Case title
          <input
            required
            value={title}
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="form-label">
          Selected scenario
          <select
            aria-label="Selected scenario"
            value={scenario.id}
            onChange={(e) => onScenario(e.target.value)}
          >
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} · v{s.version} · {s.id}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="micro">
        Preparing <code>{scenario.id}</code> · v{scenario.version} · hash{" "}
        <code>{scenario.content_hash}</code>. Edits create new snapshots; saved
        cases retain this identity.
      </p>
      <fieldset className="evidence-fieldset">
        <legend>Explicit physical parameters</legend>
        <div className="form-grid">
          {caseFields.map((f) => (
            <NumericInput
              key={f.key}
              label={f.label}
              unit={f.unit}
              min={f.min}
              max={f.max}
              value={core[f.key]}
              onChange={(value) =>
                setCore((old) => ({ ...old, [f.key]: value }))
              }
            />
          ))}
        </div>
        <p className="micro">
          Use compatible reference height, ground roughness and neutral
          atmospheric support. Imported sources remain passive tracer sources;
          this compiler does not model reactive chemistry or particle
          deposition.
        </p>
      </fieldset>
      <details className="evidence-fieldset">
        <summary>Advanced domain, mesh and numerical controls</summary>
        <p>
          Use the compiler's bounded control names. Omitted controls use
          explicit compiler defaults that are frozen in the manifest. Core
          physical fields cannot be overridden here.
        </p>
        <ul className="advanced-controls">
          {Object.entries(advancedCaseFields).map(([key, field]) => (
            <li key={key}>
              <code>{key}</code> · {field.min}–{field.max}
              {field.integer ? " · integer" : ""}
            </li>
          ))}
        </ul>
        <label className="form-label">
          Advanced control JSON
          <textarea
            rows={8}
            spellCheck={false}
            value={advanced}
            onChange={(e) => setAdvanced(e.target.value)}
          />
        </label>
        <p className="micro">
          Unknown keys, incompatible controls, invalid geometry or
          source/receptor support are rejected by the server.
        </p>
      </details>
      {localError || error ? <ErrorBox error={localError || error} /> : null}
      <button className="button primary" disabled={pending}>
        {pending ? "Checking and compiling files…" : "Prepare case files"}
      </button>
    </form>
  );
}

function RecordDetail({
  record,
  onDownload,
}: {
  record: EvidenceRecord;
  onDownload: () => void;
}) {
  const summary = record.summary;
  const rows = objects(summary.rows);
  return (
    <>
      <div className="evidence-section-title">
        <h2>{record.title}</h2>
        <Badge
          tone={
            record.effective_evidence_kind === "illustrative"
              ? "amber"
              : "quiet"
          }
        >
          {record.effective_evidence_kind}
        </Badge>
      </div>
      <p>{record.source_label}</p>
      <p className="micro">
        Declared class: {record.evidence_kind} · effective class including
        frozen dependencies: {record.effective_evidence_kind}. Dependencies
        cannot acquire a stronger evidence classification through upload.
      </p>
      <p className="micro">
        Saved {record.created_at} · <code>{record.id}</code>
      </p>
      <p className="micro">
        Content hash <code>{record.content_hash}</code>
      </p>
      <div className="record-qualification">
        <strong>{words(summary.qualification)}</strong>
        <span>Method status remains unqualified.</span>
      </div>
      {record.kind === "inventory" ? (
        <>
          <div className="evidence-metrics">
            <div>
              <span>Total emission rate</span>
              <strong>
                {number(summary.total_rate_g_s)} <small>g/s</small>
              </strong>
            </div>
            <div>
              <span>Canonical mass rate</span>
              <strong>
                {number(summary.total_rate_kg_s)} <small>kg/s</small>
              </strong>
            </div>
            <div>
              <span>Species</span>
              <strong>{words(summary.species)}</strong>
            </div>
          </div>
          <p className="micro">
            {words(summary.interval_start_utc)} →{" "}
            {words(summary.interval_end_utc)} · {words(summary.background_mode)}
          </p>
          <div className="table-wrap">
            <table>
              <caption>Derived road segment rates</caption>
              <thead>
                <tr>
                  <th>Segment</th>
                  <th>Length (m)</th>
                  <th>Rate (g/s)</th>
                  <th>Line rate (g/s/m)</th>
                  <th>Parameter range (g/s)</th>
                </tr>
              </thead>
              <tbody>
                {objects(summary.segments).map((r) => (
                  <tr key={String(r.id)}>
                    <td>{words(r.id)}</td>
                    <td>{number(r.length_m)}</td>
                    <td>{number(r.rate_g_s)}</td>
                    <td>{number(r.line_rate_g_s_m)}</td>
                    <td>
                      {Array.isArray(r.range_g_s)
                        ? r.range_g_s.map(number).join(" – ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {record.kind === "measurement_campaign" ? (
        <>
          <div className="evidence-metrics">
            <div>
              <span>Raw rows retained</span>
              <strong>{number(summary.row_count)}</strong>
            </div>
            <div>
              <span>Instruments</span>
              <strong>{number(summary.instrument_count)}</strong>
            </div>
            {["accepted", "flagged", "quarantined"].map((k) => (
              <div key={k}>
                <span>{k}</span>
                <strong>
                  {number(
                    isObject(summary.counts) ? (summary.counts[k] ?? 0) : 0,
                  )}
                </strong>
              </div>
            ))}
          </div>
          <p className="micro">
            QC is an automated screen. Quarantine excludes a row from use
            without deleting its raw reading. Negative measurement noise is
            retained.
          </p>
          <div
            className="table-wrap"
            tabIndex={0}
            role="region"
            aria-label="Measurement QC rows, horizontally scrollable"
          >
            <table>
              <caption>
                Raw lineage and canonical time support · showing{" "}
                {Math.min(rows.length, 200)} of {rows.length} rows; complete
                data in protected download
              </caption>
              <thead>
                <tr>
                  <th>Observation / physical instrument</th>
                  <th>Location / species</th>
                  <th>Raw reading</th>
                  <th>Canonical (µg/m³)</th>
                  <th>UTC interval</th>
                  <th>QC / flags</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 200).map((r, i) => (
                  <tr key={typeof r.id === "string" ? r.id : i}>
                    <td>
                      <strong>{words(r.id)}</strong>
                      <small>
                        {words(r.instrument_id)} / {words(r.upstream_id)}
                      </small>
                    </td>
                    <td>
                      {words(r.location_id)}
                      <small>{words(r.species)}</small>
                    </td>
                    <td>
                      {number(r.raw_value)} <small>{words(r.raw_unit)}</small>
                    </td>
                    <td>{number(r.value_ug_m3)}</td>
                    <td>
                      {words(r.interval_start_utc)}
                      <small>→ {words(r.interval_end_utc)}</small>
                    </td>
                    <td>
                      <Badge
                        tone={r.qc_state === "accepted" ? "cyan" : "amber"}
                      >
                        {words(r.qc_state)}
                      </Badge>
                      <small>
                        {strings(r.flags).map(words).join("; ") ||
                          "No automated flag"}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      {record.kind === "replay_protocol" ? (
        <>
          <p>
            Frozen campaign <code>{String(summary.campaign_evidence_id)}</code>
          </p>
          <div className="evidence-overview">
            <div className="evidence-card">
              <h3>Calibration reservation</h3>
              <p>{strings(summary.calibration_locations).join(", ")}</p>
              <RawDetails
                title="Calibration time blocks"
                data={summary.calibration_blocks}
              />
            </div>
            <div className="evidence-card">
              <h3>Evaluation reservation</h3>
              <p>{strings(summary.evaluation_locations).join(", ")}</p>
              <RawDetails
                title="Evaluation time blocks"
                data={summary.evaluation_blocks}
              />
            </div>
          </div>
          <RawDetails
            title="Proposed thresholds · not achieved accuracy"
            data={summary.thresholds}
          />
        </>
      ) : null}
      {record.kind === "replay_assessment" ? (
        <ReplayScores summary={summary} />
      ) : null}
      <ul className="evidence-limits">
        {[...strings(summary.limits), ...strings(summary.limitations)].map(
          (s, i) => (
            <li key={i}>{s}</li>
          ),
        )}
      </ul>
      <RawDetails
        title="Frozen typed raw payload and provenance"
        data={{
          payload: record.payload,
          evidence_kind: record.evidence_kind,
          source_label: record.source_label,
          rights: record.rights,
          effective_rights: record.effective_rights,
          effective_evidence_kind: record.effective_evidence_kind,
          dependencies: record.dependencies,
        }}
      />
      <RawDetails
        title="Complete server-derived summary"
        data={record.summary}
      />
      <button
        className="button primary"
        disabled={!record.effective_rights.export}
        onClick={onDownload}
      >
        <Download size={15} /> Download frozen evidence JSON
      </button>
      {!record.effective_rights.export ? (
        <p className="micro">
          Export is restricted by this record or its frozen dependency's source
          rights.
        </p>
      ) : null}
    </>
  );
}

function ReplayScores({ summary }: { summary: Record<string, unknown> }) {
  const aggregate = isObject(summary.aggregate) ? summary.aggregate : {};
  const locations = objects(summary.by_location);
  const rows = objects(summary.rows);
  return (
    <>
      <p className="micro">
        Imported method identity is self-reported. These arithmetic scores do
        not attest solver execution or qualify a method.
      </p>
      <p className="micro">Wind accuracy: {words(summary.wind_gate)}.</p>
      <div className="record-qualification">
        <strong>FAC2 target · {words(summary.fac2_gate)}</strong>
        <span>Arithmetic target only · method remains unqualified</span>
      </div>
      <div className="evidence-metrics">
        {[
          ["count", "Aligned predictions"],
          ["rmse_ug_m3", "RMSE (µg/m³)"],
          ["bias_ug_m3", "Bias (µg/m³)"],
          ["fac2", "FAC2"],
          ["fac2_floor_excluded_count", "Floor exclusions"],
        ].map(([key, label]) => (
          <div key={key}>
            <span>{label}</span>
            <strong>{number(aggregate[key])}</strong>
          </div>
        ))}
      </div>
      <div
        className="table-wrap"
        tabIndex={0}
        role="region"
        aria-label="Replay location scores, horizontally scrollable"
      >
        <table>
          <caption>
            Held-out scores by location · no scientific qualification
          </caption>
          <thead>
            <tr>
              <th>Location</th>
              <th>Count</th>
              <th>Bias (µg/m³)</th>
              <th>MAE (µg/m³)</th>
              <th>RMSE (µg/m³)</th>
              <th>FAC2</th>
            </tr>
          </thead>
          <tbody>
            {locations.map((r, i) => (
              <tr key={i}>
                <td>{words(r.location_id)}</td>
                {["count", "bias_ug_m3", "mae_ug_m3", "rmse_ug_m3", "fac2"].map(
                  (k) => (
                    <td key={k}>{number(r[k])}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div
        className="table-wrap"
        tabIndex={0}
        role="region"
        aria-label="Replay matched observations, horizontally scrollable"
      >
        <table>
          <caption>
            Matched raw observations and imported means · showing{" "}
            {Math.min(200, rows.length)} of {rows.length}
          </caption>
          <thead>
            <tr>
              <th>Observation / location</th>
              <th>Observed (µg/m³)</th>
              <th>Predicted mean (µg/m³)</th>
              <th>Difference (µg/m³)</th>
              <th>FAC2 / flags</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 200).map((r, i) => (
              <tr key={i}>
                <td>
                  {words(r.observation_id)}
                  <small>{words(r.location_id)}</small>
                </td>
                <td>{number(r.observed_ug_m3)}</td>
                <td>{number(r.predicted_ug_m3)}</td>
                <td>{number(r.difference_ug_m3)}</td>
                <td>
                  {r.fac2_eligible
                    ? r.fac2_pass
                      ? "Within factor 2"
                      : "Outside factor 2"
                    : "Excluded from FAC2"}
                  <small>{strings(r.qc_flags).map(words).join("; ")}</small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <RawDetails
        title="QC exclusions · raw readings retained"
        data={summary.qc_exclusions}
      />
      <RawDetails
        title="Outside reserved support"
        data={summary.outside_reserved_support}
      />
      <RawDetails
        title="Scores by reserved time block"
        data={summary.by_time_block}
      />
    </>
  );
}
