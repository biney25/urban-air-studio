"""Strict, bounded Phase A inputs. Units are part of the public contract."""
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, field_validator


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("A finite JSON number is required, not a boolean or string")
    return value


Number = Annotated[float, BeforeValidator(numeric)]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Identifier = Annotated[str, Field(min_length=1, max_length=100)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Named(Input):
    name: Name


class Rights(Input):
    derive: StrictBool
    solve: StrictBool
    export: StrictBool


class ImportRequest(Input):
    filename: Annotated[str, Field(min_length=1, max_length=200)]
    geojson: dict
    rights: Rights
    source_label: Annotated[str, Field(min_length=1, max_length=300)] = "User-supplied geometry; unverified"


class BaselineRequest(Input):
    scenario_id: Identifier


class BuildingChanges(Input):
    height_m: Annotated[Number | None, Field(gt=0, le=200)] = None
    dx: Annotated[Number | None, Field(ge=-500, le=500)] = None
    dy: Annotated[Number | None, Field(ge=-500, le=500)] = None
    rotation_deg: Annotated[Number | None, Field(ge=-360, le=360)] = None


class SourceChanges(Input):
    rate_g_s: Annotated[Number | None, Field(ge=0, le=100)] = None
    x: Annotated[Number | None, Field(ge=-1000, le=1000)] = None
    y: Annotated[Number | None, Field(ge=-1000, le=1000)] = None
    z: Annotated[Number | None, Field(ge=0, le=100)] = None


class ReceptorChanges(Input):
    x: Annotated[Number | None, Field(ge=-1000, le=1000)] = None
    y: Annotated[Number | None, Field(ge=-1000, le=1000)] = None
    z: Annotated[Number | None, Field(ge=0, le=100)] = None


class BuildingOperation(Input):
    type: Literal["building_update"]
    object_id: Identifier
    changes: BuildingChanges


class SourceOperation(Input):
    type: Literal["source_update"]
    object_id: Identifier
    changes: SourceChanges


class ReceptorOperation(Input):
    type: Literal["receptor_update"]
    object_id: Identifier
    changes: ReceptorChanges


Operation = Annotated[BuildingOperation | SourceOperation | ReceptorOperation, Field(discriminator="type")]


class OperationsRequest(Input):
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    operations: Annotated[list[Operation], Field(min_length=1, max_length=30)]


class Conditions(Input):
    wind_speed_m_s: Annotated[Number, Field(ge=0.5, le=10)]
    wind_from_deg: Annotated[Number, Field(ge=0, le=360)]
    diffusivity_m2_s: Annotated[Number, Field(ge=0.1, le=30)]
    background_ug_m3: Annotated[Number, Field(ge=0, le=5000)]
    duration_s: Annotated[Number, Field(ge=10, le=600)]

    @field_validator("wind_from_deg")
    @classmethod
    def normalize_direction(cls, value):
        return float(value) % 360


class QuoteRequest(Input):
    scenario_ids: Annotated[list[Identifier], Field(min_length=1, max_length=8)]
    conditions: Conditions
    tier: Literal["T1"] = "T1"

    @field_validator("scenario_ids")
    @classmethod
    def unique_scenarios(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Each scenario must occur once in a quote")
        return value


class RunRequest(Input):
    quote_id: Identifier
    idempotency_key: Annotated[str, Field(min_length=8, max_length=120)]


class ComparisonRequest(Input):
    baseline_run_id: Identifier
    alternative_run_id: Identifier


class ReportRequest(ComparisonRequest):
    title: Name = "Urban Air Studio — research preview comparison"
