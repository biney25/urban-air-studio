"""Reviewed small-site preparation from immutable map context, without qualification."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, model_validator
from api.schemas import Input, Number, Identifier, Name
from api.responses import Output, Scenario, Project

Hash = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
Reference = Annotated[str, Field(min_length=8, max_length=500)]


class HeightReview(Input):
    object_id: Identifier
    height_m: Annotated[Number, Field(ge=1, le=80)]
    height_basis: Literal["assumed", "osm_tag", "user_declared_evidence"]
    reference: Reference
    units: Literal["m_AGL"] = "m_AGL"


class ObjectExclusion(Input):
    object_id: Identifier
    reason: Reference


class LocalLicenceReview(Input):
    review_id: Literal["osm-odbl-local-study-v1"] = "osm-odbl-local-study-v1"
    accept_attribution: StrictBool
    accept_local_only: StrictBool
    assumptions_owned_or_permitted: StrictBool


class StudyPoint(Input):
    id: Identifier
    name: Name
    longitude: Annotated[Number, Field(ge=-180, le=180)]
    latitude: Annotated[Number, Field(ge=-80, le=80)]
    z_m: Annotated[Number, Field(ge=0, le=100)]
    basis: Literal["assumed", "user_declared_evidence"]
    reference: Reference
    units: Literal["m_AGL"] = "m_AGL"


class StudySource(StudyPoint):
    rate_g_s: Annotated[Number, Field(ge=0, le=100)]
    rate_units: Literal["g/s"] = "g/s"


class StudyReceptor(StudyPoint):
    protected: StrictBool = False


class StudyInputs(Input):
    mode: Literal["geometry_only", "illustrative_point_study", "user_supplied"] = "geometry_only"
    acknowledge_illustrative: StrictBool = False
    sources: list[StudySource] = Field(default_factory=list, max_length=100)
    receptors: list[StudyReceptor] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def explicit_support(self):
        if self.mode == "illustrative_point_study" and not self.acknowledge_illustrative:
            raise ValueError("Explicitly acknowledge the artificial point source/assessment trial")
        if self.mode != "user_supplied" and (self.sources or self.receptors):
            raise ValueError("Choose user_supplied for explicit source/assessment inputs")
        if self.mode == "user_supplied" and (not self.sources or not self.receptors):
            raise ValueError("User-supplied studies require at least one source and one assessment point")
        return self


class SitePreparationRequest(Input):
    context_id: Identifier
    expected_context_hash: Hash
    heights: list[HeightReview] = Field(default_factory=list, max_length=150)
    exclusions: list[ObjectExclusion] = Field(default_factory=list, max_length=200)
    acknowledge_flat_ground: StrictBool
    licence_review: LocalLicenceReview
    study_inputs: StudyInputs = Field(default_factory=StudyInputs)


class SitePreparationCommit(SitePreparationRequest):
    expected_preparation_hash: Hash


class SitePreparationContext(Output):
    schema_version: Literal["site-preparation-context-v1"]
    project_id: str
    context_id: str
    context_hash: str
    source_snapshot: dict
    buildings: list[dict]
    unsupported_objects: list[dict]
    context_only_road_count: int
    licence_review: dict
    illustrative_study_template: dict
    limitations: list[str]


class PreparationIssue(Output):
    code: str
    message: str
    object_id: str | None = None


class SitePreparationPreview(Output):
    schema_version: Literal["site-preparation-preview-v1"]
    context_id: str
    context_hash: str
    preparation_hash: str
    ready_to_commit: bool
    issues: list[PreparationIssue]
    warnings: list[str]
    proposed_scenario: dict | None
    audit: dict | None
    source_review: dict
    compliance_bundle: dict
    qualified: Literal[False] = False


class PreparedSite(Output):
    schema_version: Literal["prepared-site-v1"]
    preparation: dict
    scenario: Scenario
    project: Project
