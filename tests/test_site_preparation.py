"""Scoped source review and immutable baseline integration, without provider IO.

These synthetic connector responses test the implementation, never real Delhi
coverage, height accuracy, environmental benefit or scientific qualification.
"""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.explore import CONTEXT_KIND, ExploreBroker
from api.main import Identity, create_app, error, get_store, local_identity
from api.site_preparation import (LEGACY_METHOD_VERSION, LOCAL_RIGHTS, METHOD_VERSION,
                                  PREPARATION_KIND, REVIEW_POLICY_HASH, _preview, _scoped_context,
                                  _study_grid_support, checked_preparation_dependency)
from api.site_preparation_schemas import SitePreparationRequest
from api.storage import Store, canonical, digest
from science.errors import ScienceError
from science.geometry import coordinate_transform, validate_scenario
from science.model import make_grid
from shapely.geometry import Point, Polygon


def provider_fixture():
    def rectangle(west):
        return [{"lon": west, "lat": 28.609}, {"lon": west + .0003, "lat": 28.609},
                {"lon": west + .0003, "lat": 28.6093}, {"lon": west, "lat": 28.6093},
                {"lon": west, "lat": 28.609}]
    return {"elements": [
        {"type": "way", "id": 11, "tags": {"building": "yes", "height": "12 m", "name": "Synthetic first building"}, "geometry": rectangle(77.219)},
        {"type": "way", "id": 12, "tags": {"building": "yes", "building:levels": "3"}, "geometry": rectangle(77.220)},
        {"type": "way", "id": 13, "tags": {"highway": "residential"}, "geometry": [{"lon": 77.217, "lat": 28.61}, {"lon": 77.223, "lat": 28.61}]},
        {"type": "relation", "id": 14, "tags": {"building": "yes", "type": "multipolygon"}},
    ]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_MODE", "local")
    store, requests = Store(tmp_path), []
    identity = {"tenant": "preparation-a"}
    payload = provider_fixture()
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=payload)
    app = create_app()
    app.state.store = store
    app.state.explore_broker = ExploreBroker(transport=httpx.MockTransport(handler),
        clock=lambda: datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp())
    app.dependency_overrides[local_identity] = lambda: Identity(identity["tenant"], "site-reviewer")
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Independent synthetic preparation site"}).json()
        context_response = client.post("/api/explore/region-context", json={
            "center": {"longitude": 77.22, "latitude": 28.61},
            "bounds": {"west": 77.217, "south": 28.607, "east": 77.223, "north": 28.613},
            "include_geometry": True, "include_weather": False, "project_id": project["id"]})
        assert context_response.status_code == 201, context_response.text
        context = context_response.json()
        yield client, store, app, identity, requests, project, context


def reviewed(context, **changes):
    body = {"context_id": context["id"], "expected_context_hash": context["content_hash"],
            "heights": [{"object_id": "osm:way:11", "height_m": 12, "height_basis": "osm_tag", "reference": "Retained OSM height tag, not a survey"},
                        {"object_id": "osm:way:12", "height_m": 10, "height_basis": "assumed", "reference": "Explicit synthetic test assumption, not measured"}],
            "exclusions": [{"object_id": "osm:relation:14", "reason": "Unsupported relation excluded from this bounded synthetic study"}],
            "acknowledge_flat_ground": True,
            "licence_review": {"review_id": "osm-odbl-local-study-v1", "accept_attribution": True,
                               "accept_local_only": True, "assumptions_owned_or_permitted": True},
            "study_inputs": {"mode": "geometry_only"}}
    body.update(changes)
    return body


def prefix(project):
    return f"/api/projects/{project['id']}/site-preparations"


def preview(setup, body=None):
    client, _, _, _, _, project, context = setup
    response = client.post(prefix(project) + "/preview", json=body or reviewed(context))
    assert response.status_code == 200, response.text
    return response.json()


def freeze(setup, body=None):
    client, _, _, _, _, project, context = setup
    body = body or reviewed(context)
    proposed = preview(setup, body)
    assert proposed["ready_to_commit"], proposed["issues"]
    response = client.post(prefix(project), json={**body, "expected_preparation_hash": proposed["preparation_hash"]})
    assert response.status_code == 201, response.text
    return response.json(), response


def replace_object(store, tenant, kind, record):
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?",
                   (canonical(record), kind, record["id"], tenant))


def rehash(record, field="content_hash"):
    record[field] = digest({k: v for k, v in record.items() if k != field})


def test_context_review_exposes_missing_heights_and_retained_rights(setup):
    client, store, _, identity, requests, project, context = setup
    before = deepcopy(store.get(CONTEXT_KIND, context["id"], identity["tenant"]))
    response = client.get(prefix(project) + "/context", params={"context_id": context["id"]})
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["schema_version"] == "site-preparation-context-v1"
    assert review["context_hash"] == context["content_hash"]
    assert review["buildings"][1]["height_m"] is None
    assert review["unsupported_objects"] == [{"object_id": "osm:relation:14", "reason": "Unresolved building relation/holes or quarantined geometry", "requires_exclusion": True}]
    assert review["licence_review"]["content_hash"] == REVIEW_POLICY_HASH
    assert review["context_only_road_count"] == 1
    assert all(not review["source_snapshot"]["rights"][name] for name in ("solver_input", "derive_geometry", "export_data", "share_public"))
    assert len(requests) == 1
    assert store.get(CONTEXT_KIND, context["id"], identity["tenant"]) == before


def test_preview_requires_explicit_height_exclusion_and_vertical_inputs(setup):
    context = setup[-1]
    result = preview(setup, reviewed(context, heights=[], exclusions=[], acknowledge_flat_ground=False))
    assert not result["ready_to_commit"] and result["proposed_scenario"] is None
    codes = {i["code"] for i in result["issues"]}
    assert codes == {"HEIGHT_REVIEW_REQUIRED", "UNRESOLVED_SOURCE_OBJECT", "VERTICAL_MODEL_UNREVIEWED", "SITE_BUILDING_REQUIRED"}
    assert result["compliance_bundle"]["height_alterations"] == []
    assert result["compliance_bundle"]["qualified"] is False


def test_geometry_only_preview_never_invents_emissions_or_assessment_points(setup):
    result = preview(setup)
    assert result["ready_to_commit"]
    assert result["proposed_scenario"]["sources"] == result["proposed_scenario"]["receptors"] == []
    assert result["proposed_scenario"]["source_rights"] == LOCAL_RIGHTS
    bundle = result["compliance_bundle"]
    assert bundle["schema_version"] == "osm-local-study-compliance-v1"
    required = {"licence", "licence_url", "attribution", "copyright_url", "source_context_id", "source_context_hash", "source_snapshot",
                "selected_original_objects", "height_alterations", "exclusions", "declared_study_inputs", "semantic_geojson", "conversion_method", "metric_frame", "permissions", "publication", "qualified", "content_hash"}
    assert required <= bundle.keys()
    assert bundle["permissions"] == LOCAL_RIGHTS
    assert bundle["selected_original_objects"][1]["properties"]["height_m"] is None
    assert bundle["height_alterations"][1]["height_m"] == 10
    assert bundle["height_alterations"][1]["verification"] == "assumed"
    assert bundle["metric_frame"]["unit"] == "m"
    assert bundle["content_hash"] == digest({k: v for k, v in bundle.items() if k != "content_hash"})
    assert result["audit"]["source_rights"]["export"] is False
    assert result["qualified"] is False


def test_exact_freeze_persists_real_review_binding_and_baseline_without_granting_original_source(setup):
    client, store, _, identity, requests, project, context = setup
    before = deepcopy(store.get(CONTEXT_KIND, context["id"], identity["tenant"]))
    result, response = freeze(setup)
    scenario, record = result["scenario"], result["preparation"]
    assert result["project"]["baseline_scenario_id"] == scenario["id"]
    assert scenario["source_rights"] == LOCAL_RIGHTS
    assert scenario["source_preparation"]["context_hash"] == context["content_hash"]
    assert scenario["source_preparation"]["compliance_bundle_hash"] == record["compliance_bundle"]["content_hash"]
    assert record["authorization"]["decision"] == "Allow"
    assert response.headers["x-authorization-engine"].startswith("cedar-policy/")
    assert checked_preparation_dependency(store, scenario, identity["tenant"], error) == record
    assert client.get(prefix(project) + "/" + record["id"]).json() == record
    assert store.get(CONTEXT_KIND, context["id"], identity["tenant"]) == before
    assert len(requests) == 1 and not store.list("run", identity["tenant"])
    assert len(store.list("import", identity["tenant"], project["id"])) == 1
    assert not record["qualified"] and not record["compliance_bundle"]["permissions"]["export"]


def test_baseline_cannot_be_replaced_by_second_preparation(setup):
    client, store, _, identity, _, project, _ = setup
    result, _ = freeze(setup)
    body = reviewed(setup[-1])
    response = client.post(prefix(project), json={**body, "expected_preparation_hash": preview(setup)["preparation_hash"]})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "BASELINE_ALREADY_SET"
    assert len(store.list(PREPARATION_KIND, identity["tenant"])) == 1
    assert store.get("project", project["id"], identity["tenant"])["baseline_scenario_id"] == result["scenario"]["id"]


def test_illustrative_study_requires_acknowledgement_and_keeps_declared_artificial_basis(setup):
    body = reviewed(setup[-1], study_inputs={"mode": "illustrative_point_study", "acknowledge_illustrative": True})
    result, _ = freeze(setup, body)
    assert len(result["scenario"]["sources"]) == 1 and len(result["scenario"]["receptors"]) == 3
    assert result["scenario"]["sources"][0]["rate_g_s"] == 1
    assert result["scenario"]["receptors"][-1]["protected"] is True
    assumptions = result["preparation"]["compliance_bundle"]["declared_study_inputs"]
    assert all(point["basis"] == "assumed" for point in assumptions["sources"] + assumptions["receptors"])
    assert "not traffic" in assumptions["sources"][0]["reference"]


@pytest.mark.parametrize("field", ["accept_attribution", "accept_local_only", "assumptions_owned_or_permitted"])
def test_rights_declarations_fail_closed_before_any_freeze(setup, field):
    client, store, _, identity, _, project, context = setup
    body = reviewed(context)
    body["licence_review"][field] = False
    result = preview(setup, body)
    assert not result["ready_to_commit"]
    assert result["source_review"]["rights"] == {"derive": False, "solve": False, "export": False}
    response = client.post(prefix(project), json={**body, "expected_preparation_hash": result["preparation_hash"]})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "LICENCE_USE_BLOCKED"
    assert response.json()["detail"]["authorization"]["decision"] == "Deny"
    assert not store.list("scenario", identity["tenant"])


@pytest.mark.parametrize("changes,code", [
    ({"heights": [{"object_id": "osm:way:11", "height_m": 14, "height_basis": "osm_tag", "reference": "Original source tag must match"}]}, "HEIGHT_SOURCE_MISMATCH"),
    ({"heights": [{"object_id": "osm:way:12", "height_m": 10, "height_basis": "osm_tag", "reference": "Missing source tag cannot qualify"}]}, "HEIGHT_SOURCE_MISMATCH"),
    ({"heights": [{"object_id": "wrong-building", "height_m": 10, "height_basis": "assumed", "reference": "Explicit assumption wrong object"}]}, "UNKNOWN_HEIGHT_OBJECT"),
    ({"exclusions": [{"object_id": "not-in-snapshot", "reason": "Unknown object cannot be excluded"}]}, "UNKNOWN_EXCLUSION"),
    ({"exclusions": [{"object_id": "osm:way:11", "reason": "Deliberate conflicting review"}]}, "EXCLUDED_HEIGHT_CONFLICT"),
])
def test_object_linked_reviews_never_silently_repair_missing_or_conflicting_inputs(setup, changes, code):
    result = preview(setup, reviewed(setup[-1], **changes))
    assert not result["ready_to_commit"] and code in {i["code"] for i in result["issues"]}


@pytest.mark.parametrize("kind,code", [("heights", "DUPLICATE_HEIGHT_REVIEW"), ("exclusions", "DUPLICATE_EXCLUSION")])
def test_duplicate_reviews_are_ambiguous_and_blocked(setup, kind, code):
    body = reviewed(setup[-1]); body[kind].append(deepcopy(body[kind][0]))
    assert code in {i["code"] for i in preview(setup, body)["issues"]}


def test_expected_context_and_preparation_hashes_block_stale_commit(setup):
    client, store, _, identity, _, project, context = setup
    body = reviewed(context, expected_context_hash="sha256:" + "0" * 64)
    assert client.post(prefix(project) + "/preview", json=body).status_code == 409
    body = reviewed(context)
    assert client.post(prefix(project), json={**body, "expected_preparation_hash": "sha256:" + "0" * 64}).status_code == 409
    old = preview(setup, body)
    body["heights"][1]["height_m"] = 11
    assert client.post(prefix(project), json={**body, "expected_preparation_hash": old["preparation_hash"]}).status_code == 409
    assert not store.list(PREPARATION_KIND, identity["tenant"])


def test_cross_tenant_and_project_access_denied(setup):
    client, store, _, identity, _, project, context = setup
    result, _ = freeze(setup)
    other = client.post("/api/projects", json={"name": "Other independent site"}).json()
    assert client.get(prefix(other) + "/context", params={"context_id": context["id"]}).status_code == 404
    assert client.get(prefix(other) + "/" + result["preparation"]["id"]).status_code == 404
    identity["tenant"] = "preparation-b"
    assert client.get(prefix(project) + "/context", params={"context_id": context["id"]}).status_code == 404
    assert client.post(prefix(project) + "/preview", json=reviewed(context)).status_code == 404
    assert client.get(prefix(project) + "/" + result["preparation"]["id"]).status_code == 404
    with pytest.raises(HTTPException) as denied:
        checked_preparation_dependency(store, result["scenario"], identity["tenant"], error)
    assert denied.value.status_code == 503


@pytest.mark.parametrize("field,value", [("qualified", True), ("context_id", "context_foreign"),
    ("context_hash", "sha256:" + "0" * 64), ("source_review_hash", "sha256:" + "1" * 64),
    ("compliance_bundle_hash", "sha256:" + "2" * 64), ("content_hash", "sha256:" + "3" * 64)])
def test_source_dependency_binding_cannot_be_changed(setup, field, value):
    result, _ = freeze(setup)
    scenario = deepcopy(result["scenario"]); scenario["source_preparation"][field] = value
    with pytest.raises(HTTPException) as denied:
        checked_preparation_dependency(setup[1], scenario, setup[3]["tenant"], error)
    assert denied.value.detail["code"] == "SITE_PREPARATION_INTEGRITY_ERROR"


@pytest.mark.parametrize("target", ["policy", "permissions", "bundle_geometry", "height_assumption", "audit", "request", "schema"])
def test_rehashed_tampered_review_still_fails_source_and_input_replay(setup, target):
    client, store, _, identity, _, project, _ = setup
    result, _ = freeze(setup)
    record, scenario = deepcopy(result["preparation"]), deepcopy(result["scenario"])
    if target == "policy":
        record["source_review"]["policy"]["scope"] = "Unbounded production use"
        record["source_review"]["policy_hash"] = digest(record["source_review"]["policy"])
    elif target == "permissions":
        record["source_review"]["rights"]["export"] = True
        record["compliance_bundle"]["permissions"]["export"] = True
        record["rights"]["export"] = scenario["source_rights"]["export"] = True
    elif target == "bundle_geometry":
        record["compliance_bundle"]["semantic_geojson"]["features"][0]["properties"]["height_m"] = 15
    elif target == "height_assumption":
        record["compliance_bundle"]["height_alterations"][1]["height_m"] = 15
    elif target == "audit":
        record["audit"]["geometry_accuracy"] = "surveyed"
    elif target == "request":
        record["request"]["heights"][1]["height_m"] = 15
    else:
        record["compliance_bundle"]["schema_version"] = "generic-rights-v0"
    rehash(record["source_review"]); rehash(record["compliance_bundle"]); rehash(record)
    scenario["source_preparation"].update(content_hash=record["content_hash"],
        source_review_hash=record["source_review"]["content_hash"], compliance_bundle_hash=record["compliance_bundle"]["content_hash"])
    replace_object(store, identity["tenant"], PREPARATION_KIND, record)
    with pytest.raises(HTTPException) as denied:
        checked_preparation_dependency(store, scenario, identity["tenant"], error)
    assert denied.value.detail["code"] == "SITE_PREPARATION_INTEGRITY_ERROR"
    response = client.get(prefix(project) + "/" + record["id"])
    assert response.status_code == 503


@pytest.mark.parametrize("target", ["raw", "geometry", "context_rights", "request_parameters"])
def test_context_and_raw_bytes_must_match_exactly_even_with_rehashed_envelope(setup, target):
    client, store, _, identity, _, project, context = setup
    record = deepcopy(store.get(CONTEXT_KIND, context["id"], identity["tenant"]))
    if target == "raw":
        record["raw_records"][0]["raw_base64"] = base64.b64encode(b'{"elements": []}').decode()
    elif target == "geometry":
        record["context"]["geometry"]["features"][0]["properties"]["height_m"] = 15
    elif target == "context_rights":
        record["context"]["snapshots"][0]["rights"]["solver_input"] = True
    else:
        record["raw_records"][0]["parameters"] = {"data": "unreviewed query"}
    rehash(record["context"]); rehash(record, "record_hash")
    replace_object(store, identity["tenant"], CONTEXT_KIND, record)
    response = client.get(prefix(project) + "/context", params={"context_id": context["id"]})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "SITE_SNAPSHOT_INTEGRITY_ERROR"


def test_missing_original_context_blocks_downstream_use(setup):
    result, _ = freeze(setup)
    with setup[1].transaction() as db:
        db.execute("DELETE FROM objects WHERE kind=? AND id=? AND tenant_id=?", (CONTEXT_KIND, setup[-1]["id"], setup[3]["tenant"]))
    with pytest.raises(HTTPException) as denied:
        checked_preparation_dependency(setup[1], result["scenario"], setup[3]["tenant"], error)
    assert denied.value.status_code == 404


def test_source_rights_cannot_be_escalated_but_explicit_branch_geometry_remains_possible(setup):
    result, _ = freeze(setup)
    scenario = deepcopy(result["scenario"])
    scenario["buildings"][0]["height_m"] = 11
    assert checked_preparation_dependency(setup[1], scenario, setup[3]["tenant"], error) == result["preparation"]
    scenario["source_rights"]["export"] = True
    with pytest.raises(HTTPException) as denied:
        checked_preparation_dependency(setup[1], scenario, setup[3]["tenant"], error)
    assert denied.value.status_code == 503
    assert checked_preparation_dependency(setup[1], {"source_rights": LOCAL_RIGHTS}, setup[3]["tenant"], error) is None


def test_nonlocal_mode_cannot_freeze_or_reuse_private_local_review(setup, monkeypatch):
    result, _ = freeze(setup)
    client, _, _, _, _, project, context = setup
    body = reviewed(context); proposed = preview(setup, body)
    monkeypatch.setenv("UA_MODE", "cognito")
    response = client.post(prefix(project), json={**body, "expected_preparation_hash": proposed["preparation_hash"]})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "SITE_PREPARATION_LOCAL_ONLY"
    with pytest.raises(HTTPException) as denied:
        checked_preparation_dependency(setup[1], result["scenario"], setup[3]["tenant"], error)
    assert denied.value.status_code == 409


@pytest.mark.parametrize("changes", [
    {"extra_field": True}, {"expected_context_hash": "bad-hash"}, {"acknowledge_flat_ground": "true"},
    {"licence_review": {"review_id": "generic-import", "accept_attribution": True, "accept_local_only": True, "assumptions_owned_or_permitted": True}},
    {"study_inputs": {"mode": "illustrative_point_study", "acknowledge_illustrative": False}},
    {"study_inputs": {"mode": "user_supplied", "sources": [], "receptors": []}},
    {"heights": [{"object_id": "osm:way:11", "height_m": True, "height_basis": "assumed", "reference": "Boolean cannot become a height"}]},
    {"heights": [{"object_id": "osm:way:11", "height_m": "12", "height_basis": "assumed", "reference": "String cannot become a height"}]},
    {"heights": [{"object_id": "osm:way:11", "height_m": 12, "height_basis": "assumed", "reference": "Explicit test assumption", "units": "feet"}]},
])
def test_schema_rejects_unreviewed_types_units_and_study_modes(changes):
    body = reviewed({"id": "context_test", "content_hash": "sha256:" + "a" * 64}, **changes)
    with pytest.raises(ValidationError):
        SitePreparationRequest.model_validate(body)


def test_user_supplied_points_inside_solids_cannot_pass_preparation(setup):
    body = reviewed(setup[-1], study_inputs={"mode": "user_supplied", "sources": [{"id": "explicit-source", "name": "Declared test source",
        "longitude": 77.21915, "latitude": 28.60915, "z_m": 1, "rate_g_s": 1, "basis": "assumed", "reference": "Explicit synthetic test input"}],
        "receptors": [{"id": "explicit-receptor", "name": "Declared assessment point", "longitude": 77.222, "latitude": 28.612,
        "z_m": 1.5, "basis": "assumed", "reference": "Explicit synthetic test input"}]})
    result = preview(setup, body)
    assert not result["ready_to_commit"] and "SOURCE_IN_SOLID" in {i["code"] for i in result["issues"]}
    body["study_inputs"]["sources"][0]["longitude"] = 77.224
    result = preview(setup, body)
    assert "STUDY_POINT_OUTSIDE_SELECTION" in {i["code"] for i in result["issues"]}


def test_unavailable_cedar_never_freezes_reviewed_site(setup):
    client, store, app, identity, _, project, context = setup
    body = reviewed(context); proposed = preview(setup, body)
    app.state.authorization.available = False
    response = client.post(prefix(project), json={**body, "expected_preparation_hash": proposed["preparation_hash"]})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "AUTHORIZATION_UNAVAILABLE"
    assert not store.list(PREPARATION_KIND, identity["tenant"])
    assert store.get("project", project["id"], identity["tenant"])["baseline_scenario_id"] is None


def test_explicit_building_exclusion_preserves_omitted_source_in_audit(setup):
    body = reviewed(setup[-1])
    body["heights"] = body["heights"][:1]
    body["exclusions"].append({"object_id": "osm:way:12", "reason": "Height evidence unavailable; excluded from this exact limited trial"})
    result, _ = freeze(setup, body)
    assert len(result["scenario"]["buildings"]) == 1
    assert len(result["preparation"]["compliance_bundle"]["exclusions"]) == 2
    assert any("physical completeness is not claimed" in w for w in result["preparation"]["warnings"])
    assert setup[-1]["geometry"]["missing_height_count"] == 1


def test_unlinked_same_tenant_context_can_freeze_new_project_without_source_mutation(setup):
    client, store, _, identity, _, project, context = setup
    record = deepcopy(store.get(CONTEXT_KIND, context["id"], identity["tenant"]))
    record["context"]["project_id"] = None
    rehash(record["context"]); rehash(record, "record_hash")
    replace_object(store, identity["tenant"], CONTEXT_KIND, record)
    new_context = record["context"]
    result, _ = freeze(setup, reviewed(new_context))
    assert result["scenario"]["project_id"] == project["id"]
    assert store.get(CONTEXT_KIND, context["id"], identity["tenant"]) == record


@pytest.mark.parametrize("height", [0, 81, float("nan"), float("inf")])
def test_height_range_and_nonfinite_values_fail_schema(height):
    body = reviewed({"id": "context_test", "content_hash": "sha256:" + "a" * 64})
    body["heights"][0]["height_m"] = height
    with pytest.raises(ValidationError):
        SitePreparationRequest.model_validate(body)


def test_more_than_150_height_reviews_cannot_sneak_into_method():
    body = reviewed({"id": "context_test", "content_hash": "sha256:" + "a" * 64})
    body["heights"] = [deepcopy(body["heights"][0]) for _ in range(151)]
    with pytest.raises(ValidationError):
        SitePreparationRequest.model_validate(body)


def test_new_artificial_preparation_quotes_actual_shared_t1_grid_and_records_support(setup):
    client, _, _, _, _, project, context = setup
    body = reviewed(context, study_inputs={"mode": "illustrative_point_study", "acknowledge_illustrative": True})
    result, _ = freeze(setup, body)
    scenario = result["scenario"]
    bundle = result["preparation"]["compliance_bundle"]
    assert bundle["conversion_method"] == METHOD_VERSION
    support = bundle["study_grid_support"]
    assert support == scenario["diagnostics"]["study_grid_support"]
    assert support["analysis_domain"] == scenario["analysis_domain"]
    grid = make_grid(scenario)
    for row in support["points"]:
        cell = tuple(row["cell_index_zyx"])
        assert grid.fluid[cell]
        assert row["requested_xyz_m"][:2] == pytest.approx(row["native_cell_centre_xyz_m"][:2], abs=1e-7)
        assert row["requested_xyz_m"][2] in {.5, 1.5} and row["native_cell_centre_xyz_m"][2] == 2
    branch = client.post(f"/api/scenarios/{scenario['id']}/branch", json={"name": "Lower artificial declared stimulus"}).json()
    response = client.post(f"/api/scenarios/{branch['id']}/operations", json={"expected_version": branch["version"],
        "operations": [{"type": "source_update", "object_id": "illustrative-source", "changes": {"rate_g_s": .5}}]})
    assert response.status_code == 201, response.text
    alternative = response.json()
    quote = client.post(f"/api/projects/{project['id']}/quote", json={"scenario_ids": [scenario["id"], alternative["id"]],
        "conditions": {"wind_speed_m_s": 3, "wind_from_deg": 270, "diffusivity_m2_s": 3, "background_ug_m3": 0, "duration_s": 120}})
    assert quote.status_code == 201, quote.text
    assert len(quote.json()["preflight_grid_sizes"]) == 2


def test_physical_point_validity_cannot_substitute_for_actual_raster_support(setup):
    scenario = preview(setup)["proposed_scenario"]
    grid = make_grid(scenario)
    polygons = [Polygon(b["footprint"]) for b in scenario["buildings"]]
    unsupported = None
    # Deliberately find an actual occupied boundary cell with physical free
    # space beside its solid centre. This reproduces the integrated failure.
    for iy, y in enumerate(grid.y):
        for ix, x in enumerate(grid.x):
            if grid.fluid[0, iy, ix]:
                continue
            for dx, dy in ((.49, 0), (-.49, 0), (0, .49), (0, -.49)):
                px, py = float(x + dx * grid.dx), float(y + dy * grid.dy)
                if not any(poly.covers(Point(px, py)) for poly in polygons):
                    unsupported = {"id": "physical-only-assessment", "name": "Declared physical free-space point",
                                   "x": px, "y": py, "z": 1.5, "protected": False}
                    break
            if unsupported:
                break
        if unsupported:
            break
    assert unsupported is not None
    scenario["receptors"] = [unsupported]
    assert validate_scenario(scenario)["valid"]
    with pytest.raises(ScienceError) as blocked:
        _study_grid_support(scenario)
    assert blocked.value.code == "MESH_FAILED" and blocked.value.issues[0]["object_id"] == unsupported["id"]
    _, inverse, _ = coordinate_transform(**scenario["origin"])
    source_lon, source_lat = inverse.transform(0, 0)
    receptor_lon, receptor_lat = inverse.transform(unsupported["x"], unsupported["y"])
    body = reviewed(setup[-1], study_inputs={"mode": "user_supplied", "sources": [{"id": "explicit-free-source", "name": "Explicit free source",
        "longitude": source_lon, "latitude": source_lat, "z_m": .5, "rate_g_s": 1, "basis": "assumed", "reference": "Explicit synthetic test assumption"}],
        "receptors": [{"id": unsupported["id"], "name": unsupported["name"], "longitude": receptor_lon, "latitude": receptor_lat,
                       "z_m": 1.5, "basis": "assumed", "reference": "Deliberately unsupported cell, no silent relocation"}]})
    rejected = preview(setup, body)
    assert not rejected["ready_to_commit"] and rejected["proposed_scenario"] is None
    assert any(issue["code"] == "MESH_FAILED" and issue["object_id"] == unsupported["id"] for issue in rejected["issues"])
    declared = rejected["compliance_bundle"]["declared_study_inputs"]["receptors"][0]
    assert declared["longitude"] == receptor_lon and declared["latitude"] == receptor_lat


def test_v1_frozen_records_replay_their_original_method_without_hidden_movement(setup):
    client, store, _, identity, _, project, context = setup
    body = reviewed(context, study_inputs={"mode": "illustrative_point_study", "acknowledge_illustrative": True})
    new, _ = freeze(setup, body)
    checked_context, snapshot, raw = _scoped_context(store, context["id"], identity["tenant"], error)
    original = _preview(checked_context, snapshot, raw, SitePreparationRequest.model_validate(body), LEGACY_METHOD_VERSION)
    legacy_record = deepcopy(new["preparation"])
    for field in ("preparation_hash", "source_review", "compliance_bundle", "audit", "warnings"):
        legacy_record[field] = deepcopy(original[field])
    rehash(legacy_record)
    replace_object(store, identity["tenant"], PREPARATION_KIND, legacy_record)
    legacy_scenario = deepcopy(new["scenario"])
    legacy_scenario.update(deepcopy(original["proposed_scenario"]))
    legacy_scenario["source_preparation"].update(content_hash=legacy_record["content_hash"],
        source_review_hash=legacy_record["source_review"]["content_hash"], compliance_bundle_hash=legacy_record["compliance_bundle"]["content_hash"])
    assert checked_preparation_dependency(store, legacy_scenario, identity["tenant"], error) == legacy_record
    assert "study_grid_support" not in legacy_record["compliance_bundle"]
    assert legacy_scenario["receptors"] == original["proposed_scenario"]["receptors"]
    assert legacy_record["compliance_bundle"]["conversion_method"] == LEGACY_METHOD_VERSION
    assert new["preparation"]["compliance_bundle"]["conversion_method"] == METHOD_VERSION
    assert original["preparation_hash"] != new["preparation"]["preparation_hash"]
