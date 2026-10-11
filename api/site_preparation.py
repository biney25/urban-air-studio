"""Scoped OSM context -> explicitly reviewed small-site research baseline.

The original provider snapshot remains context-only. A separate, narrowly scoped
ODbL review grants local derivation/computation for exact supported objects and
declared assumptions. No terrain, traffic, height or scientific accuracy is inferred.
"""
import base64
from copy import deepcopy
import hashlib
import os

from fastapi import APIRouter, Depends, Response
from shapely.geometry import Point, Polygon

from api.explore import (ADAPTER_VERSION, CONTEXT_KIND, PROVIDERS, ProviderFailure,
                         _json, normalise_osm, provider_parameters)
from api.explore_schemas import ExploreContext, ExploreRequest, ProviderRights
from api.site_preparation_schemas import (PreparedSite, SitePreparationCommit,
                                         SitePreparationContext, SitePreparationPreview,
                                         SitePreparationRequest)
from api.storage import Store, canonical, digest, new_id, utc_now
from science.errors import ScienceError
from science.geometry import coordinate_transform, import_geojson, validate_scenario
from science.model import MODEL_VERSION, make_grid

PREPARATION_KIND = "site_preparation"
REVIEW_ID = "osm-odbl-local-study-v1"
LEGACY_METHOD_VERSION = "site-preparation-v1"
METHOD_VERSION = "site-preparation-v2-t1-support"
LICENCE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
ATTRIBUTION = "© OpenStreetMap contributors; ODbL 1.0"
REVIEW_POLICY = {
    "id": REVIEW_ID, "reviewed_at": "2026-10-11",
    "sources": [LICENCE_URL, COPYRIGHT_URL,
                "https://osmfoundation.org/wiki/Licence/Community_Guidelines/Produced_Work_-_Guideline"],
    "licence": "ODbL 1.0", "scope": "Private local research derivation and computation from the exact bounded OSM snapshot",
    "basis": "ODbL3.1 permits database adaptation/derivation;4.5(c) distinguishes internal use. Public produced work/derivative publication triggers4.3/4.4/4.6 attribution/share-alike/access obligations.",
    "public_export": "Blocked here; a separate rights-checking export must include the attributable machine-readable derivative/alteration bundle.",
    "provider_service": "Uses already retained permitted evaluation snapshot; no new Overpass or other provider request.",
    "qualification": "Licence/use review does not attest geometry accuracy, height evidence or scientific validity.",
}
REVIEW_POLICY_HASH = digest(REVIEW_POLICY)
LOCAL_RIGHTS = {"derive": True, "solve": True, "export": False}


def _issue(code, message, object_id=None):
    return {"code": code, "message": message, "object_id": object_id}


def _scoped_context(store, identifier, tenant, error, db=None):
    record = store.get(CONTEXT_KIND, identifier, tenant, db)
    if not record:
        error("NOT_FOUND", "Map snapshot not found in this tenant", 404)
    try:
        if record["id"] != identifier or digest({k: v for k, v in record.items() if k != "record_hash"}) != record["record_hash"]:
            raise ValueError("Context record changed")
        context = record["context"]
        ExploreContext.model_validate(context)
        if context["id"] != identifier or digest({k: v for k, v in context.items() if k != "content_hash"}) != context["content_hash"]:
            raise ValueError("Context content changed")
        snapshots = {s["id"]: s for s in context["snapshots"]}
        if len(snapshots) != len(context["snapshots"]) or len(record["raw_records"]) != len(snapshots):
            raise ValueError("Snapshot identity changed")
        body = ExploreRequest(center=context["region"]["center"], bounds=context["region"]["bounds"], include_geometry=True, include_weather=False)
        osm_raw = osm_snapshot = None
        raw_ids = set()
        for entry in record["raw_records"]:
            if entry["id"] in raw_ids:
                raise ValueError("Duplicate raw snapshot dependency")
            raw_ids.add(entry["id"])
            snapshot = snapshots[entry["id"]]
            provider = PROVIDERS[snapshot["provider_id"]]
            if (snapshot["rights"] != ProviderRights.model_validate(provider["rights"]).model_dump()
                    or snapshot["source_url"] != provider["source_url"]
                    or snapshot["licence"] != provider["licence"]
                    or snapshot["attribution"] != provider["attribution"]):
                raise ValueError("Original context-only provider rights/identity changed")
            raw = base64.b64decode(entry["raw_base64"], validate=True)
            if (len(raw) != snapshot["raw_bytes"] or (raw and not snapshot["raw_sha256"])
                    or (snapshot["raw_sha256"] and "sha256:" + hashlib.sha256(raw).hexdigest() != snapshot["raw_sha256"])):
                raise ValueError("Raw snapshot changed")
            if digest({"adapter_version": ADAPTER_VERSION, "provider": snapshot["provider_id"], "parameters": entry["parameters"]}) != snapshot["request_hash"]:
                raise ValueError("Snapshot request changed")
            if entry["parameters"] != provider_parameters(snapshot["provider_id"], body):
                raise ValueError("Snapshot provider selection changed")
            if snapshot["provider_id"] == "osm_overpass":
                if osm_raw is not None or snapshot["source_url"] != PROVIDERS["osm_overpass"]["source_url"] or snapshot["licence"] != "ODbL 1.0":
                    raise ValueError("OSM source/licence identity changed")
                osm_raw, osm_snapshot = raw, snapshot
        if not context["geometry"] or not osm_raw or osm_snapshot["status"] != "available":
            error("SITE_GEOMETRY_UNAVAILABLE", "Select a saved area with available independent OSM geometry", 422)
        normalized, _, _, _ = normalise_osm(_json(osm_raw), body.bounds)
        if normalized != context["geometry"]:
            raise ValueError("Semantic geometry differs from the raw source snapshot")
        return context, osm_snapshot, _json(osm_raw)
    except (ValueError, KeyError, TypeError, AttributeError, ProviderFailure):
        error("SITE_SNAPSHOT_INTEGRITY_ERROR", "Saved map/provider bytes or source identities failed verification", 503)


def _objects(context, raw):
    features = context["geometry"]["features"]
    buildings = {f["id"]: f for f in features if f["properties"]["kind"] == "building"}
    included = {f["id"] for f in features}
    unsupported, seen = [], set()
    for i, item in enumerate(raw["elements"]):
        if not isinstance(item, dict):
            unsupported.append({"object_id": f"quarantined:{i}", "reason": "Invalid provider record", "requires_exclusion": True})
            continue
        # Quarantined malformed identities must not expose arbitrary raw fields.
        kind, number = item.get("type"), item.get("id")
        if not isinstance(kind, str) or kind not in {"way", "relation"} or isinstance(number, bool) or not isinstance(number, int) or number <= 0 or len(str(number)) > 60:
            unsupported.append({"object_id": f"quarantined:{i}", "reason": "Invalid provider object identity", "requires_exclusion": True})
            continue
        identifier = f"osm:{kind}:{number}"
        if identifier in seen:
            unsupported.append({"object_id": f"duplicate:{i}:{identifier}", "reason": "Duplicate provider identity; source support must be reviewed", "requires_exclusion": True})
        elif identifier not in included:
            unsupported.append({"object_id": identifier, "reason": "Unresolved building relation/holes or quarantined geometry", "requires_exclusion": True})
        seen.add(identifier)
    return buildings, unsupported


def _source_identity(snapshot):
    return {k: deepcopy(snapshot[k]) for k in ("id", "provider_id", "source_url", "licence", "attribution", "fetched_at", "raw_sha256", "raw_bytes", "request_hash", "rights")}


def _template_points(context, buildings):
    """Explicit artificial stimulus/assessment template; never measured traffic."""
    bounds = context["region"]["bounds"]
    polygons = [Polygon(f["geometry"]["coordinates"][0]) for f in buildings]
    candidates = []
    # Points remain inside the selected navigation area. Reject physical solids;
    # no receptor is moved to a roof to conceal invalid placement.
    for fy, fx in ((.1,.1),(.9,.9),(.1,.9),(.9,.1),(.5,.1),(.5,.9),(.1,.5),(.9,.5),(.3,.3),(.7,.7),(.3,.7),(.7,.3)):
        lon = bounds["west"] + fx * (bounds["east"] - bounds["west"])
        lat = bounds["south"] + fy * (bounds["north"] - bounds["south"])
        point = Point(lon, lat)
        if not any(poly.covers(point) for poly in polygons):
            candidates.append((lon, lat))
    if len(candidates) < 4:
        return [], [], [_issue("ILLUSTRATIVE_POINTS_UNAVAILABLE", "No safe four-point trial template fits the selected area; choose explicit sources/assessment points or geometry only.")]
    source = {"id": "illustrative-source", "name": "Artificial tracer trial source — 1 g/s", "longitude": candidates[0][0], "latitude": candidates[0][1], "z_m": .5,
              "rate_g_s": 1.0, "basis": "assumed", "reference": "Explicit artificial point-stimulus template; not traffic, facility inventory or a measured source"}
    receptors = [{"id": f"illustrative-assessment-{i}", "name": f"Artificial trial assessment {i}", "longitude": coord[0], "latitude": coord[1], "z_m": 1.5,
                  "protected": i == 3, "basis": "assumed", "reference": "Explicit template evaluation location; no real sensitive facility or measured occupancy is inferred"}
                 for i, coord in enumerate(candidates[1:4], 1)]
    return [source], receptors, []


def _supported_template_points(context, building_core):
    """New artificial trials use actual fluid-cell support in a frozen frame.

    Physical point validity and containing raster-cell validity are distinct.
    The building-only importer freezes the frame/domain before point selection;
    adding the trial points must not silently move that numerical raster.
    """
    grid = make_grid(building_core)
    forward, inverse, _ = coordinate_transform(**building_core["origin"])
    bounds = context["region"]["bounds"]
    polygons = [Polygon(building["footprint"]) for building in building_core["buildings"]]
    candidates = []
    for iy, y in enumerate(grid.y):
        for ix, x in enumerate(grid.x):
            # Keep the existing immutable-domain geometry margin; selecting a
            # fluid boundary cell cannot bypass validate_scenario's 16 m gate.
            if not (grid.xmin + 16 <= x <= grid.xmax - 16 and grid.ymin + 16 <= y <= grid.ymax - 16):
                continue
            if not grid.fluid[0, iy, ix] or any(poly.covers(Point(float(x), float(y))) for poly in polygons):
                continue
            lon, lat = inverse.transform(float(x), float(y), errcheck=True)
            if bounds["west"] <= lon <= bounds["east"] and bounds["south"] <= lat <= bounds["north"]:
                candidates.append((float(x), float(y), float(lon), float(lat)))
    if len(candidates) < 4:
        return [], [], [_issue("ILLUSTRATIVE_POINTS_UNAVAILABLE", "Fewer than four supported T1 fluid cells fit the selected area; choose explicit valid study points or geometry only.")]
    selected = []
    for fy, fx in ((.1, .1), (.9, .9), (.1, .9), (.9, .1)):
        target_x, target_y = forward.transform(bounds["west"] + fx * (bounds["east"] - bounds["west"]),
                                                bounds["south"] + fy * (bounds["north"] - bounds["south"]), errcheck=True)
        nearest = min(candidates, key=lambda c: ((c[0] - target_x) ** 2 + (c[1] - target_y) ** 2, c[1], c[0]))
        candidates.remove(nearest)
        selected.append(nearest)
    source = {"id": "illustrative-source", "name": "Artificial tracer trial source — 1 g/s", "longitude": selected[0][2], "latitude": selected[0][3], "z_m": .5,
              "rate_g_s": 1.0, "basis": "assumed", "reference": "Explicit artificial point-stimulus template at supported T1 fluid-cell horizontal centre; not traffic, facility inventory or a measured source"}
    receptors = [{"id": f"illustrative-assessment-{i}", "name": f"Artificial trial assessment {i}", "longitude": coord[2], "latitude": coord[3], "z_m": 1.5,
                  "protected": i == 3, "basis": "assumed", "reference": "Explicit template location at supported T1 fluid-cell horizontal centre; no real sensitive facility or measured occupancy is inferred"}
                 for i, coord in enumerate(selected[1:], 1)]
    return [source], receptors, []


def _fixed_frame_trial(building_core, sources, receptors, source_label):
    core = deepcopy(building_core)
    forward, inverse, _ = coordinate_transform(**core["origin"])
    residual = core["diagnostics"]["roundtrip_max_residual_m"]
    for kind, points in (("sources", sources), ("receptors", receptors)):
        core[kind] = []
        for point in points:
            x, y = forward.transform(point["longitude"], point["latitude"], errcheck=True)
            lon, lat = inverse.transform(x, y, errcheck=True)
            rx, ry = forward.transform(lon, lat, errcheck=True)
            residual = max(residual, ((rx - x) ** 2 + (ry - y) ** 2) ** .5)
            row = {"id": point["id"], "name": point["name"], "x": x, "y": y, "z": point["z_m"]}
            row.update({"rate_g_s": point["rate_g_s"]} if kind == "sources" else {"protected": point["protected"]})
            core[kind].append(row)
    if residual > 1e-4:
        raise ScienceError("CRS_UNRESOLVED", "Coordinate round-trip exceeded the 0.1 mm numerical gate.")
    audit = validate_scenario(core)
    audit.update(roundtrip_max_residual_m=residual, transform_verification="numerical_roundtrip_not_survey_validation",
                 source_label=source_label, evidence_kind="illustrative",
                 coordinate_frame_basis="Reviewed building footprint bounds; artificial point selection preserves that metric frame and analysis domain")
    core["diagnostics"] = audit
    return core, audit


def _study_grid_support(core):
    grid = make_grid(core)
    points = []
    for kind in ("sources", "receptors"):
        for point in core[kind]:
            cell = grid.locate(point["x"], point["y"], point["z"])
            if cell is None or not grid.fluid[cell]:
                raise ScienceError("MESH_FAILED", "Declared study input has no supported T1 fluid cell.", [{"object_id": point["id"]}])
            iz, iy, ix = cell
            points.append({"object_id": point["id"], "kind": kind[:-1], "requested_xyz_m": [point["x"], point["y"], point["z"]],
                           "cell_index_zyx": list(cell), "native_cell_centre_xyz_m": [float(grid.x[ix]), float(grid.y[iy]), float(grid.z[iz])], "fluid": True})
    return {"schema_version": "t1-study-grid-support-v1", "model_version": MODEL_VERSION,
            "grid_shape_zyx": [grid.nz, grid.ny, grid.nx], "cell_size_xyz_m": [grid.dx, grid.dy, grid.dz],
            "analysis_domain": deepcopy(core["analysis_domain"]), "points": points,
            "qualification": "Numerical raster support only; no measurement, site accuracy or physical prediction qualification"}


def _point_feature(point, kind):
    properties = {"id": point["id"], "name": point["name"], "kind": kind, "z": point["z_m"], "height_unit": "m"}
    if kind == "source":
        properties.update(rate_g_s=point["rate_g_s"], rate_unit="g/s")
    else:
        properties["protected"] = point["protected"]
    return {"type": "Feature", "id": point["id"], "geometry": {"type": "Point", "coordinates": [point["longitude"], point["latitude"]]}, "properties": properties}


def _preview(context, snapshot, raw, body, method_version=METHOD_VERSION):
    issues, warnings = [], ["Research geometry/study only; no surveyed terrain, measured urban accuracy or environmental benefit is established."]
    buildings, unsupported = _objects(context, raw)
    reviews, exclusions = {}, {}
    for height in body.heights:
        if height.object_id in reviews:
            issues.append(_issue("DUPLICATE_HEIGHT_REVIEW", "Each building needs one unambiguous height review", height.object_id))
        reviews[height.object_id] = height.model_dump()
    for exclusion in body.exclusions:
        if exclusion.object_id in exclusions:
            issues.append(_issue("DUPLICATE_EXCLUSION", "Each excluded object needs one scoped reason", exclusion.object_id))
        exclusions[exclusion.object_id] = exclusion.model_dump()
    allowed_ids = set(buildings) | {u["object_id"] for u in unsupported}
    for identifier in set(exclusions) - allowed_ids:
        issues.append(_issue("UNKNOWN_EXCLUSION", "Exclusion must identify a building or unresolved object from this exact snapshot", identifier))
    for identifier in set(reviews) - set(buildings):
        issues.append(_issue("UNKNOWN_HEIGHT_OBJECT", "Height review is not linked to a supported building in this snapshot", identifier))
    for item in unsupported:
        if item["object_id"] not in exclusions:
            issues.append(_issue("UNRESOLVED_SOURCE_OBJECT", item["reason"] + "; explicitly bound and record its exclusion or choose another area", item["object_id"]))
    if not body.acknowledge_flat_ground:
        issues.append(_issue("VERTICAL_MODEL_UNREVIEWED", "Acknowledge the explicit flat-ground/m_AGL assumption; surveyed terrain is not available"))
    accepted = body.licence_review.accept_attribution and body.licence_review.accept_local_only and body.licence_review.assumptions_owned_or_permitted
    if not accepted:
        issues.append(_issue("LICENCE_USE_BLOCKED", "Accept attribution, private local-use scope and permission for supplied assumptions before preparing this asset"))
    granted = {"derive": bool(accepted), "solve": bool(accepted), "export": False}
    features, height_rows, originals = [], [], []
    for identifier, feature in buildings.items():
        if identifier in exclusions:
            if identifier in reviews:
                issues.append(_issue("EXCLUDED_HEIGHT_CONFLICT", "An excluded building cannot also receive a height repair", identifier))
            continue
        review = reviews.get(identifier)
        if review is None:
            issues.append(_issue("HEIGHT_REVIEW_REQUIRED", "Supply an object-linked height and explicit measured/evidenced or assumed basis; no default height is used", identifier))
            continue
        source_height = feature["properties"]["height_m"]
        if review["height_basis"] == "osm_tag" and (source_height is None or abs(review["height_m"] - source_height) > 1e-9):
            issues.append(_issue("HEIGHT_SOURCE_MISMATCH", "OSM-tag review must match the retained actual height tag; otherwise choose an explicit independent evidence/assumption", identifier))
        geometry = feature["geometry"]
        if geometry.get("type") != "Polygon" or len(geometry.get("coordinates", [])) != 1:
            issues.append(_issue("GEOMETRY_PROCESS_UNSUPPORTED", "Only simple Polygon footprints without holes are supported", identifier))
            continue
        feature_copy = {"type": "Feature", "id": identifier, "geometry": deepcopy(geometry), "properties": {
            "id": identifier, "name": str(feature["properties"]["tags"].get("name") or identifier)[:120], "kind": "building", "height_m": review["height_m"], "height_unit": "m"}}
        features.append(feature_copy)
        originals.append(deepcopy(feature))
        height_rows.append({**review, "source_height_tag_m": source_height, "source_url": feature["properties"]["source_url"],
                            "verification": "assumed" if review["height_basis"] == "assumed" else "user_reviewed_unverified"})
    if not features:
        issues.append(_issue("SITE_BUILDING_REQUIRED", "Select at least one supported reviewed building; this path does not silently substitute a fictional site"))
    if len(features) > 150:
        issues.append(_issue("SITE_BUILDING_LIMIT", "Select no more than150 buildings for this local small-site method"))
    if exclusions:
        warnings.append(f"{len(exclusions)} source objects are explicitly excluded; physical completeness is not claimed.")
    sources, receptors, building_core = [], [], None
    source_label = ATTRIBUTION + "; reviewed private research assumptions; unqualified inputs"
    if body.study_inputs.mode == "illustrative_point_study":
        if method_version == LEGACY_METHOD_VERSION:
            sources, receptors, point_issues = _template_points(context, originals)
            issues.extend(point_issues)
        elif not issues:
            try:
                building_core, _ = import_geojson({"type": "FeatureCollection", "features": features},
                                                 {"derive": True, "solve": True, "export": True}, source_label)
                sources, receptors, point_issues = _supported_template_points(context, building_core)
                issues.extend(point_issues)
            except ScienceError as exc:
                issues.append(_issue(exc.code, exc.message, exc.issues[0].get("object_id") if exc.issues else None))
        warnings.append("The trial source/assessment locations are artificial, explicitly selected inputs; they do not represent real traffic or population.")
    elif body.study_inputs.mode == "user_supplied":
        sources = [s.model_dump() for s in body.study_inputs.sources]
        receptors = [r.model_dump() for r in body.study_inputs.receptors]
    else:
        warnings.append("Geometry-only baseline: no emissions or assessment points were invented. Add declared study inputs before interpreting any preview.")
    bounds = context["region"]["bounds"]
    for kind, points in (("source", sources), ("receptor", receptors)):
        for point in points:
            if not (bounds["west"] <= point["longitude"] <= bounds["east"] and bounds["south"] <= point["latitude"] <= bounds["north"]):
                issues.append(_issue("STUDY_POINT_OUTSIDE_SELECTION", "Explicit study points must lie inside the selected navigation scope", point["id"]))
            features.append(_point_feature(point, kind))
    source_review = {"schema_version": "site-source-review-v1", "policy": deepcopy(REVIEW_POLICY), "policy_hash": REVIEW_POLICY_HASH,
                     "accepted": accepted, "declarations": body.licence_review.model_dump(), "rights": granted,
                     "context_id": context["id"], "context_hash": context["content_hash"], "source_snapshot": _source_identity(snapshot),
                     "reviewed_height_count": len(height_rows), "original_context_permissions_unchanged": True,
                     "scope": "This reviewed private research asset only; not a new right for all provider data"}
    source_review["content_hash"] = digest(source_review)
    geojson = {"type": "FeatureCollection", "features": features}
    core = audit = grid_support = None
    if not issues:
        try:
            # The old pure importer conflates parser admission with export.
            # ODbL permits internal adaptation; this temporary declaration is
            # ONLY geometry validation, never persisted as an export grant.
            if building_core is not None:
                core, audit = _fixed_frame_trial(building_core, sources, receptors, source_label)
            else:
                core, audit = import_geojson(geojson, {"derive": True, "solve": True, "export": True}, source_label)
            if method_version != LEGACY_METHOD_VERSION and body.study_inputs.mode != "geometry_only":
                grid_support = _study_grid_support(core)
            core["source_rights"] = deepcopy(granted)
            audit.update(source_rights=deepcopy(granted), source_review_hash=source_review["content_hash"],
                         height_assumptions=height_rows, source_assumptions=sources, receptor_assumptions=receptors,
                         excluded_objects=list(exclusions.values()), qualified=False,
                         scientific_status="research_preview_unqualified", geometry_accuracy="unverified", study_inputs_mode=body.study_inputs.mode)
            if grid_support is not None:
                audit["study_grid_support"] = deepcopy(grid_support)
            core["diagnostics"] = audit
        except ScienceError as exc:
            core = audit = None
            issues.append(_issue(exc.code, exc.message, (exc.issues[0].get("object_id") if exc.issues else None)))
        except (ValueError, TypeError, KeyError):
            issues.append(_issue("PREPARATION_INVALID", "Supported semantic geometry failed finite/unit/topology validation"))
    # No provider weather enters the scientific boundary. This bundle contains
    # only original selected OSM objects and explicit reviewed alterations.
    bundle = {"schema_version": "osm-local-study-compliance-v1", "licence": "ODbL 1.0", "licence_url": LICENCE_URL,
              "attribution": ATTRIBUTION, "copyright_url": COPYRIGHT_URL, "source_context_id": context["id"],
              "source_context_hash": context["content_hash"], "source_snapshot": _source_identity(snapshot),
              "selected_original_objects": originals, "height_alterations": height_rows, "exclusions": list(exclusions.values()),
              "declared_study_inputs": {"mode": body.study_inputs.mode, "sources": sources, "receptors": receptors},
              "semantic_geojson": geojson, "conversion_method": method_version,
              "metric_frame": core["coordinate_frame"] if core else None,
              "permissions": granted, "publication": "Not performed; public-use notices and machine-readable alterations must accompany a separately admitted export",
              "qualified": False}
    if method_version != LEGACY_METHOD_VERSION:
        bundle["study_grid_support"] = grid_support
    bundle["content_hash"] = digest(bundle)
    preparation_binding = {"method": method_version, "request": body.model_dump(), "context_hash": context["content_hash"],
                           "source_review_hash": source_review["content_hash"], "bundle_hash": bundle["content_hash"], "scenario": core}
    return {"schema_version": "site-preparation-preview-v1", "context_id": context["id"], "context_hash": context["content_hash"],
            "preparation_hash": digest(preparation_binding), "ready_to_commit": not issues,
            "issues": issues, "warnings": warnings, "proposed_scenario": core, "audit": audit,
            "source_review": source_review, "compliance_bundle": bundle, "qualified": False}


def checked_preparation_dependency(store, scenario, tenant, error, db=None):
    """Required downstream source authority; never trust browser metadata alone."""
    binding = scenario.get("source_preparation")
    if binding is None:
        return None
    if os.getenv("UA_MODE", "local") != "local":
        error("SITE_PREPARATION_LOCAL_ONLY", "This reviewed provider asset permits private local research use only", 409)
    try:
        record = store.get(PREPARATION_KIND, binding["id"], tenant, db)
        if not record or record["project_id"] != scenario["project_id"] or record["id"] != binding["id"]:
            raise ValueError("Scoped preparation missing")
        if record["schema_version"] != "prepared-site-record-v1" or record["qualified"] is not False or record["rights"] != LOCAL_RIGHTS:
            raise ValueError("Prepared asset scope changed")
        if digest({k:v for k,v in record.items() if k != "content_hash"}) != record["content_hash"] or record["content_hash"] != binding["content_hash"]:
            raise ValueError("Preparation bytes changed")
        review, bundle = record["source_review"], record["compliance_bundle"]
        if digest({k:v for k,v in review.items() if k != "content_hash"}) != review["content_hash"] or digest({k:v for k,v in bundle.items() if k != "content_hash"}) != bundle["content_hash"]:
            raise ValueError("Review/bundle changed")
        if (review["policy_hash"] != REVIEW_POLICY_HASH or review["policy"] != REVIEW_POLICY
                or review["content_hash"] != binding["source_review_hash"] or bundle["content_hash"] != binding["compliance_bundle_hash"]
                or review["rights"] != LOCAL_RIGHTS or review["rights"] != scenario["source_rights"] or review["accepted"] is not True):
            raise ValueError("Source grant changed")
        expected_binding = {"id": record["id"], "content_hash": record["content_hash"],
                            "source_review_hash": review["content_hash"], "compliance_bundle_hash": bundle["content_hash"],
                            "context_id": record["context_id"], "context_hash": record["context_hash"], "qualified": False}
        if binding != expected_binding:
            raise ValueError("Scenario source dependency changed")
        context, snapshot, raw = _scoped_context(store, record["context_id"], tenant, error, db)
        if context["content_hash"] != record["context_hash"] or context.get("project_id") not in {None, scenario["project_id"]}:
            raise ValueError("Original source dependency changed")
        request = SitePreparationRequest.model_validate(record["request"])
        if request.context_id != context["id"] or request.expected_context_hash != context["content_hash"]:
            raise ValueError("Reviewed input binding changed")
        # Rebuild from the retained source bytes and reviewed inputs rather than
        # treating a rehashed metadata dictionary as an authority to grant rights.
        method_version = bundle["conversion_method"]
        if method_version not in {LEGACY_METHOD_VERSION, METHOD_VERSION}:
            raise ValueError("Preparation method is not supported")
        replay = _preview(context, snapshot, raw, request, method_version)
        if (not replay["ready_to_commit"] or replay["preparation_hash"] != record["preparation_hash"]
                or replay["source_review"] != review or replay["compliance_bundle"] != bundle
                or replay["audit"] != record["audit"] or replay["warnings"] != record["warnings"]):
            raise ValueError("Reviewed derivation no longer matches its exact source/input bundle")
        # Immutable branches can change geometry deliberately; the source
        # declaration and original licence/assumption dependency remain fixed.
        return record
    except (ValueError, TypeError, KeyError, AttributeError):
        error("SITE_PREPARATION_INTEGRITY_ERROR", "Reviewed site/source dependency failed integrity or permission checks", 503)


def make_router(*, identity_dependency, store_dependency, required, project_resource, error, authorizer):
    router = APIRouter(tags=["Reviewed site preparation"])

    def context_for(store, project_id, identifier, identity, db=None):
        required(store, "project", project_id, identity, db)
        context, snapshot, raw = _scoped_context(store, identifier, identity.tenant_id, error, db)
        if context.get("project_id") and context["project_id"] != project_id:
            error("PROJECT_SCOPE_MISMATCH", "Map snapshot belongs to another project", 404)
        return context, snapshot, raw

    @router.get("/api/projects/{project_id}/site-preparations/context", response_model=SitePreparationContext)
    def preparation_context(project_id: str, context_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        context, snapshot, raw = context_for(store, project_id, context_id, identity)
        buildings, unsupported = _objects(context, raw)
        return {"schema_version": "site-preparation-context-v1", "project_id": project_id, "context_id": context_id,
                "context_hash": context["content_hash"], "source_snapshot": _source_identity(snapshot),
                "buildings": [{"object_id": identifier, "name": f["properties"]["tags"].get("name", identifier),
                               "height_m": f["properties"]["height_m"], "height_basis": f["properties"]["height_basis"],
                               "source_url": f["properties"]["source_url"], "footprint": f["geometry"]} for identifier, f in buildings.items()],
                "unsupported_objects": unsupported, "context_only_road_count": context["geometry"]["road_count"],
                "licence_review": {**deepcopy(REVIEW_POLICY), "content_hash": REVIEW_POLICY_HASH},
                "illustrative_study_template": {"source_count": 1, "source_rate_g_s": 1.0, "source_height_m_AGL": .5,
                                               "assessment_count": 3, "assessment_height_m_AGL": 1.5,
                                               "basis": "Explicit artificial tracer trial; no real traffic/emissions/occupancy inferred"},
                "limitations": ["Only simple Polygon footprints without holes and the existing 1.5 km / 150-building local method are supported.",
                                "Every selected building height needs an explicit review; exclusion reasons bound the limited study scope.",
                                "Weather remains separate context; surveyed terrain and physical accuracy are not qualified."]}

    @router.post("/api/projects/{project_id}/site-preparations/preview", response_model=SitePreparationPreview)
    def preparation_preview(project_id: str, body: SitePreparationRequest, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        context, snapshot, raw = context_for(store, project_id, body.context_id, identity)
        if context["content_hash"] != body.expected_context_hash:
            error("VERSION_CONFLICT", "Expected immutable map snapshot hash differs", 409)
        return _preview(context, snapshot, raw, body)

    @router.post("/api/projects/{project_id}/site-preparations", status_code=201, response_model=PreparedSite)
    def preparation_commit(project_id: str, body: SitePreparationCommit, response: Response, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        from api.main import scenario_version
        if os.getenv("UA_MODE", "local") != "local":
            error("SITE_PREPARATION_LOCAL_ONLY", "This reviewed provider pathway permits private local research use only", 409)
        base = SitePreparationRequest.model_validate(body.model_dump(exclude={"expected_preparation_hash"}))
        context, snapshot, raw = context_for(store, project_id, body.context_id, identity)
        if context["content_hash"] != body.expected_context_hash:
            error("VERSION_CONFLICT", "Expected immutable map snapshot hash differs", 409)
        preview = _preview(context, snapshot, raw, base)
        check = authorizer.check()
        review = preview["source_review"]
        check.require(identity, project_id, action="derive", identifier=review["content_hash"], tenant_id=identity.tenant_id,
                      resource_project_id=project_id, rights=review["rights"])
        if not preview["ready_to_commit"]:
            error("SITE_PREPARATION_BLOCKED", "Resolve the listed site inputs before freezing a baseline", 422, preview["issues"])
        if preview["preparation_hash"] != body.expected_preparation_hash:
            error("VERSION_CONFLICT", "Reviewed preparation changed; inspect it again before committing", 409)
        core = deepcopy(preview["proposed_scenario"])
        preparation = {"schema_version": "prepared-site-record-v1", "id": new_id("preparation"), "project_id": project_id,
                       "created_at": utc_now(), "context_id": context["id"], "context_hash": context["content_hash"],
                       "preparation_hash": preview["preparation_hash"], "reviewed_by": identity.user_id,
                       "request": base.model_dump(), "source_review": review, "compliance_bundle": preview["compliance_bundle"],
                       "audit": preview["audit"], "warnings": preview["warnings"], "qualified": False,
                       "rights": deepcopy(review["rights"]), "authorization": {"decision_id": check.decision_id, "decision": "Allow", **check.headers()}}
        preparation["content_hash"] = digest(preparation)
        core["source_preparation"] = {"id": preparation["id"], "content_hash": preparation["content_hash"],
                                     "source_review_hash": review["content_hash"], "compliance_bundle_hash": preview["compliance_bundle"]["content_hash"],
                                     "context_id": context["id"], "context_hash": context["content_hash"], "qualified": False}
        scenario = scenario_version(core, project_id, "Reviewed map baseline")
        imported = {"id": new_id("import"), "project_id": project_id, "filename": "reviewed-map-semantic.geojson",
                    "source_label": scenario["source_label"], "rights": deepcopy(review["rights"]), "created_at": utc_now(),
                    "scenario_id": scenario["id"], "audit": deepcopy(preview["audit"]),
                    "raw_checksum": digest(preview["compliance_bundle"]["semantic_geojson"]), "source_preparation": deepcopy(core["source_preparation"])}
        with store.transaction() as db:
            project = required(store, "project", project_id, identity, db)
            if project.get("baseline_scenario_id"):
                error("BASELINE_ALREADY_SET", "The existing baseline remains unchanged; prepare another independent project or branch its alternatives", 409)
            fresh, _, _ = context_for(store, project_id, body.context_id, identity, db)
            if fresh["content_hash"] != body.expected_context_hash:
                error("VERSION_CONFLICT", "Source snapshot changed during preparation", 409)
            check.require(identity, project_id, action="derive", identifier=review["content_hash"], tenant_id=identity.tenant_id,
                          resource_project_id=project_id, rights=review["rights"])
            Store.put(db, PREPARATION_KIND, identity.tenant_id, preparation, project_id)
            Store.put(db, "scenario", identity.tenant_id, scenario, project_id)
            Store.put(db, "import", identity.tenant_id, imported, project_id)
            project["baseline_scenario_id"] = scenario["id"]
            db.execute("UPDATE objects SET data=? WHERE kind='project' AND id=? AND tenant_id=?", (canonical(project), project_id, identity.tenant_id))
        for key, value in check.headers().items():
            response.headers[key] = value
        return {"schema_version": "prepared-site-v1", "preparation": preparation, "scenario": scenario, "project": project}

    @router.get("/api/projects/{project_id}/site-preparations/{preparation_id}")
    def preparation_read(project_id: str, preparation_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        record = required(store, PREPARATION_KIND, preparation_id, identity)
        project_resource(record, project_id)
        try:
            binding = {"id": record["id"], "content_hash": record["content_hash"],
                       "source_review_hash": record["source_review"]["content_hash"],
                       "compliance_bundle_hash": record["compliance_bundle"]["content_hash"],
                       "context_id": record["context_id"], "context_hash": record["context_hash"], "qualified": False}
            checked_preparation_dependency(store, {"project_id": project_id, "source_preparation": binding,
                                                   "source_rights": record["rights"]}, identity.tenant_id, error)
        except (KeyError, TypeError):
            error("SITE_PREPARATION_INTEGRITY_ERROR", "Reviewed site record failed integrity checks", 503)
        return record

    return router
