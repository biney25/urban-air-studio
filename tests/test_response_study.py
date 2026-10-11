"""Finite response preparation uses real Cedar, geometry and planning gates."""
from copy import deepcopy
from pathlib import Path
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.auth import Identity
from api.authorization import Authorizer
from api.main import create_app, local_identity, required, project_resource, physical_hash, scenario_version, compare
from api.planning import validate_programme
from api.response_study import make_router, seal
from api.storage import canonical, new_id, utc_now
from api.worker import work_once
from science.fixtures import load_fixture


@pytest.fixture
def setup():
    path = Path(__file__).resolve().parents[1] / "tmp" / ("response-study-test-" + uuid.uuid4().hex)
    parent_app = create_app(path)
    principal = {"tenant": "response-a", "user": "planner-a"}
    parent_app.dependency_overrides[local_identity] = lambda: Identity(principal["tenant"], principal["user"])
    with TestClient(parent_app) as parent_client:
        project = parent_client.post("/api/projects", json={"name": "Finite candidate preparation", "category": "internal_test"}).json()
        response = parent_client.post(f"/api/projects/{project['id']}/imports", json={"filename": "fixture.geojson", "geojson": load_fixture(), "rights": {"derive": True, "solve": True, "export": True}})
        assert response.status_code == 201, response.text
        scenario = response.json()["scenario"]
        response = parent_client.post(f"/api/projects/{project['id']}/planning-brief", json={"question": "Compare explicit source-rate alternatives", "expected_revision": 0})
        assert response.status_code == 201, response.text
        brief = response.json()
        store = parent_app.state.store
        app = FastAPI()
        app.include_router(make_router(identity_dependency=lambda: Identity(principal["tenant"], principal["user"]),
            store_dependency=lambda: store, required=required, project_resource=project_resource,
            physical_hash=physical_hash, scenario_version=scenario_version, authorizer=Authorizer(),
            constraint_validator=validate_programme, compare=compare))
        with TestClient(app) as client:
            yield client, store, principal, project, scenario, brief, parent_client


def preview(setup, **changes):
    client, _, _, project, scenario, brief, _ = setup
    body = {"name": "Source controls", "scenario_id": scenario["id"], "expected_version": scenario["version"],
            "parent_hash": scenario["content_hash"], "brief_hash": brief["content_hash"],
            "source_ids": [scenario["sources"][0]["id"]], "factors": [.5, .75, 1]}
    return client.post(f"/api/projects/{project['id']}/response-studies/preview", json={**body, **changes})


def commit(setup, record, **changes):
    client, _, _, project, *_ = setup
    return client.post(f"/api/projects/{project['id']}/response-studies", json={
        "preview_id": record["id"], "preview_hash": record["content_hash"], "idempotency_key": "reviewed-confirmation-01", **changes})


def update(store, kind, record):
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=?", (canonical(record), kind, record["id"]))


def test_review_commit_are_immutable_bounded_and_do_not_run(setup):
    client, store, principal, project, scenario, brief, _ = setup
    response = preview(setup)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["valid"] and record["requires_confirmation"]
    assert record["candidates"][-1]["unchanged_control"]
    assert len(store.list("scenario", principal["tenant"], project["id"])) == 1
    assert preview(setup).json() == record
    response = commit(setup, record)
    assert response.status_code == 201, response.text
    study = response.json()
    assert len(study["candidates"]) == 3 and not study["qualified"] and not study["provenance"]["optimal"]
    assert study["brief_hash"] == brief["content_hash"]
    for candidate in study["candidates"]:
        saved = store.get("scenario", candidate["scenario_id"], principal["tenant"])
        assert saved["parent_id"] == scenario["id"] and saved["version"] == scenario["version"] + 1
        assert saved["content_hash"] == candidate["scenario_hash"]
        for key in ("buildings", "receptors", "origin", "coordinate_frame", "analysis_domain", "source_rights"):
            assert saved[key] == scenario[key]
        expected = deepcopy(scenario["sources"])
        expected[0]["rate_g_s"] *= candidate["factor"]
        assert saved["sources"] == expected
    assert store.get("scenario", scenario["id"], principal["tenant"]) == scenario
    assert store.get("project", project["id"], principal["tenant"])["baseline_scenario_id"] is None
    assert store.list_runs(principal["tenant"], project["id"]) == []
    assert store.list("quote", principal["tenant"], project["id"]) == []
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM usage").fetchone()[0] == 0
    assert client.get(f"/api/projects/{project['id']}/response-studies").json() == [study]
    assert client.get(f"/api/projects/{project['id']}/response-studies/{study['id']}").json() == study
    assert commit(setup, record).json() == study
    other = preview(setup, factors=[.2]).json()
    assert commit(setup, other).status_code == 409
    assert len(store.list("scenario", principal["tenant"], project["id"])) == 4


@pytest.mark.parametrize("factors", [[True], ["0.5"], [-.1], [2.1], [], [.5,.5], [0,.5,1,2]])
def test_bad_factors_do_not_create_previews(setup, factors):
    assert preview(setup, factors=factors).status_code == 422
    _, store, principal, project, *_ = setup
    assert store.list("response_study_preview", principal["tenant"], project["id"]) == []


@pytest.mark.parametrize("source_ids", [[], ["foreign-source"], ["source-1", "source-1"]])
def test_source_selection_is_exact_and_unique(setup, source_ids):
    assert preview(setup, source_ids=source_ids).status_code == 422


def test_foreign_parent_project_tenant_and_preview_user(setup):
    client, store, principal, project, scenario, _, parent_client = setup
    other = parent_client.post("/api/projects", json={"name": "Other"}).json()
    imported = parent_client.post(f"/api/projects/{other['id']}/imports", json={"filename": "other.geojson", "geojson": load_fixture(), "rights": {"derive": True, "solve": True, "export": True}}).json()["scenario"]
    assert preview(setup, scenario_id=imported["id"], parent_hash=imported["content_hash"]).status_code == 404
    record = preview(setup).json()
    study = commit(setup, record).json()
    assert client.get(f"/api/projects/{other['id']}/response-studies/{study['id']}").status_code == 404
    principal["user"] = "other-user"
    assert commit(setup, record).status_code == 404
    assert client.get(f"/api/projects/{project['id']}/response-studies/previews/{record['id']}").status_code == 404
    principal["tenant"] = "other-tenant"
    assert preview(setup).status_code == 404
    assert client.get(f"/api/projects/{project['id']}/response-studies/{study['id']}").status_code == 404


def test_exact_parent_and_brief_checks(setup):
    _, store, principal, project, scenario, brief, parent_client = setup
    assert preview(setup, expected_version=2).status_code == 409
    assert preview(setup, parent_hash="sha256:" + "0"*64).status_code == 409
    assert preview(setup, brief_hash="sha256:" + "0"*64).status_code == 409
    record = preview(setup).json()
    parent_client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision":1,"question":"Revised assumptions"})
    assert commit(setup, record).status_code == 409
    assert len(store.list("scenario", principal["tenant"], project["id"])) == 1
    corrupt = deepcopy(scenario)
    corrupt["sources"][0]["rate_g_s"] += 1
    update(store, "scenario", corrupt)
    assert preview(setup).status_code == 503


def test_native_derive_permission_and_inventory_fail_closed(setup):
    _, store, _, _, scenario, *_ = setup
    denied = deepcopy(scenario)
    denied["source_rights"]["derive"] = False
    denied["content_hash"] = physical_hash(denied)
    update(store, "scenario", denied)
    response = preview(setup, parent_hash=denied["content_hash"])
    assert response.status_code == 403 and response.json()["detail"]["authorization"]["engine"] == "cedar-policy"
    inventory = deepcopy(scenario)
    inventory["source_allocation"] = {"inventory_id": "not-a-point-source"}
    update(store, "scenario", inventory)
    assert preview(setup).status_code == 409


def test_nonpoint_and_unconfigured_brief_block(setup):
    _, store, principal, project, scenario, *_ = setup
    nonpoint = deepcopy(scenario)
    nonpoint["sources"][0]["geometry_type"] = "LineString"
    nonpoint["content_hash"] = physical_hash(nonpoint)
    update(store, "scenario", nonpoint)
    assert preview(setup, parent_hash=nonpoint["content_hash"]).status_code == 422
    update(store, "scenario", scenario)
    changed = store.get("project", project["id"], principal["tenant"])
    changed["planning_brief_id"] = None
    update(store, "project", changed)
    assert preview(setup).status_code == 409


def test_constraint_failure_is_reviewable_but_never_committed(setup):
    _, store, principal, project, scenario, brief, parent_client = setup
    response = parent_client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision":1,"question":"Declared access", "access_corridors":[{"id":"blocked-path", "name":"Access", "footprint":scenario["buildings"][0]["footprint"]}]})
    record = preview(setup, brief_hash=response.json()["content_hash"]).json()
    assert not record["valid"] and all(not c["validation"]["valid"] for c in record["candidates"])
    assert commit(setup, record).status_code == 422
    assert len(store.list("scenario", principal["tenant"], project["id"])) == 1
    assert store.list("response_study", principal["tenant"], project["id"]) == []


def test_scaled_rate_outside_geometry_domain_blocks_every_sibling(setup):
    _, store, _, _, scenario, *_ = setup
    changed = deepcopy(scenario)
    changed["sources"][0]["rate_g_s"] = 80
    changed["content_hash"] = physical_hash(changed)
    update(store, "scenario", changed)
    record = preview(setup, parent_hash=changed["content_hash"], factors=[.5,2]).json()
    assert record["candidates"][0]["validation"]["valid"]
    assert not record["candidates"][1]["validation"]["valid"]
    assert commit(setup, record).status_code == 422


def test_transaction_rolls_back_prior_sibling_on_storage_failure(setup, monkeypatch):
    client, store, principal, project, *_ = setup
    record = preview(setup).json()
    original = store.put
    calls = []
    def fail_second(db, kind, tenant, value, project_id=None):
        if kind == "scenario":
            calls.append(value["id"])
            if len(calls) == 2:
                raise RuntimeError("Injected second-sibling storage failure")
        return original(db, kind, tenant, value, project_id)
    monkeypatch.setattr(store, "put", fail_second)
    with pytest.raises(RuntimeError):
        commit(setup, record)
    assert len(calls) == 2
    assert len(store.list("scenario", principal["tenant"], project["id"])) == 1
    assert store.list("response_study", principal["tenant"], project["id"]) == []
    assert store.list("response_study_commit", principal["tenant"], project["id"]) == []


def test_integrity_and_preview_quota(setup):
    client, store, principal, project, *_ = setup
    record = preview(setup).json()
    with store.transaction() as db:
        for i in range(99):
            value = seal({**record, "id":new_id("response_preview"), "name":str(i)})
            store.put(db, "response_study_preview", principal["tenant"], value, project["id"])
    assert preview(setup).json() == record
    assert preview(setup, factors=[.3]).status_code == 429
    changed = deepcopy(record)
    changed["candidates"][0]["factor"] = .99
    update(store, "response_study_preview", changed)
    assert commit(setup, record).status_code == 503


def test_study_quota_and_replay(setup):
    _, store, principal, project, *_ = setup
    record = preview(setup).json()
    study = commit(setup, record).json()
    with store.transaction() as db:
        for _ in range(9):
            value = seal({**study, "id":new_id("response_study")})
            store.put(db, "response_study", principal["tenant"], value, project["id"])
    assert commit(setup, record).json() == study
    assert commit(setup, record, idempotency_key="new-confirmation-02").status_code == 429


def test_comparison_requires_exact_completed_pair_and_frozen_brief(setup):
    client, store, principal, project, scenario, brief, _ = setup
    study = commit(setup, preview(setup).json()).json()
    candidate = study["candidates"][0]
    endpoint = f"/api/projects/{project['id']}/response-studies/{study['id']}/compare"
    body = {"candidate_scenario_id":candidate["scenario_id"],"baseline_run_id":"missing-a","alternative_run_id":"missing-b"}
    assert client.post(endpoint, json=body).status_code == 404
    body["candidate_scenario_id"] = scenario["id"]
    assert client.post(endpoint, json=body).status_code == 404
    body["candidate_scenario_id"] = candidate["scenario_id"]
    scenarios = [scenario, store.get("scenario", candidate["scenario_id"], principal["tenant"])]
    ids = []
    with store.transaction() as db:
        for selected in scenarios:
            run = {"id":new_id("run"), "project_id":project["id"], "scenario_id":selected["id"], "status":"queued", "created_at":utc_now(),
                   "manifest":{"scenario_id":selected["id"],"scenario_hash":selected["content_hash"],"inputs":selected,"planning_brief_hash":brief["content_hash"]}}
            store.insert_run(db, principal["tenant"], run)
            ids.append(run["id"])
    body.update(baseline_run_id=ids[0], alternative_run_id=ids[1])
    response = client.post(endpoint, json=body)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RUN_INCOMPLETE"
    body.update(baseline_run_id=ids[1], alternative_run_id=ids[0])
    assert client.post(endpoint, json=body).json()["detail"]["code"] == "RESPONSE_STUDY_RUN_MISMATCH"


def test_actual_worker_pair_matches_existing_comparison_and_detects_corruption(setup):
    client, store, principal, project, scenario, brief, parent_client = setup
    study = commit(setup, preview(setup, factors=[.5]).json()).json()
    candidate = study["candidates"][0]
    quote = parent_client.post(f"/api/projects/{project['id']}/quote", json={
        "scenario_ids":[scenario["id"],candidate["scenario_id"]], "tier":"T1",
        "conditions":{"wind_speed_m_s":2,"wind_from_deg":270,"diffusivity_m2_s":2,"background_ug_m3":0,"duration_s":10}})
    assert quote.status_code == 201, quote.text
    submission = parent_client.post(f"/api/projects/{project['id']}/runs", json={"quote_id":quote.json()["id"],"idempotency_key":uuid.uuid4().hex})
    assert submission.status_code == 202, submission.text
    runs = submission.json()["runs"]
    assert work_once(store) and work_once(store)
    assert all(store.run(r["id"])["status"] == "complete_unqualified" for r in runs)
    endpoint = f"/api/projects/{project['id']}/response-studies/{study['id']}/compare"
    body = {"candidate_scenario_id":candidate["scenario_id"],"baseline_run_id":runs[0]["id"],"alternative_run_id":runs[1]["id"]}
    result = client.post(endpoint, json=body)
    assert result.status_code == 200, result.text
    existing = parent_client.post(f"/api/projects/{project['id']}/compare", json={k:v for k,v in body.items() if k != "candidate_scenario_id"})
    assert result.json()["comparison"] == existing.json()
    assert result.json()["comparison"]["metrics"] and result.json()["qualified"] is False
    # A changed live brief does not rewrite the completed study's frozen evidence.
    parent_client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision":1,"question":"Different future study"})
    assert client.post(endpoint, json=body).status_code == 200
    run = store.run(runs[1]["id"])
    run["manifest"]["planning_brief_hash"] = "sha256:" + "0"*64
    with store.transaction() as db:
        db.execute("UPDATE runs SET data=? WHERE id=?", (canonical({k:v for k,v in run.items() if not k.startswith("_")}),run["id"]))
    assert client.post(endpoint, json=body).json()["detail"]["code"] == "RESPONSE_STUDY_RUN_MISMATCH"
    run["manifest"]["planning_brief_hash"] = brief["content_hash"]
    with store.transaction() as db:
        db.execute("UPDATE runs SET data=? WHERE id=?", (canonical({k:v for k,v in run.items() if not k.startswith("_")}),run["id"]))
    assert client.post(endpoint, json=body).json()["comparison"] == existing.json()


def test_preparation_dependency_is_preserved_and_current_authority_required(setup):
    _, store, _, _, scenario, *_ = setup
    changed = deepcopy(scenario)
    changed["source_preparation"] = {"id":"missing-source-grant"}
    changed["content_hash"] = physical_hash(changed)
    update(store, "scenario", changed)
    response = preview(setup, parent_hash=changed["content_hash"])
    assert response.status_code == 503 and response.json()["detail"]["code"] == "SITE_PREPARATION_INTEGRITY_ERROR"
