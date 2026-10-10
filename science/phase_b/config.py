"""Strict, explicit neutral stationary RANS preparation inputs."""
import math
from typing import Annotated
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def finite_number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError("Supply a finite physical number, never bool/string/null")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Physical number cannot exceed finite floating point bounds") from None
    if not math.isfinite(result):
        raise ValueError("Supply a finite physical number, never bool/string/null")
    return result


Number = Annotated[float,BeforeValidator(finite_number)]


class CaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid",validate_default=True,allow_inf_nan=False)
    wind_from_deg: Number = Field(ge=0,lt=360)
    reference_speed_m_s: Number = Field(ge=0.5,le=20)
    reference_height_m: Number = Field(ge=2,le=100)
    roughness_length_m: Number = Field(ge=0.001,le=2)
    kinematic_viscosity_m2_s: Number = Field(ge=1e-6,le=1e-3)
    molecular_diffusivity_m2_s: Number = Field(ge=0,le=1e-2)
    turbulent_schmidt_number: Number = Field(ge=0.1,le=2)
    background_ug_m3: Number = Field(ge=0,le=10000)
    base_cell_size_m: Number = Field(default=8,ge=0.5,le=20)
    surface_refinement_level: int = Field(default=1,strict=True,ge=0,le=3)
    source_box_half_width_m: Number = Field(default=4,ge=0.25,le=20)
    upstream_h: Number = Field(default=5,ge=5,le=20)
    downstream_h: Number = Field(default=15,ge=15,le=40)
    lateral_h: Number = Field(default=5,ge=5,le=20)
    top_h: Number = Field(default=5,ge=5,le=20)
    max_cells: int = Field(default=2_000_000,strict=True,ge=1000,le=10_000_000)
    flow_iterations: int = Field(default=1000,strict=True,ge=100,le=10000)
    residual_tolerance: Number = Field(default=1e-5,ge=1e-8,le=1e-3)
    scalar_residual_tolerance: Number = Field(default=1e-5,ge=1e-8,le=1e-3)
    max_wall_seconds: int = Field(default=3600,strict=True,ge=60,le=86400)

    @model_validator(mode="after")
    def compatible_inputs(self):
        if self.reference_height_m <= 20*self.roughness_length_m:
            raise ValueError("Reference height must exceed 20 roughness lengths for this neutral preparation envelope")
        if self.source_box_half_width_m < self.base_cell_size_m/2:
            raise ValueError("Source support must cover at least one base-cell width; mesh cell-volume verification is still required")
        return self
