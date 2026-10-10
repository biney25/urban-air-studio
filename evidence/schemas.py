"""Strict, bounded evidence inputs; units and observation support are explicit."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, field_validator, model_validator
from api.schemas import Input, Number, Rights, Name, Identifier

Species = Literal['passive_tracer', 'PM2.5', 'PM10', 'NO', 'NO2', 'CO', 'O3']
Fraction = Annotated[Number, Field(ge=0, le=1)]
Text = Annotated[str, Field(min_length=1, max_length=1000)]


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO 8601 string with an explicit timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid ISO 8601 timestamp') from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('An explicit timezone is required')
    return parsed


class Interval(Input):
    start: Annotated[str, Field(max_length=40)]
    end: Annotated[str, Field(max_length=40)]

    @model_validator(mode='after')
    def ordered(self):
        if timestamp(self.end) <= timestamp(self.start):
            raise ValueError('Interval end must follow its start')
        return self


class FleetItem(Input):
    category: Name
    vehicles_per_hour: Annotated[Number, Field(ge=0, le=100000)]
    factor_g_per_vehicle_km: Annotated[Number, Field(ge=0, le=1000)]
    factor_source: Text
    count_uncertainty_fraction: Fraction
    factor_uncertainty_fraction: Fraction


class RoadSegment(Input):
    id: Identifier
    length_m: Annotated[Number, Field(gt=0, le=100000)]
    fleet: Annotated[list[FleetItem], Field(min_length=1, max_length=30)]

    @model_validator(mode='after')
    def unique(self):
        categories = [item.category for item in self.fleet]
        if len(categories) != len(set(categories)):
            raise ValueError('Fleet categories must not be counted twice in a segment')
        return self


class Background(Input):
    mode: Literal['independent_inflow', 'coarse_total_baseline_adjustment']
    value_ug_m3: Annotated[Number, Field(ge=0, le=100000)]
    includes_local_emissions: StrictBool
    local_baseline_ug_m3: Annotated[Number | None, Field(ge=0, le=100000)] = None

    @model_validator(mode='after')
    def no_double_counting(self):
        if self.mode == 'independent_inflow' and self.includes_local_emissions:
            raise ValueError('Independent inflow cannot already include the same local emissions')
        if self.mode == 'independent_inflow' and self.local_baseline_ug_m3 is not None:
            raise ValueError('A local-baseline adjustment does not belong to independent inflow')
        if self.mode == 'coarse_total_baseline_adjustment' and (not self.includes_local_emissions or self.local_baseline_ug_m3 is None):
            raise ValueError('Coarse-total adjustment needs its included local baseline; do not add the full local source twice')
        if self.local_baseline_ug_m3 is not None and self.local_baseline_ug_m3 > self.value_ug_m3:
            raise ValueError('The declared local baseline exceeds the coarse total; retain and resolve this inconsistency')
        return self


class Inventory(Input):
    species: Species
    interval_start: Annotated[str, Field(max_length=40)]
    interval_end: Annotated[str, Field(max_length=40)]
    segments: Annotated[list[RoadSegment], Field(min_length=1, max_length=200)]
    background: Background
    uncertainty_note: Text

    @model_validator(mode='after')
    def consistent(self):
        Interval(start=self.interval_start, end=self.interval_end)
        ids = [segment.id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError('Road segment IDs must be unique')
        return self


class Instrument(Input):
    id: Identifier
    upstream_id: Identifier
    location_id: Identifier
    species: Species
    longitude: Annotated[Number, Field(ge=-180, le=180)]
    latitude: Annotated[Number, Field(ge=-90, le=90)]
    inlet_height_m: Annotated[Number, Field(ge=0, le=200)]
    serial: Name
    configuration: Text
    calibration_version: Text | None
    calibration_uncertainty_ug_m3: Annotated[Number | None, Field(ge=0, le=100000)]
    collocation_record: Text | None
    maintenance_note: Text
    clock_offset_seconds: Annotated[Number, Field(ge=-86400, le=86400)]


class Observation(Input):
    id: Identifier
    instrument_id: Identifier
    interval_start: Annotated[str, Field(max_length=40)]
    interval_end: Annotated[str, Field(max_length=40)]
    raw_value: Annotated[Number | None, Field(ge=-1000000, le=1000000)]
    raw_unit: Literal['ug/m3', 'mg/m3', 'ppb', 'ppm']
    provider_qa: Literal['accepted', 'flagged', 'rejected', 'missing']
    temperature_k: Annotated[Number | None, Field(ge=150, le=400)] = None
    pressure_pa: Annotated[Number | None, Field(ge=20000, le=120000)] = None
    relative_humidity_percent: Annotated[Number | None, Field(ge=0, le=100)] = None

    @model_validator(mode='after')
    def interval(self):
        Interval(start=self.interval_start, end=self.interval_end)
        return self


class MeasurementCampaign(Input):
    instruments: Annotated[list[Instrument], Field(min_length=1, max_length=100)]
    observations: Annotated[list[Observation], Field(min_length=1, max_length=5000)]
    purpose: Text

    @model_validator(mode='after')
    def bindings(self):
        instrument_ids = [instrument.id for instrument in self.instruments]
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError('Instrument IDs must be unique')
        ids = [row.id for row in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError('Observation IDs must be unique')
        if any(row.instrument_id not in instrument_ids for row in self.observations):
            raise ValueError('Every observation must resolve to its instrument metadata')
        # One upstream physical instrument cannot be assigned conflicting sites/species.
        sources = {}
        locations = {}
        for instrument in self.instruments:
            position = (instrument.longitude, instrument.latitude, instrument.inlet_height_m)
            signature = (instrument.location_id, instrument.species, position)
            if instrument.upstream_id in sources and sources[instrument.upstream_id] != signature:
                raise ValueError('An upstream instrument has conflicting physical metadata')
            sources[instrument.upstream_id] = signature
            if instrument.location_id in locations and locations[instrument.location_id] != position:
                raise ValueError('A location ID has conflicting coordinates/heights')
            locations[instrument.location_id] = position
        return self


class ReplayProtocol(Input):
    campaign_evidence_id: Identifier
    calibration_locations: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    evaluation_locations: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    calibration_blocks: Annotated[list[Interval], Field(min_length=1, max_length=50)]
    evaluation_blocks: Annotated[list[Interval], Field(min_length=1, max_length=50)]
    wind_nrmse_limit: Annotated[Number, Field(gt=0, le=10)]
    concentration_fac2_min: Annotated[Number, Field(gt=0, le=1)]
    concentration_floor_ug_m3: Annotated[Number, Field(gt=0, le=1000)]
    uncertainty_note: Text

    @model_validator(mode='after')
    def no_leakage(self):
        for ids in (self.calibration_locations, self.evaluation_locations):
            if len(ids) != len(set(ids)):
                raise ValueError('Location partitions must not repeat an ID')
        if set(self.calibration_locations) & set(self.evaluation_locations):
            raise ValueError('Reserve whole evaluation locations, separate from calibration')
        labelled = [(block, label) for label, blocks in [('calibration',self.calibration_blocks), ('evaluation',self.evaluation_blocks)] for block in blocks]
        for index, (left, _) in enumerate(labelled):
            for right, _ in labelled[index+1:]:
                if max(timestamp(left.start), timestamp(right.start)) < min(timestamp(left.end), timestamp(right.end)):
                    raise ValueError('Time blocks must be disjoint, including calibration and evaluation')
        return self


class Prediction(Input):
    observation_id: Identifier
    interval_start: Annotated[str, Field(max_length=40)]
    interval_end: Annotated[str, Field(max_length=40)]
    value_ug_m3: Annotated[Number, Field(ge=0, le=1000000)]

    @model_validator(mode='after')
    def interval(self):
        Interval(start=self.interval_start, end=self.interval_end)
        return self


class ReplayAssessment(Input):
    protocol_evidence_id: Identifier
    method_id: Name
    method_build_hash: Annotated[str, Field(pattern=r'^sha256:[0-9a-f]{64}$')]
    species: Species
    units: Literal['ug/m3']
    time_support: Literal['interval_mean']
    predictions: Annotated[list[Prediction], Field(max_length=5000)]

    @model_validator(mode='after')
    def identifiers(self):
        ids = [row.observation_id for row in self.predictions]
        if len(ids) != len(set(ids)):
            raise ValueError('Every held-out prediction ID must occur once')
        return self


class EvidenceRequest(Input):
    title: Name
    kind: Literal['inventory', 'measurement_campaign', 'replay_protocol', 'replay_assessment']
    evidence_kind: Literal['observed', 'provider-modelled', 'simulated', 'illustrative']
    source_label: Text
    rights: Rights
    payload: dict

    @model_validator(mode='after')
    def payload_schema(self):
        model = {'inventory':Inventory, 'measurement_campaign':MeasurementCampaign, 'replay_protocol':ReplayProtocol,
                 'replay_assessment':ReplayAssessment}[self.kind]
        if self.kind == 'replay_assessment' and self.evidence_kind not in {'simulated', 'illustrative'}:
            raise ValueError('A scored model-prediction assessment must be labelled simulated or illustrative, never observed')
        self.payload = model.model_validate(self.payload).model_dump(mode='json')
        return self
