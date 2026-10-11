"""Programme requirements are declared, versioned and enforced on real APIs."""
from copy import deepcopy
import json
from fastapi.testclient import TestClient
import pytest

from api.main import create_app, local_identity
from api.auth import Identity
from api.planning import check_constraints
from api.storage import canonical
from science.fixtures import load_fixture


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path)
    identity = {"tenant": "planner-a"}
    app.dependency_overrides[local_identity] = lambda: Identity(identity["tenant"], "planner")
    with TestClient(app) as client:
        yield client, app.state.store, identity


def site(client):
    project = client.post("/api/projects", json={"name":"Capacity study", "category":"user", "planning_question":"Which design retains capacity without blocking access?", "site":{"latitude":28.61,"longitude":77.20,"width_km":0.25}}).json()
    imported = client.post(f"/api/projects/{project['id']}/imports", json={"filename":"fixture.geojson", "geojson":load_fixture(),"rights":{"derive":True,"solve":True,"export":True}})
    assert imported.status_code == 201, imported.text
    return project, imported.json()["scenario"]


def brief(client, project, **rules):
    return client.post(f"/api/projects/{project['id']}/planning-brief", json={"question":"Compare alternatives with declared requirements", "expected_revision":0, **rules})


def test_project_brief_and_category_are_real_persisted_fields(setup):
    client, store, identity = setup
    project, _ = site(client)
    saved = client.get(f"/api/projects/{project['id']}").json()["project"]
    assert saved["site"]["width_km"] == .25 and saved["category"] == "user"
    assert "capacity" in saved["planning_question"]
    response = client.post(f"/api/projects/{project['id']}/category", json={"category":"internal_test"})
    assert response.status_code == 200 and response.json()["category"] == "internal_test"
    assert store.get("project", project["id"], identity["tenant"])


@pytest.mark.parametrize("site_value", [{"latitude":True,"longitude":77,"width_km":.25},{"latitude":28,"longitude":77,"width_km":True},{"latitude":28,"longitude":77,"width_km":.4}])
def test_invalid_or_boolean_site_values_do_not_create_records(setup, site_value):
    client, store, identity = setup
    assert client.post("/api/projects", json={"name":"bad", "site":site_value}).status_code == 422
    assert store.list("project", identity["tenant"]) == []


def test_requirements_are_immutable_revisions_and_scoped(setup):
    client, store, identity = setup
    project, _ = site(client)
    first = brief(client, project, storey_height_m=3, min_floor_area_m2=10)
    assert first.status_code == 201, first.text
    record = first.json()
    assert brief(client, project).status_code == 409
    second = client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision":1,"question":"A new reviewed question"})
    assert second.status_code == 201 and second.json()["parent_id"] == record["id"]
    assert store.get("planning_brief", record["id"], identity["tenant"]) == record
    identity["tenant"] = "foreign"
    assert client.get(f"/api/projects/{project['id']}/planning-brief").status_code == 404


def test_capacity_requires_explicit_storey_assumption(setup):
    client, store, identity = setup
    project, scenario = site(client)
    assert brief(client, project, min_floor_area_m2=300).status_code == 422
    response = client.post(f"/api/projects/{project['id']}/constraints/check", json={"scenario_id":scenario["id"]})
    assert response.json()["status"] == "unconfigured" and not response.json()["valid"]


def test_real_edit_cannot_violate_declared_capacity(setup):
    client, _, _ = setup
    project, scenario = site(client)
    initial = brief(client, project, storey_height_m=3).json()
    area = check_constraints(scenario, initial)["evaluations"][0]["value_m2"]
    capacity = client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision":1,"question":"Retain programme capacity", "storey_height_m":3,"min_floor_area_m2":area})
    assert capacity.status_code == 201
    building = max(scenario["buildings"], key=lambda b:b["height_m"])
    edited = client.post(f"/api/scenarios/{scenario['id']}/operations", json={"expected_version":scenario["version"],"operations":[{"type":"building_update","object_id":building["id"],"changes":{"height_m":1}}]})
    assert edited.status_code == 422, edited.text
    assert edited.json()["detail"]["code"] == "PLANNING_CONSTRAINT_FAILED"
    assert client.get(f"/api/scenarios/{scenario['id']}").json()["content_hash"] == scenario["content_hash"]


def test_access_and_protected_support_checks_use_actual_geometry(setup):
    client, _, _ = setup
    project, scenario = site(client)
    building = scenario["buildings"][0]
    record = brief(client, project, access_corridors=[{"id":"access","name":"Declared pedestrian access","footprint":building["footprint"]}]).json()
    result = check_constraints(scenario, record)
    assert not result["valid"] and result["issues"][0]["code"] == "ACCESS_CORRIDOR_BLOCKED"
    candidate = deepcopy(scenario)
    protected = next(r for r in candidate["receptors"] if r["protected"])
    protected["x"] += 1
    assert any(i["code"] == "PROTECTED_LOCATION_CHANGED" for i in check_constraints(candidate, record, scenario)["issues"])


def test_corrupt_requirements_fail_closed(setup):
    client, store, identity = setup
    project, scenario = site(client)
    record = brief(client, project).json()
    record["requirements"]["min_floor_area_m2"] = 1
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='planning_brief' AND id=?", (canonical(record), record["id"]))
    assert client.post(f"/api/projects/{project['id']}/constraints/check", json={"scenario_id":scenario["id"]}).status_code == 503
