export type EvidenceKind =
  | "inventory"
  | "measurement_campaign"
  | "replay_protocol"
  | "replay_assessment";
export type EvidenceClass =
  "observed" | "provider-modelled" | "simulated" | "illustrative";
export interface EvidenceMetadata {
  title: string;
  evidence_kind: EvidenceClass;
  source_label: string;
  rights: { derive: boolean; solve: boolean; export: boolean };
}
export interface EvidenceRequest extends EvidenceMetadata {
  kind: EvidenceKind;
  payload: Record<string, unknown>;
}
export interface EvidenceSummary extends EvidenceMetadata {
  schema_version: "phase-b-evidence-v1";
  kind: EvidenceKind;
  id: string;
  effective_evidence_kind: EvidenceClass;
  project_id: string;
  created_at: string;
  content_hash: string;
  summary: Record<string, unknown>;
  effective_rights: EvidenceMetadata["rights"];
  dependencies: Record<string, unknown>[];
  download_url?: string;
}
export interface EvidenceRecord extends EvidenceSummary {
  payload: Record<string, unknown>;
}
export interface DetailedCase {
  schema_version: "phase-b-case-v1";
  id: string;
  project_id: string;
  title: string;
  scenario_id: string;
  scenario_version: number;
  scenario_hash: string;
  created_at: string;
  content_hash: string;
  manifest: Record<string, unknown>;
  rights: EvidenceMetadata["rights"];
  file_count: number;
  file_bytes: number;
  bundle_checksum: string;
  status: "prepared_unexecuted";
  qualified: false;
  download_url?: string;
}
export interface Readiness {
  schema_version: "phase-b-readiness-v1";
  project_id: string;
  status: "unqualified";
  detailed_method: Record<string, unknown> & {
    qualified: false;
    runtime_available: boolean;
    limitations: string[];
  };
  research_preview: Record<string, unknown>;
  packages: { id: string; title: string; state: string; remaining: string[] }[];
  evidence: EvidenceSummary[];
  cases: DetailedCase[];
  local_resource_observations: unknown;
  authorization_method: AuthorizationMethod;
  native_runtime?: NativeRuntime;
  native_executions?: NativeExecutionSummary[];
  scientific_requirements?: unknown;
  scientific_qualification?: {
    status: "unqualified";
    qualified: false;
    scientific_gates_passed: false;
    preregistered_protocol: null;
    independent_review_verified: false;
    trusted_scientific_gate_receipt_count: number;
    reason: string;
  };
  review_packet_url?: string;
  source_allocations?: SourceAllocation[];
}
export interface SourceAllocation {
  schema_version: "phase-b-source-allocation-record-v1";
  id: string;
  title: string;
  qualified: false;
  status: "allocated_unexecuted";
  plan_hash: string;
  project_id: string;
  scenario_id: string;
  parent_scenario_id: string;
  inventory_evidence_id: string;
  content_hash: string;
  artifact_checksum: string;
  effective_rights: EvidenceMetadata["rights"];
  effective_evidence_kind: EvidenceClass;
  summary: {
    species: "passive_tracer";
    interval_start_utc: string;
    interval_end_utc: string;
    total_rate_g_s: number;
    total_range_g_s: [number, number];
    source_count: number;
    background_ug_m3: number;
    range_basis: string;
    qualified: false;
  };
  download_url?: string;
}
export interface NativeRuntime {
  available: boolean;
  qualified: false;
  runtime_digest?: string;
  runtime_identity?: Record<string, unknown>;
  reason?: string;
}
export interface NativeExecutionSummary {
  receipt_id: string;
  execution_id: string;
  case_id: string;
  status: "diagnostic_only" | "complete_unqualified" | "failed" | "cancelled";
  qualified: false;
  numerical_verification_passed: boolean;
  artifact_readback_verified: true;
  content_hash: string;
  native_execution_hash: string;
  runtime_digest: string;
  evidence_basis: "byte_verified_native_diagnostic_not_preregistered_scientific_gate";
  results_eligible_for_comparison: false;
  current_attempt?: boolean;
  receipt_url?: string | null;
  download_url?: string | null;
}
export interface ScientificGate {
  gate_id: string;
  package: string;
  state: "unexecuted";
  accepted_evidence_basis: string[];
  minimum_distinct_mesh_levels: number;
  proposed_baseline_target: null | {
    name: string;
    unit: string;
    operator: "lt" | "le" | "ge" | "gt" | "eq";
    threshold: number;
  };
}
export interface ScientificRequirements {
  schema_version: "phase-b-qualification-requirements-v1";
  qualified: false;
  status: "requires_preregistered_protocol_and_executed_evidence";
  threshold_basis: "preserved_project_proposed_targets_not_industry_certification";
  gates: ScientificGate[];
  independent_human_review_required: true;
  promotion_requirements: string[];
}

const requiredGateIds = [
  "runtime_identity",
  "mesh_quality",
  "source_assignment",
  "receptor_support",
  "abl_homogeneity",
  "obstacle_wind_validation",
  "array_wind_validation",
  "scalar_no_source",
  "scalar_uniform",
  "scalar_manufactured",
  "scalar_positivity",
  "scalar_mass_balance",
  "flow_scalar_stationarity",
  "inventory_background",
  "campaign_qc",
  "mesh_sensitivity",
  "domain_sensitivity",
  "closure_sensitivity",
  "tracer_validation",
  "heldout_replay",
  "uncertainty_budget",
  "paired_no_change",
  "paired_source_control",
  "protected_receptor_no_harm",
  "resource_calibration",
];
export function hasVerifiedNativeRuntime(value?: NativeRuntime): boolean {
  return (
    value?.available === true &&
    value.qualified === false &&
    /^sha256:[0-9a-f]{64}$/.test(value.runtime_digest ?? "") &&
    isObject(value.runtime_identity)
  );
}
export function decodeScientificRequirements(
  value: unknown,
): ScientificRequirements {
  const fail = () => {
    throw new Error(
      "The scientific gate catalogue is unavailable or inconsistent. No qualification can be inferred.",
    );
  };
  if (
    !isObject(value) ||
    value.schema_version !== "phase-b-qualification-requirements-v1" ||
    value.qualified !== false ||
    value.status !== "requires_preregistered_protocol_and_executed_evidence" ||
    value.threshold_basis !==
      "preserved_project_proposed_targets_not_industry_certification" ||
    value.independent_human_review_required !== true ||
    !Array.isArray(value.gates) ||
    value.gates.length !== requiredGateIds.length ||
    !Array.isArray(value.promotion_requirements) ||
    !value.promotion_requirements.length ||
    value.promotion_requirements.some((v) => typeof v !== "string")
  )
    return fail();
  const ids = new Set<string>();
  for (const gate of value.gates) {
    if (
      !isObject(gate) ||
      typeof gate.gate_id !== "string" ||
      !requiredGateIds.includes(gate.gate_id) ||
      ids.has(gate.gate_id) ||
      typeof gate.package !== "string" ||
      !/^B(?:0[1-9]|1[0-2])$/.test(gate.package) ||
      gate.state !== "unexecuted" ||
      !Array.isArray(gate.accepted_evidence_basis) ||
      !gate.accepted_evidence_basis.length ||
      gate.accepted_evidence_basis.some(
        (v) =>
          typeof v !== "string" ||
          ![
            "native_execution",
            "independent_observation",
            "heldout_observation",
            "measurement_qc",
            "uncertainty_analysis",
            "native_paired_execution",
            "runtime_measurement",
          ].includes(v),
      ) ||
      typeof gate.minimum_distinct_mesh_levels !== "number" ||
      !Number.isInteger(gate.minimum_distinct_mesh_levels) ||
      gate.minimum_distinct_mesh_levels <
        (gate.gate_id === "mesh_sensitivity" ? 3 : 1)
    )
      return fail();
    const target = gate.proposed_baseline_target;
    if (
      target !== null &&
      (!isObject(target) ||
        typeof target.name !== "string" ||
        typeof target.unit !== "string" ||
        typeof target.operator !== "string" ||
        !["lt", "le", "ge", "gt", "eq"].includes(target.operator) ||
        typeof target.threshold !== "number" ||
        !Number.isFinite(target.threshold) ||
        target.threshold < 0)
    )
      return fail();
    ids.add(gate.gate_id);
  }
  return value as unknown as ScientificRequirements;
}
export interface AuthorizationMethod {
  id: "cedar-phase-b-permissions-v1";
  state: "active" | "unavailable_fail_closed";
  available: boolean;
  engine: string;
  engine_version: string;
  binding: string;
  binding_version: string;
  binding_maintainer: string;
  binding_support: string;
  license: string;
  aws_origin: string;
  policy_id: string;
  policy_sha256: string;
  schema_sha256: string;
  provenance_sha256: string;
  actions: ("derive" | "solve" | "export")[];
  fail_closed: true;
  error_diagnostics_blocked: true;
  local_evaluation: true;
  cloud_service: false;
  scope: string;
  source_urls: { engine: string; binding: string; aws_origin: string };
}
export interface FleetItem {
  category: string;
  vehicles_per_hour: number;
  factor_g_per_vehicle_km: number;
  factor_source: string;
  count_uncertainty_fraction: number;
  factor_uncertainty_fraction: number;
}
export interface RoadSegment {
  id: string;
  length_m: number;
  fleet: FleetItem[];
}
export const newFleet = (): FleetItem => ({
  category: "",
  vehicles_per_hour: NaN,
  factor_g_per_vehicle_km: NaN,
  factor_source: "",
  count_uncertainty_fraction: NaN,
  factor_uncertainty_fraction: NaN,
});
export const newSegment = (): RoadSegment => ({
  id: "",
  length_m: NaN,
  fleet: [newFleet()],
});
export const newMetadata = (): EvidenceMetadata => ({
  title: "",
  evidence_kind: "illustrative",
  source_label: "",
  rights: { derive: false, solve: false, export: false },
});
export function isObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}
export function assertFiniteJson(v: unknown): void {
  if (typeof v === "number" && !Number.isFinite(v))
    throw new Error(
      "A number is outside the finite range. Correct it before saving; it will not be replaced by a missing reading.",
    );
  if (Array.isArray(v)) v.forEach(assertFiniteJson);
  else if (isObject(v)) Object.values(v).forEach(assertFiniteJson);
}
export function parseEvidenceRequest(
  text: string,
  kind: EvidenceKind,
): EvidenceRequest {
  const v: unknown = JSON.parse(text);
  assertFiniteJson(v);
  if (
    !isObject(v) ||
    v.kind !== kind ||
    !isObject(v.payload) ||
    !isObject(v.rights)
  )
    throw new Error(
      `Use a complete ${kind.replaceAll("_", " ")} request with title, kind, evidence_kind, source_label, rights and payload.`,
    );
  const keys = [
    "title",
    "kind",
    "evidence_kind",
    "source_label",
    "rights",
    "payload",
  ];
  if (
    Object.keys(v).some((k) => !keys.includes(k)) ||
    keys.some((k) => !(k in v))
  )
    throw new Error(
      "The request has missing or unknown envelope fields. Use the supplied request template.",
    );
  if (
    typeof v.title !== "string" ||
    !v.title.trim() ||
    typeof v.source_label !== "string" ||
    !v.source_label.trim()
  )
    throw new Error("A title and explicit source label are required.");
  if (
    !["observed", "provider-modelled", "simulated", "illustrative"].includes(
      String(v.evidence_kind),
    )
  )
    throw new Error("Choose an explicit evidence kind.");
  if (
    kind === "replay_assessment" &&
    !["simulated", "illustrative"].includes(String(v.evidence_kind))
  )
    throw new Error(
      "Replay predictions must be classified as simulated or illustrative; they cannot be observed evidence.",
    );
  const rights = v.rights;
  if (
    Object.keys(rights).length !== 3 ||
    ["derive", "solve", "export"].some((k) => typeof rights[k] !== "boolean")
  )
    throw new Error(
      "Rights must declare derive, solve and export as true or false.",
    );
  return v as unknown as EvidenceRequest;
}
export const caseFields = [
  {
    key: "wind_from_deg",
    label: "Wind from",
    unit: "°",
    min: 0,
    max: 359.999999,
  },
  {
    key: "reference_speed_m_s",
    label: "Reference wind speed",
    unit: "m/s",
    min: 0.5,
    max: 20,
  },
  {
    key: "reference_height_m",
    label: "Reference height",
    unit: "m",
    min: 2,
    max: 100,
  },
  {
    key: "roughness_length_m",
    label: "Ground roughness length",
    unit: "m",
    min: 0.001,
    max: 2,
  },
  {
    key: "kinematic_viscosity_m2_s",
    label: "Kinematic viscosity",
    unit: "m²/s",
    min: 1e-6,
    max: 1e-3,
  },
  {
    key: "molecular_diffusivity_m2_s",
    label: "Molecular diffusivity",
    unit: "m²/s",
    min: 0,
    max: 1e-2,
  },
  {
    key: "turbulent_schmidt_number",
    label: "Turbulent Schmidt number",
    unit: "dimensionless",
    min: 0.1,
    max: 2,
  },
  {
    key: "background_ug_m3",
    label: "Passive tracer background",
    unit: "µg/m³",
    min: 0,
    max: 10000,
  },
] as const;
export const advancedCaseFields: Record<
  string,
  { min: number; max: number; integer?: boolean }
> = {
  base_cell_size_m: { min: 0.5, max: 20 },
  surface_refinement_level: { min: 0, max: 3, integer: true },
  source_box_half_width_m: { min: 0.25, max: 20 },
  upstream_h: { min: 5, max: 20 },
  downstream_h: { min: 15, max: 40 },
  lateral_h: { min: 5, max: 20 },
  top_h: { min: 5, max: 20 },
  max_cells: { min: 1000, max: 10000000, integer: true },
  flow_iterations: { min: 100, max: 10000, integer: true },
  residual_tolerance: { min: 1e-8, max: 1e-3 },
  scalar_residual_tolerance: { min: 1e-8, max: 1e-3 },
  max_wall_seconds: { min: 60, max: 86400, integer: true },
};
export function caseConfig(
  core: Record<string, number>,
  advancedText: string,
): Record<string, unknown> {
  const advanced: unknown = JSON.parse(advancedText);
  if (!isObject(advanced))
    throw new Error("Advanced controls must be a JSON object.");
  assertFiniteJson(advanced);
  const output: Record<string, unknown> = { ...advanced };
  for (const field of caseFields) {
    if (field.key in advanced)
      throw new Error(
        `Set ${field.label.toLowerCase()} in its labelled field, rather than overriding it in advanced controls.`,
      );
    const value = core[field.key];
    if (!Number.isFinite(value) || value < field.min || value > field.max)
      throw new Error(
        `${field.label} must be between ${field.min} and ${field.max} ${field.unit}.`,
      );
    output[field.key] = value;
  }
  for (const [key, value] of Object.entries(advanced)) {
    const field = Object.hasOwn(advancedCaseFields, key)
      ? advancedCaseFields[key]
      : undefined;
    if (!field)
      throw new Error(
        `Unknown advanced control: ${key}. Use a declared compiler control.`,
      );
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < field.min ||
      value > field.max ||
      (field.integer && !Number.isInteger(value))
    )
      throw new Error(
        `${key} must be ${field.integer ? "an integer" : "a number"} between ${field.min} and ${field.max}.`,
      );
  }
  if (core.reference_height_m <= 20 * core.roughness_length_m)
    throw new Error(
      "Reference height must exceed 20 ground roughness lengths for this neutral preparation envelope.",
    );
  if (
    Number(output.source_box_half_width_m ?? 4) <
    Number(output.base_cell_size_m ?? 8) / 2
  )
    throw new Error(
      "Source support must cover at least one base-cell width; increase source half-width or refine the base cell.",
    );
  return output;
}
export function controlledDownloadPath(path: string): boolean {
  if (path !== path.trim() || /[\r\n]/.test(path)) return false;
  return (
    /^\/(?:scenarios|runs)\/[A-Za-z0-9_-]+\/export$/.test(path) ||
    /^\/reports\/[A-Za-z0-9_-]+\/download\?format=(?:html|pdf|json)$/.test(
      path,
    ) ||
    /^\/projects\/[A-Za-z0-9_-]+\/(?:evidence|detailed-cases|detailed-executions|source-allocations)\/[A-Za-z0-9_-]+\/download$/.test(
      path,
    ) ||
    /^\/projects\/project_[0-9a-f]{32}\/qualification\/review-packet$/.test(
      path,
    )
  );
}
