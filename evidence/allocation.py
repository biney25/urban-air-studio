"""Checked conservative road-inventory allocation to finite source elements.

An explicit passive-tracer diagnostic approximation. No licence, measurement,
probability distribution, pollutant qualification or native mesh is inferred.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from shapely.geometry import LineString, Polygon, box

from api.schemas import Input, Number, Identifier
from evidence.schemas import Inventory, timestamp
from evidence.processing import inventory_summary
from science.errors import ScienceError
from science.phase_b.geometry import check_frame

ALLOCATION_BUILD_HASH = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=False, allow_nan=False).encode()).hexdigest()


Coordinate = Annotated[Number, Field(ge=-1500, le=1500)]
Point2 = Annotated[list[Coordinate], Field(min_length=2, max_length=2)]
Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class RoadGeometry(Input):
    segment_id: Identifier
    coordinates: Annotated[list[Point2], Field(min_length=2, max_length=1000)]

    @model_validator(mode="after")
    def valid_polyline(self):
        if any(left == right for left, right in zip(self.coordinates, self.coordinates[1:])):
            raise ValueError("A road cannot contain a zero-length consecutive edge")
        line = LineString(self.coordinates)
        if not line.is_valid or not line.is_simple or line.length <= 0:
            raise ValueError("Road geometry must be a finite non-self-intersecting positive-length polyline")
        return self


class AllocationGeometry(Input):
    unit: Literal["m"]
    coordinate_frame_hash: Hash
    roads: Annotated[list[RoadGeometry], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def unique_roads(self):
        ids = [item.segment_id for item in self.roads]
        if len(ids) != len(set(ids)):
            raise ValueError("Every road segment must be assigned once")
        if sum(len(item.coordinates) for item in self.roads) > 10000:
            raise ValueError("Road geometry exceeds the bounded10000-coordinate allowance")
        return self


class AllocationControls(Input):
    max_element_length_m: Annotated[Number, Field(ge=1, le=100)]
    source_height_m: Annotated[Number, Field(ge=.25, le=100)]
    max_source_elements: Annotated[int, Field(strict=True, ge=1, le=200)] = 200


def allocate_inventory(inventory_payload: dict, geometry_payload: dict,
                       scenario: dict, controls_payload: dict) -> dict:
    """Return deterministic source rows/ranges without mutating any input.

    The host binds inventory/scenario record hashes, intersected permissions,
    explicit manual-source replacement, and creates a NEW immutable scenario.
    Preparation/native execution must still verify finite box support and actual
    selected fluid volumes; this allocator never claims those checks passed.
    """
    inventory = Inventory.model_validate(inventory_payload)
    geometry = AllocationGeometry.model_validate(geometry_payload)
    controls = AllocationControls.model_validate(controls_payload)
    if inventory.species != "passive_tracer":
        raise ValueError("This allocation supports passive_tracer diagnostics only; PM/gas inventory needs an explicitly compatible species method")
    if inventory.background.mode != "independent_inflow":
        raise ValueError("Coarse-total background adjustment requires an independently approved paired baseline; use independent inflow for this method")
    try:
        check_frame(scenario)
    except (ScienceError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Allocation needs the audited scenario AEQD metre frame") from exc
    frame_hash = digest(scenario["coordinate_frame"])
    if geometry.coordinate_frame_hash != frame_hash:
        raise ValueError("Road coordinates refer to a different frozen scenario metre frame")
    segments = {item.id: item for item in inventory.segments}
    if set(segments) != {item.segment_id for item in geometry.roads}:
        raise ValueError("Road geometry must resolve every inventory segment exactly once, without omissions or additions")
    domain = scenario.get("analysis_domain")
    try:
        valid_domain = isinstance(domain, dict) and all(not isinstance(domain.get(key), bool)
            and isinstance(domain.get(key), (int, float)) and math.isfinite(float(domain[key]))
            for key in ("xmin", "ymin", "xmax", "ymax", "zmax"))
    except (ValueError, TypeError, OverflowError):
        valid_domain = False
    if not valid_domain or domain["xmin"] >= domain["xmax"] or domain["ymin"] >= domain["ymax"]:
        raise ValueError("Allocation needs the checked frozen study domain")
    if controls.source_height_m >= domain["zmax"]:
        raise ValueError("Road-source height must lie inside the frozen study domain")
    extent = box(domain["xmin"] + 16, domain["ymin"] + 16, domain["xmax"] - 16, domain["ymax"] - 16)
    if domain["xmax"] - domain["xmin"] <= 32 or domain["ymax"] - domain["ymin"] <= 32:
        raise ValueError("Frozen study domain has no supported interior")
    buildings = []
    for building in scenario.get("buildings", []):
        footprint = Polygon(building["footprint"])
        if not footprint.is_valid or footprint.area <= 0:
            raise ValueError("Allocation cannot use invalid building geometry")
        height = building.get("height_m")
        try:
            valid_height = not isinstance(height, bool) and isinstance(height, (int, float)) and math.isfinite(float(height)) and 0 < height <= 200
        except (TypeError, ValueError, OverflowError):
            valid_height = False
        if not valid_height:
            raise ValueError("Allocation needs a checked physical building height")
        buildings.append((footprint, height))
    summaries = {item["id"]: item for item in inventory_summary(inventory.model_dump())["segments"]}
    lines = []
    for road in sorted(geometry.roads, key=lambda item: item.segment_id):
        line = LineString(road.coordinates)
        if not extent.covers(line):
            raise ValueError("Road approaches or leaves the frozen study boundary; import a larger study")
        if any(height >= controls.source_height_m and footprint.intersects(line) for footprint, height in buildings):
            raise ValueError("Road emission geometry intersects a building at its declared height")
        if any(line.intersection(other).length > 1e-8 for other in lines):
            raise ValueError("Overlapping road supports would count the same physical road twice")
        lines.append(line)
        relative_length_error = abs(line.length - segments[road.segment_id].length_m) / segments[road.segment_id].length_m
        if relative_length_error > .001:
            raise ValueError("Road length differs from declared inventory length by more than the fixed0.1% support tolerance")
    required_count = sum(math.ceil(line.length / controls.max_element_length_m) for line in lines)
    if required_count > controls.max_source_elements:
        raise ValueError("Road discretisation exceeds the explicit source-element allowance; choose a declared coarser approximation")
    reserved_ids = {item["id"] for key in ("buildings", "receptors") for item in scenario.get(key, [])}
    sources, elements, road_records = [], [], []
    for road, line in zip(sorted(geometry.roads, key=lambda item: item.segment_id), lines):
        count = math.ceil(line.length / controls.max_element_length_m)
        summary = summaries[road.segment_id]
        target = [summary["rate_g_s"], *summary["range_g_s"]]
        allocated = [[], [], []]
        segment_elements = []
        token = hashlib.sha256(road.segment_id.encode()).hexdigest()[:16]
        for index in range(count):
            start, end = line.length * index / count, line.length * (index + 1) / count
            fraction = (end - start) / line.length
            point = line.interpolate((start + end) / 2)
            rates = [value - math.fsum(previous) if index == count - 1 else value * fraction
                     for value, previous in zip(target, allocated)]
            if any(value < 0 or not math.isfinite(value) for value in rates) or rates[0] > 100:
                raise ValueError("Allocated element rate exceeds the compatible passive source envelope")
            for value, previous in zip(rates, allocated):
                previous.append(value)
            identifier = f"traffic_{token}_{index:04d}"
            if identifier in reserved_ids:
                raise ValueError("Generated road-source identity collides with an existing object")
            reserved_ids.add(identifier)
            sources.append({"id": identifier, "name": f"Road {road.segment_id[:60]} · element{index + 1}",
                            "x": point.x, "y": point.y, "z": controls.source_height_m, "rate_g_s": rates[0]})
            element = {"source_id": identifier, "segment_id": road.segment_id,
                       "arc_start_m": start, "arc_end_m": end, "represented_length_m": end - start,
                       "fraction_of_road_length": fraction, "rate_g_s": rates[0], "rate_kg_s": rates[0] * .001,
                       "range_g_s": rates[1:], "range_basis": "declared_parameter_bounds_not_probability_or_confidence_interval"}
            elements.append(element)
            segment_elements.append(element)
        actual = [math.fsum(previous) for previous in allocated]
        errors = [abs(a - b) / max(abs(b), 1e-30) for a, b in zip(actual, target)]
        if any(value > 1e-12 for value in errors):
            raise ValueError("Conservative allocation failed floating-point mass readback")
        road_records.append({"segment_id": road.segment_id, "declared_length_m": segments[road.segment_id].length_m,
                             "geometry_length_m": line.length, "source_count": count,
                             "central_rate_g_s": actual[0], "range_g_s": actual[1:], "relative_mass_errors": errors})
    total = math.fsum(item["rate_g_s"] for item in sources)
    plan = {"schema_version": "phase-b-source-allocation-plan-v1", "qualified": False,
            "species": inventory.species, "source_semantics": "midpoint_finite_volumetric_elements_approximating_uniform_road_line_release",
            "coordinate_frame_hash": frame_hash, "inventory_payload_hash": digest(inventory.model_dump()),
            "geometry_hash": digest(geometry.model_dump()), "controls": controls.model_dump(),
            "allocation_build_hash": ALLOCATION_BUILD_HASH, "geometry": geometry.model_dump(),
            "interval_start_utc": timestamp(inventory.interval_start).astimezone(timezone.utc).isoformat(),
            "interval_end_utc": timestamp(inventory.interval_end).astimezone(timezone.utc).isoformat(),
            "background_mode": "independent_inflow", "background_ug_m3": inventory.background.value_ug_m3,
            "sources": sources, "source_set_hash": digest(sources), "elements": elements, "segments": road_records,
            "total_rate_g_s": total, "total_rate_kg_s": total * .001,
            "total_range_g_s": [math.fsum(item["range_g_s"][index] for item in road_records) for index in (0, 1)],
            "uncertainty_note": inventory.uncertainty_note,
            "limitations": ["Declared traffic counts, factors and ranges retain their source evidence class; arithmetic is not field calibration.",
                            "Explicitly replaces manual source rows only in a new child scenario; parent remains unchanged.",
                            "Road midpoint elements require source-width/resolution sensitivity and actual native fluid-volume checks.",
                            "Independent prescribed inflow background only; no local/coarse-total double counting or paired correction is inferred.",
                            "Rates are stationary representatives of the declared interval, not an activity schedule, measured interval mean or forecast.",
                            "No PM/gas chemistry, deposition, settling, resuspension, AQI or qualified environmental prediction is admitted."],
            "native_source_assignment_verified": False, "mass_allocation_arithmetic_verified": True}
    plan["content_hash"] = digest(plan)
    return deepcopy(plan)
