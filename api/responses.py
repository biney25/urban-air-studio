"""Typed browser-facing contracts; extensible metadata does not weaken core units."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from api.schemas import Conditions, Rights


class Output(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class Project(Output):
    id: str
    name: str
    created_at: str
    baseline_scenario_id: str | None


class Origin(Output):
    longitude: float = Field(ge=-180,le=180)
    latitude: float = Field(ge=-90,le=90)


class Building(Output):
    id: str
    name: str
    footprint: list[list[float]]
    height_m: float = Field(gt=0,le=200)


class Source(Output):
    id: str
    name: str
    x: float
    y: float
    z: float
    rate_g_s: float = Field(ge=0)


class Receptor(Output):
    id: str
    name: str
    x: float
    y: float
    z: float
    protected: bool


class Scenario(Output):
    id: str
    project_id: str
    name: str
    version: int = Field(ge=1)
    content_hash: str
    parent_id: str | None
    created_at: str
    origin: Origin
    coordinate_frame: dict
    analysis_domain: dict
    buildings: list[Building]
    sources: list[Source]
    receptors: list[Receptor]
    diagnostics: dict
    evidence_kind: Literal["illustrative"]
    source_rights: Rights
    source_label: str
    operations: list[dict]


class FieldSlice(Output):
    schema_version: Literal["fields-v1"]
    extent: list[float] = Field(min_length=4,max_length=4)
    nx: int = Field(ge=1)
    ny: int = Field(ge=1)
    height_m: float
    values: list[float | None]
    mask: list[bool]
    units: Literal["ug/m3"]


class ReceptorMetric(Output):
    receptor_id: str
    name: str
    value_ug_m3: float | None
    valid: bool
    reason: str | None


class Result(Output):
    field: FieldSlice
    metrics: list[ReceptorMetric]
    diagnostics: dict
    evidence_kind: Literal["simulated"]
    qualification: Literal["research_preview"]
    limitations: list[str]


class RunManifest(Output):
    schema_version: Literal["phase-a-run-v1"]
    species: Literal["passive_tracer"]
    tier: Literal["T1"]
    model_version: str
    science_build_hash: str
    science_source_hashes: dict[str,str]
    dependency_versions: dict[str,str]
    conditions: Conditions
    scenario_id: str
    scenario_hash: str
    inputs: Scenario
    conditions_hash: str
    rights: Rights
    evidence_kind: Literal["simulated"]
    qualification: Literal["research_preview"]
    resource_allowance_seconds: float
    limitations: list[str]


class Run(Output):
    id: str
    project_id: str
    scenario_id: str
    status: Literal["queued","preparing","solving","verifying","complete_unqualified","failed","cancelled"]
    progress: float = Field(ge=0,le=1)
    stage: str
    created_at: str
    manifest: RunManifest
    result: Result | None
    error: dict | None
    cancel_requested: bool
    attempt: int = Field(ge=1)


class RunSubmission(Output):
    runs: list[Run]


class ImportResponse(Output):
    id: str
    scenario: Scenario
    audit: dict


class Quote(Output):
    id: str
    project_id: str
    scenario_ids: list[str]
    conditions: Conditions
    tier: Literal["T1"]
    cases: int
    maximum_seconds: float
    expires_at: str
    limitations: list[str]


class DeltaMetric(Output):
    receptor_id: str
    name: str
    baseline_ug_m3: float | None
    alternative_ug_m3: float | None
    delta_ug_m3: float | None
    percent_change: float | None
    percent_suppression_reason: str | None
    protected: bool
    valid: bool
    reason: str | None


class Comparison(Output):
    baseline_run_id: str
    alternative_run_id: str
    metrics: list[DeltaMetric]
    species: Literal["passive_tracer"]
    units: Literal["ug/m3"]
    qualification: Literal["research_preview"]
    decision_adequacy: Literal["not_established"]
    limitations: list[str]


class Report(Output):
    id: str
    project_id: str
    title: str
    status: Literal["draft"]
    created_at: str
    baseline_run_id: str
    alternative_run_id: str
    comparison: Comparison
    manifest: dict
    limitations: list[str]
    checksum: str


class ProjectDetail(Output):
    project: Project
    scenarios: list[Scenario]
    runs: list[Run]
    imports: list[dict]
    reports: list[Report]
