"""Explicit WGS84 -> local metre import, immutable audit, and T0 geometry metrics."""

from __future__ import annotations

import math
from copy import deepcopy

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon, shape
from shapely.validation import explain_validity

from .errors import ScienceError

MAX_SITE_SPAN_M = 1500.0
MAX_BUILDINGS = 150
MAX_VERTICES = 10000


def _number(value, field: str) -> float:
    if isinstance(value, bool):
        raise ScienceError("INPUT_UNIT_UNKNOWN", f"{field} must be a finite number.")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ScienceError("INPUT_UNIT_UNKNOWN", f"{field} must be a finite number.") from None
    if not math.isfinite(result):
        raise ScienceError("INPUT_UNIT_UNKNOWN", f"{field} must be a finite number.")
    return result


def _fail(code: str, message: str, object_id=None) -> None:
    raise ScienceError(code, message, [{"object_id": object_id, "message": message}])


def coordinate_transform(longitude: float, latitude: float) -> tuple[Transformer, Transformer, dict]:
    """Local AEQD, centre true north/east; only a small-site metric frame is supported."""
    longitude, latitude = _number(longitude, "longitude"), _number(latitude, "latitude")
    if not (-180 <= longitude <= 180 and -80 <= latitude <= 80):
        _fail("CRS_UNRESOLVED", "Local small-site imports support longitude ±180 and latitude ±80 degrees.")
    local = CRS.from_proj4(
        f"+proj=aeqd +lat_0={latitude:.12f} +lon_0={longitude:.12f} +datum=WGS84 +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", local, always_xy=True)
    inverse = Transformer.from_crs(local, "EPSG:4326", always_xy=True)
    metadata = {
        "kind": "local_azimuthal_equidistant",
        "horizontal_crs_wkt": local.to_wkt(),
        "source_crs": "EPSG:4326",
        "source_axis_order": "longitude_latitude",
        "axis_order": "x_east_y_north",
        "unit": "m",
        "vertical_reference": "flat_ground_z0; heights_metres_AGL",
        "origin": {"longitude": longitude, "latitude": latitude},
        "north_rotation_deg": 0,
        "max_supported_site_span_m": MAX_SITE_SPAN_M,
        "limitations": ["Local projected frame; not a survey or resolved terrain model."],
    }
    return forward, inverse, metadata


def import_geojson(geojson: dict, rights: dict | None = None, source_label: str = "") -> tuple[dict, dict]:
    """Return scenario core + audit. Never repair, guess units, or fetch remote content."""
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        _fail("GEOMETRY_INVALID", "Upload must be a WGS84 GeoJSON FeatureCollection.")
    if geojson.get("crs") is not None:
        _fail("CRS_UNRESOLVED", "GeoJSON import uses explicit WGS84 longitude/latitude; legacy CRS is unsupported.")
    if not rights or any(rights.get(purpose) is not True for purpose in ("derive", "solve", "export")):
        _fail("LICENCE_USE_BLOCKED", "Explicit derive, solve and export permission declarations are required.")
    features = geojson.get("features")
    if not isinstance(features, list) or not features or len(features) > 500:
        _fail("IMPORT_LIMIT_EXCEEDED", "Supply between 1 and 500 supported building/source/receptor features.")
    records, coordinates, ids = [], [], set()
    for i, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            _fail("GEOMETRY_INVALID", f"Feature {i} is invalid.")
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            _fail("GEOMETRY_INVALID", f"Feature {i} properties must be an object.")
        kind = properties.get("kind") or properties.get("feature_type")
        object_id = str(properties.get("id") or feature.get("id") or f"{kind}_{i}")
        if not object_id or object_id in ids:
            _fail("GEOMETRY_INVALID", "Object IDs must be unique and non-empty.", object_id)
        ids.add(object_id)
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            _fail("GEOMETRY_INVALID", "Feature geometry is required.", object_id)
        try:
            parsed = shape(geometry)
        except Exception as exc:
            _fail("GEOMETRY_INVALID", f"Cannot parse geometry: {type(exc).__name__}.", object_id)
        if parsed.is_empty:
            _fail("GEOMETRY_INVALID", "Feature geometry must not be empty.", object_id)
        if kind == "building" and parsed.geom_type == "Polygon":
            if parsed.interiors:
                _fail("PROCESS_UNSUPPORTED", "Courtyard holes require a later supported geometry import; no silent filling.", object_id)
            if not parsed.is_valid or parsed.is_empty:
                _fail("GEOMETRY_INVALID", explain_validity(parsed), object_id)
            points = list(parsed.exterior.coords)
            if any(len(p) != 2 for p in points):
                _fail("CRS_UNRESOLVED", "Supply 2D footprint coordinates and explicit height_m.", object_id)
        elif kind in ("source", "receptor") and parsed.geom_type == "Point":
            points = [list(parsed.coords)[0]]
            if len(points[0]) != 2:
                _fail("CRS_UNRESOLVED", "Point elevation is explicit properties.z in metres AGL.", object_id)
        else:
            _fail("PROCESS_UNSUPPORTED", "Supported features are Polygon buildings and Point sources/receptors with properties.kind.", object_id)
        for point in points:
            lon, lat = (_number(point[0], "longitude"), _number(point[1], "latitude"))
            if not (-180 <= lon <= 180 and -80 <= lat <= 80):
                _fail("CRS_UNRESOLVED", "Coordinates must be longitude/latitude degrees, latitude within ±80.", object_id)
            coordinates.append((lon, lat))
        records.append((kind, object_id, properties, points))
    if len(coordinates) > MAX_VERTICES:
        _fail("IMPORT_LIMIT_EXCEEDED", "Too many geometry vertices.")
    longitudes, latitudes = zip(*coordinates)
    if max(longitudes) - min(longitudes) > 1 or max(latitudes) - min(latitudes) > 1:
        _fail("MODEL_OUT_OF_DOMAIN", "Import spans multiple degrees/dateline; split into a bounded site.")
    origin = {"longitude": (max(longitudes) + min(longitudes)) / 2, "latitude": (max(latitudes) + min(latitudes)) / 2}
    forward, inverse, frame = coordinate_transform(**origin)
    scenario = {"origin": origin, "coordinate_frame": frame, "buildings": [], "sources": [], "receptors": [],
                "evidence_kind": "illustrative", "source_label": source_label or "user-declared input; not measured",
                "source_rights": deepcopy(rights), "synthetic": bool(geojson.get("synthetic", False))}
    max_residual_m = 0.0
    for kind, object_id, props, points in records:
        local_points = []
        for lon, lat in points:
            x, y = forward.transform(lon, lat, errcheck=True)
            check_lon, check_lat = inverse.transform(x, y, errcheck=True)
            rx, ry = forward.transform(check_lon, check_lat, errcheck=True)
            max_residual_m = max(max_residual_m, math.hypot(rx - x, ry - y))
            local_points.append([x, y])
        name = str(props.get("name", object_id))[:200]
        if kind == "building":
            if props.get("height_unit", "m") != "m" or "height_m" not in props:
                _fail("INPUT_UNIT_UNKNOWN", "Building height_m is required in metres; no assumed height.", object_id)
            scenario["buildings"].append({"id": object_id, "name": name, "footprint": local_points[:-1], "height_m": _number(props["height_m"], "height_m")})
        else:
            if "z" not in props or props.get("height_unit", "m") != "m":
                _fail("INPUT_UNIT_UNKNOWN", "Point z is required in metres above flat ground.", object_id)
            record = {"id": object_id, "name": name, "x": local_points[0][0], "y": local_points[0][1], "z": _number(props["z"], "z")}
            if kind == "source":
                if "rate_g_s" not in props or props.get("rate_unit", "g/s") != "g/s":
                    _fail("SOURCE_RATE_MISSING", "Source rate_g_s in g/s is required.", object_id)
                record["rate_g_s"] = _number(props["rate_g_s"], "rate_g_s")
                scenario["sources"].append(record)
            else:
                if not isinstance(props.get("protected", False), bool):
                    _fail("GEOMETRY_INVALID", "protected must be boolean.", object_id)
                record["protected"] = props.get("protected", False)
                scenario["receptors"].append(record)
    audit = validate_scenario(scenario)
    xmin, ymin, xmax, ymax = audit["extent"]
    # Freeze a shared physical box once, so branch geometry edits do not also
    # move the comparison's outer boundaries or change its lateral grid.
    scenario["analysis_domain"] = {
        "xmin": math.floor((xmin - 64) / 8) * 8,
        "ymin": math.floor((ymin - 64) / 8) * 8,
        "xmax": math.ceil((xmax + 64) / 8) * 8,
        "ymax": math.ceil((ymax + 64) / 8) * 8,
        "zmin": 0.0, "zmax": 100.0,
        "cell_size_m": 8.0, "vertical_cell_size_m": 4.0,
    }
    if max_residual_m > 1e-4:
        _fail("CRS_UNRESOLVED", "Coordinate round-trip exceeded the 0.1 mm numerical gate.")
    audit.update({"roundtrip_max_residual_m": max_residual_m, "transform_verification": "numerical_roundtrip_not_survey_validation",
                  "source_label": scenario["source_label"], "evidence_kind": "illustrative"})
    scenario["diagnostics"] = audit
    return scenario, audit


def validate_scenario(scenario: dict) -> dict:
    if not isinstance(scenario, dict):
        _fail("GEOMETRY_INVALID", "Scenario must be an object.")
    origin = scenario.get("origin")
    if not isinstance(origin, dict):
        _fail("CRS_UNRESOLVED", "Scenario origin is required.")
    coordinate_transform(origin.get("longitude"), origin.get("latitude"))
    buildings = scenario.get("buildings", [])
    sources = scenario.get("sources", [])
    receptors = scenario.get("receptors", [])
    if any(not isinstance(v, list) for v in (buildings, sources, receptors)) or len(buildings) > MAX_BUILDINGS:
        _fail("IMPORT_LIMIT_EXCEEDED", "Scenario collections or building count exceed supported limits.")
    polygons, bounds, ids, height_volume = [], [], set(), 0.0
    for building in buildings:
        object_id = building.get("id")
        if not object_id or object_id in ids:
            _fail("GEOMETRY_INVALID", "IDs must be globally unique.", object_id)
        ids.add(object_id)
        footprint = building.get("footprint")
        if not isinstance(footprint, list) or not 3 <= len(footprint) <= 1000:
            _fail("GEOMETRY_INVALID", "Building footprint needs 3–1000 vertices.", object_id)
        points = [[_number(p[0], "x"), _number(p[1], "y")] for p in footprint if isinstance(p, (list, tuple)) and len(p) == 2]
        if len(points) != len(footprint):
            _fail("GEOMETRY_INVALID", "Footprint vertices must be [x,y] metre pairs.", object_id)
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.is_empty or polygon.area < 4:
            _fail("GEOMETRY_INVALID", f"Invalid or too-small footprint: {explain_validity(polygon)}.", object_id)
        height = _number(building.get("height_m"), "height_m")
        if not 1 <= height <= 80:
            _fail("MODEL_OUT_OF_DOMAIN", "Research preview building heights must be 1–80 m AGL.", object_id)
        for other_id, other in polygons:
            if polygon.intersection(other).area > 1e-6:
                _fail("GEOMETRY_INVALID", f"Building overlaps {other_id}.", object_id)
        polygons.append((object_id, polygon))
        bounds.extend(points)
        height_volume += height * polygon.area
    for kind, records in (("source", sources), ("receptor", receptors)):
        if len(records) > 200:
            _fail("IMPORT_LIMIT_EXCEEDED", f"At most 200 {kind}s are supported.")
        for item in records:
            object_id = item.get("id")
            if not object_id or object_id in ids:
                _fail("GEOMETRY_INVALID", "IDs must be globally unique.", object_id)
            ids.add(object_id)
            x, y, z = (_number(item.get(axis), axis) for axis in ("x", "y", "z"))
            if abs(x) > MAX_SITE_SPAN_M or abs(y) > MAX_SITE_SPAN_M or not 0 <= z <= 100:
                _fail("MODEL_OUT_OF_DOMAIN", "Point is outside the supported metre/site/height bounds.", object_id)
            point = Point(x, y)
            for building, (_, polygon) in zip(buildings, polygons):
                if polygon.covers(point) and z <= building["height_m"]:
                    _fail("SOURCE_IN_SOLID" if kind == "source" else "RECEPTOR_IN_SOLID", f"{kind.capitalize()} is inside/on a building solid.", object_id)
            if kind == "source" and not 0 <= _number(item.get("rate_g_s"), "rate_g_s") <= 100:
                _fail("SOURCE_RATE_MISSING", "Source rate must be 0–100 g/s.", object_id)
            bounds.append([x, y])
    if not bounds:
        _fail("GEOMETRY_INVALID", "At least one spatial object is needed.")
    xs, ys = zip(*bounds)
    extent = [min(xs), min(ys), max(xs), max(ys)]
    if max(extent[2] - extent[0], extent[3] - extent[1]) > MAX_SITE_SPAN_M:
        _fail("MODEL_OUT_OF_DOMAIN", "Study span exceeds 1.5 km; split into smaller sites.")
    domain = scenario.get("analysis_domain")
    if domain:
        if not all(k in domain for k in ("xmin", "ymin", "xmax", "ymax", "zmax")):
            _fail("GEOMETRY_INVALID", "Frozen analysis domain is incomplete.")
        if (extent[0] < domain["xmin"] + 16 or extent[1] < domain["ymin"] + 16 or
                extent[2] > domain["xmax"] - 16 or extent[3] > domain["ymax"] - 16):
            _fail("MODEL_OUT_OF_DOMAIN", "Edit approaches the frozen study boundary; create a larger-domain import/study.")
    footprint_area = sum(p.area for _, p in polygons)
    ground_area = max((extent[2] - extent[0]) * (extent[3] - extent[1]), 1.0)
    distances = [a.distance(b) for i, (_, a) in enumerate(polygons) for _, b in polygons[i + 1:]]
    return {"valid": True, "issues": [], "extent": extent, "building_count": len(buildings), "source_count": len(sources),
            "receptor_count": len(receptors), "building_footprint_area_m2": footprint_area, "building_volume_m3": height_volume,
            "plan_area_density": footprint_area / ground_area, "analysis_ground_area_m2": ground_area,
            "minimum_building_gap_m": min(distances) if distances else None,
            "source_mass_rate_g_s": sum(s["rate_g_s"] for s in sources), "tier": "T0",
            "meaning": "geometry/source diagnostics; no airflow or exposure prediction",
            "limitations": ["Flat ground", "No inferred floor area, road traffic, sky-view or health score."]}
