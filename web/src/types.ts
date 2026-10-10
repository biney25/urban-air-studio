import type * as Contract from "../../contracts/api-types";
export type Point = [number, number];
export interface Building {
  id: string;
  name: string;
  footprint: Point[];
  height_m: number;
}
export interface Source {
  id: string;
  name: string;
  x: number;
  y: number;
  z: number;
  rate_g_s: number;
}
export interface Receptor {
  id: string;
  name: string;
  x: number;
  y: number;
  z: number;
  protected: boolean;
}
export interface Scenario {
  id: string;
  project_id: string;
  name: string;
  version: number;
  content_hash: string;
  parent_id: string | null;
  created_at: string;
  origin: { longitude: number; latitude: number };
  buildings: Building[];
  sources: Source[];
  receptors: Receptor[];
  diagnostics: Record<string, unknown>;
  evidence_kind: string;
  synthetic?: boolean;
  source_label?: string;
  coordinate_frame?: Record<string, unknown>;
  analysis_domain?: Record<string, unknown>;
}
export interface Project {
  id: string;
  name: string;
  created_at: string;
  baseline_scenario_id: string | null;
}
export type Conditions = Required<Contract.Conditions>;
export interface Field {
  schema_version: string;
  extent: [number, number, number, number];
  nx: number;
  ny: number;
  height_m: number;
  values: (number | null)[];
  mask: boolean[];
  units: string;
  layout?: string;
  support?: string;
  time_support?: string;
}
export interface Metric {
  receptor_id: string;
  name: string;
  value_ug_m3: number | null;
  valid: boolean;
  reason?: string | null;
  requested_height_m?: number;
  cell_centre?: [number, number, number] | null;
  sampling?: string;
  metric_definition?: string;
}
export interface Result {
  field: Field;
  metrics: Metric[];
  diagnostics: Record<string, unknown>;
  evidence_kind: string;
  qualification: string;
  limitations: string[];
}
export type RunStatus =
  | "queued"
  | "preparing"
  | "solving"
  | "verifying"
  | "complete_unqualified"
  | "failed"
  | "cancelled";
export interface Run {
  id: string;
  project_id: string;
  scenario_id: string;
  status: RunStatus;
  progress: number;
  stage: string;
  created_at: string;
  manifest: Record<string, unknown>;
  result?: Result | null;
  error: unknown;
  cancel_requested: boolean;
  attempt: number;
}
export interface ImportRecord {
  id: string;
  project_id: string;
  filename: string;
  source_label: string;
  rights: Record<string, boolean>;
  created_at: string;
  scenario_id: string;
  audit: Record<string, unknown>;
}
export interface ProjectDetail {
  project: Project;
  scenarios: Scenario[];
  runs: Run[];
  imports: ImportRecord[];
  reports?: Report[];
}
export interface Quote {
  id: string;
  cases: number;
  maximum_seconds: number;
  expires_at: string;
  limitations: string[];
}
export interface ComparisonMetric {
  receptor_id: string;
  name: string;
  baseline_ug_m3: number | null;
  alternative_ug_m3: number | null;
  delta_ug_m3: number | null;
  percent_change: number | null;
  protected: boolean;
  valid?: boolean;
  reason?: string | null;
  requested_height_m?: number;
  baseline_cell_centre?: [number, number, number] | null;
  alternative_cell_centre?: [number, number, number] | null;
  sampling?: string;
  metric_definition?: string;
  percent_suppression_reason?: string | null;
}
export interface Comparison {
  baseline_run_id: string;
  alternative_run_id: string;
  metrics: ComparisonMetric[];
  qualification: string;
  limitations: string[];
}
export interface Report {
  id: string;
  project_id: string;
  title: string;
  status: string;
  created_at: string;
  baseline_run_id: string;
  alternative_run_id: string;
  comparison: Comparison;
  manifest: Record<string, unknown>;
  limitations: string[];
  checksum: string;
}
export type Operation =
  | Contract.BuildingOperation
  | Contract.SourceOperation
  | Contract.ReceptorOperation;
/* Local view refinements retain tuple coordinates; wire requests use the generated API union. */
export type ViewOperation =
  | {
      type: "building_update";
      object_id: string;
      changes: {
        height_m?: number;
        dx?: number;
        dy?: number;
        rotation_deg?: number;
      };
    }
  | {
      type: "source_update";
      object_id: string;
      changes: { rate_g_s?: number; x?: number; y?: number; z?: number };
    }
  | {
      type: "receptor_update";
      object_id: string;
      changes: { x?: number; y?: number; z?: number };
    };
export type GeoJSON = Record<string, unknown>;
export type Selection = {
  kind: "building" | "source" | "receptor";
  id: string;
};
