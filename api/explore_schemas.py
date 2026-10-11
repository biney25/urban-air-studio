"""Bounded map context contracts. Provider context never establishes science readiness."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from api.schemas import Identifier, Input, Number


class ExploreCenter(Input):
    longitude: Annotated[Number, Field(ge=-180, le=180)]
    latitude: Annotated[Number, Field(ge=-85, le=85)]


class ExploreBounds(Input):
    west: Annotated[Number, Field(ge=-180, le=180)]
    south: Annotated[Number, Field(ge=-85, le=85)]
    east: Annotated[Number, Field(ge=-180, le=180)]
    north: Annotated[Number, Field(ge=-85, le=85)]


class ExploreRequest(Input):
    center: ExploreCenter
    bounds: ExploreBounds
    include_geometry: StrictBool = True
    include_weather: StrictBool = True
    project_id: Identifier | None = None

    @model_validator(mode="after")
    def bounded_region(self):
        import math
        b, c = self.bounds, self.center
        if not (b.west < b.east and b.south < b.north):
            raise ValueError("Bounds must be ordered and cannot cross the antimeridian")
        if not (b.west <= c.longitude <= b.east and b.south <= c.latitude <= b.north):
            raise ValueError("The WGS84 center must lie inside the selected bounds")
        height = (b.north - b.south) * 111_320
        width = (b.east - b.west) * 111_320 * math.cos(math.radians(c.latitude))
        if height < 10 or width < 10 or height > 2_000.002 or width > 2_000.002 or height * width > 1_000_001:
            raise ValueError("Choose a small area: 10–2,000 m per side and at most 1 km²; no bulk regional requests")
        if not (self.include_geometry or self.include_weather):
            raise ValueError("Select geometry, weather, or both")
        return self


class ExploreOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProviderRights(ExploreOutput):
    display: bool
    retain_raw: bool
    retain_derived: bool
    derive_geometry: Literal[False] = False
    commercial_use: Literal[False] = False
    solver_input: Literal[False] = False
    export_data: Literal[False] = False
    train_model: Literal[False] = False
    share_public: Literal[False] = False
    export_image: Literal[False] = False
    use_scope: str


class ExploreProvider(ExploreOutput):
    id: str
    name: str
    category: Literal["geometry", "weather", "observations", "visual_context", "regional_background", "terrain"]
    status: Literal["available", "not_configured"]
    source_url: str
    licence: str
    attribution: str
    rights: ProviderRights
    limits: dict
    warnings: list[str]


class ExploreCatalogue(ExploreOutput):
    schema_version: Literal["explore-providers-v1"] = "explore-providers-v1"
    mode: Literal["local_noncommercial_evaluation"] = "local_noncommercial_evaluation"
    max_area_km2: Literal[1] = 1
    max_side_m: Literal[2000] = 2000
    providers: list[ExploreProvider]


class ExploreSnapshot(ExploreOutput):
    id: str
    provider_id: str
    fetched_at: str | None
    source_url: str
    licence: str
    attribution: str
    rights: ProviderRights
    status: Literal["available", "no_coverage", "not_configured", "not_requested", "rate_limited", "timeout", "provider_error", "invalid_response", "stale"]
    cached: bool
    raw_sha256: str | None
    raw_bytes: int
    request_hash: str
    warnings: list[str]
    missing_fields: list[str]
    retry_after_seconds: int | None = None
    metadata: dict


class ExploreGeometry(ExploreOutput):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[dict]
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    axis_order: Literal["longitude,latitude"] = "longitude,latitude"
    building_count: int
    road_count: int
    missing_height_count: int
    excluded_relation_count: int
    invalid_object_count: int
    qualification: Literal["unverified_provider_geometry"] = "unverified_provider_geometry"


class ExploreWeather(ExploreOutput):
    evidence_kind: Literal["provider-modelled"] = "provider-modelled"
    qualification: Literal["context_only"] = "context_only"
    model: Literal["gfs_global"] = "gfs_global"
    model_publisher: Literal["NOAA"] = "NOAA"
    model_initialization_utc: None = None
    lead_time_seconds: None = None
    valid_at_utc: str
    temporal_support_seconds: int
    spatial_support: str
    requested_center: ExploreCenter
    returned_grid_center: ExploreCenter
    grid_elevation_m: float | None
    temperature_2m_c: float | None
    wind_speed_10m_m_s: float | None
    wind_from_10m_deg: float | None


class ExploreRegion(ExploreOutput):
    center: ExploreCenter
    bounds: ExploreBounds
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    axis_order: Literal["longitude,latitude"] = "longitude,latitude"
    approximate_area_km2: float


class ExploreReadiness(ExploreOutput):
    status: Literal["blocked"] = "blocked"
    qualified: Literal[False] = False
    blockers: list[str]


class ExploreContext(ExploreOutput):
    schema_version: Literal["explore-context-v1"] = "explore-context-v1"
    id: str
    project_id: str | None
    created_at: str
    content_hash: str
    region: ExploreRegion
    snapshots: list[ExploreSnapshot]
    geometry: ExploreGeometry | None
    weather: ExploreWeather | None
    scientific_readiness: ExploreReadiness
    warnings: list[str]
