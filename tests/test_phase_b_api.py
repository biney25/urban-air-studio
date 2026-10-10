"""Immutable Phase B API contracts, real compiler files, scope and failure paths."""
from contextlib import closing
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import uuid
import zipfile

from fastapi.testclient import TestClient
import pytest

from api.main import Identity, create_app, local_identity, physical_hash
from api.phase_b import CASE_KIND, EVIDENCE_KIND, checked_case_files, logical_record
from api.storage import Store, canonical, digest
from evidence.processing import process_evidence
from science.fixtures import load_fixture

ROOT = Path(__file__).resolve().parents[1]
RIGHTS = {"derive":True,"solve":True,"export":True}
CONFIG = {"wind_from_deg":270,"reference_speed_m_s":3,"reference_height_m":10,"roughness_length_m":0.1,
          "kinematic_viscosity_m2_s":1.5e-5,"molecular_diffusivity_m2_s":1e-5,
          "turbulent_schmidt_number":0.7,"background_ug_m3":10}


def request(kind):
    return json.loads((ROOT / "datasets" / "phase_b" / f"{kind}-illustrative.json").read_text())


def client_for(store):
    app = create_app()
    app.state.store = store
    identity = {"tenant":"phase-b-"+uuid.uuid4().hex}
    app.dependency_overrides[local_identity] = lambda: Identity(identity["tenant"],"test-planner")
    return TestClient(app), identity


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    client, identity = client_for(store)
    with client:
        yield client, store, identity


def project(client):
    response = client.post("/api/projects",json={"name":"Phase B isolated integration"})
    assert response.status_code == 201, response.text
    return response.json()


def seed(client, rights=None):
    p = project(client)
    response = client.post(f"/api/projects/{p['id']}/imports",json={"filename":"synthetic.geojson",
        "geojson":load_fixture(),"rights":rights or RIGHTS,"source_label":"Illustrative software fixture"})
    assert response.status_code == 201, response.text
    return p, response.json()["scenario"]


def seed_restricted(client, store, tenant, rights):
    # Phase A's importer only admits all three rights. Seed a valid stored
    # restricted source snapshot directly to exercise Phase B's permission seam.
    p, scenario = seed(client)
    scenario["source_rights"] = rights
    scenario["content_hash"] = physical_hash(scenario)
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=? AND tenant_id=?",
                   (canonical(scenario),scenario["id"],tenant))
    return p, scenario


def save(client, p, body):
    response = client.post(f"/api/projects/{p['id']}/evidence",json=body)
    assert response.status_code == 201, response.text
    return response.json()


def prepare(client, p, scenario, config=None):
    return client.post(f"/api/projects/{p['id']}/detailed-cases",json={"scenario_id":scenario["id"],
        "expected_version":scenario["version"],"title":"Frozen detailed case","config":config or CONFIG})


def replay_chain(client, p, campaign_rights=None):
    body = request("measurement_campaign")
    body["rights"] = campaign_rights or RIGHTS
    campaign = save(client,p,body)
    body = request("replay_protocol")
    body["payload"]["campaign_evidence_id"] = campaign["id"]
    protocol = save(client,p,body)
    return campaign, protocol


def assessment(campaign, protocol):
    rows = process_evidence("measurement_campaign",campaign["payload"])["rows"]
    predictions = [{"observation_id":r["id"],"interval_start":r["interval_start_utc"],
                    "interval_end":r["interval_end_utc"],"value_ug_m3":r["value_ug_m3"]}
                   for r in rows if r["location_id"] in protocol["payload"]["evaluation_locations"]]
    return {"title":"Imported illustrative held-out predictions","kind":"replay_assessment",
        "evidence_kind":"illustrative","source_label":"Software test only","rights":RIGHTS,
        "payload":{"protocol_evidence_id":protocol["id"],"method_id":"test-imported-means",
                   "method_build_hash":"sha256:"+"a"*64,"species":"PM2.5","units":"ug/m3",
                   "time_support":"interval_mean","predictions":predictions}}


def tamper(store, tenant, kind, identifier, change, recompute=False):
    record = store.get(kind,identifier,tenant)
    change(record)
    if recompute:
        record["content_hash"] = digest(logical_record(record))
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?",
                   (canonical(record),kind,identifier,tenant))


def test_saved_evidence_round_trip_is_immutable_and_qualification_retains_failures(setup):
    client, store, identity = setup
    p = project(client)
    body = request("inventory")
    saved = save(client,p,body)
    body["payload"]["segments"][0]["length_m"] = 12
    fetched = client.get(f"/api/projects/{p['id']}/evidence/{saved['id']}")
    assert fetched.status_code == 200 and fetched.json() == saved
    assert saved["content_hash"] == digest(logical_record(saved))
    assert saved["summary"]["total_rate_g_s"] == pytest.approx(2)
    download = client.get(saved["download_url"])
    assert download.status_code == 200 and download.json() == saved
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["content-disposition"] == f'attachment; filename="{saved["id"]}.json"'
    readiness = client.get(f"/api/projects/{p['id']}/qualification").json()
    assert readiness["status"] == "unqualified" and readiness["detailed_method"]["qualified"] is False
    assert readiness["research_preview"]["failed_gates"]
    assert {x["id"] for x in readiness["packages"]} == {f"B{i:02d}" for i in range(1,13)}
    assert readiness["evidence"][0]["content_hash"] == saved["content_hash"]
    assert store.list_runs(identity["tenant"],p["id"]) == []


@pytest.mark.parametrize("mutation",[
    lambda b:b.update(unknown="forbidden"),
    lambda b:b["rights"].update(derive="true"),
    lambda b:b["payload"]["segments"][0].update(length_m=True),
    lambda b:b["payload"]["segments"][0].update(length_m=float("nan")),
    lambda b:b["payload"]["segments"][0].update(length_m=float("inf")),
    lambda b:b["payload"]["segments"][0]["fleet"][0].update(factor_units="mg/hour"),
    lambda b:b["payload"].update(interval_end=b["payload"]["interval_start"]),
    lambda b:b["payload"].update(interval_start="2026-10-09T10:00:00"),
    lambda b:b["payload"]["background"].update(includes_local_emissions=True),
    lambda b:b["payload"]["segments"].append(deepcopy(b["payload"]["segments"][0])),
])
def test_invalid_evidence_fields_numbers_units_time_and_background_never_persist(setup, mutation):
    client, store, identity = setup
    p = project(client)
    body = request("inventory")
    mutation(body)
    response = client.post(f"/api/projects/{p['id']}/evidence",content=json.dumps(body),headers={"content-type":"application/json"})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "INPUT_VALIDATION_FAILED"
    assert store.list(EVIDENCE_KIND,identity["tenant"],p["id"]) == []


def test_campaign_qc_retains_raw_invalid_readings_and_forbids_unknown_unit(setup):
    client, _, _ = setup
    p = project(client)
    body = request("measurement_campaign")
    body["payload"]["observations"][0]["raw_value"] = -0.3
    body["payload"]["observations"][1]["provider_qa"] = "rejected"
    saved = save(client,p,body)
    assert saved["payload"]["observations"][0]["raw_value"] == -0.3
    assert saved["summary"]["rows"][0]["value_ug_m3"] == -0.3
    assert saved["summary"]["rows"][1]["qc_state"] == "quarantined"
    body["payload"]["observations"][0]["raw_unit"] = "AQI"
    assert client.post(f"/api/projects/{p['id']}/evidence",json=body).status_code == 422


def test_evidence_permissions_and_replay_inheritance(setup):
    client, store, identity = setup
    p = project(client)
    denied = request("inventory")
    denied["rights"]["derive"] = False
    assert client.post(f"/api/projects/{p['id']}/evidence",json=denied).status_code == 403
    assert not store.list(EVIDENCE_KIND,identity["tenant"],p["id"])
    campaign, protocol = replay_chain(client,p,{"derive":True,"solve":False,"export":False})
    assert protocol["rights"] == RIGHTS
    assert protocol["effective_rights"] == campaign["effective_rights"]
    assert protocol["dependencies"][0]["content_hash"] == campaign["content_hash"]
    assert client.get(protocol["download_url"]).status_code == 403
    result = save(client,p,assessment(campaign,protocol))
    assert result["effective_rights"] == campaign["effective_rights"]
    assert {row["id"]:row["content_hash"] for row in result["dependencies"]} == {
        campaign["id"]:campaign["content_hash"],protocol["id"]:protocol["content_hash"]}
    assert client.get(result["download_url"]).status_code == 403


def test_every_artifact_path_checks_tenant_and_project_scope(setup):
    client, _, identity = setup
    p, scenario = seed(client)
    campaign, protocol = replay_chain(client,p)
    case = prepare(client,p,scenario)
    assert case.status_code == 201, case.text
    other = project(client)
    paths = [f"/api/projects/{p['id']}/qualification",f"/api/projects/{p['id']}/evidence/{campaign['id']}",
             campaign["download_url"],case.json()["download_url"]]
    for path in paths:
        response = client.get(path.replace(p["id"],other["id"]))
        assert response.status_code == (200 if path.endswith("qualification") else 404)
        if path.endswith("qualification"):
            assert response.json()["evidence"] == [] and response.json()["cases"] == []
    body = request("replay_protocol")
    body["payload"]["campaign_evidence_id"] = campaign["id"]
    assert client.post(f"/api/projects/{other['id']}/evidence",json=body).status_code == 404
    assert client.post(f"/api/projects/{other['id']}/evidence",json=assessment(campaign,protocol)).status_code == 404
    assert prepare(client,other,scenario).status_code == 404
    identity["tenant"] = "another-tenant"
    for path in paths:
        assert client.get(path).status_code == 404
    assert client.post(f"/api/projects/{p['id']}/evidence",json=request("inventory")).status_code == 404
    assert prepare(client,p,scenario).status_code == 404


@pytest.mark.parametrize("mutation",[
    lambda b:b["payload"].update(evaluation_locations=["site-1"]),
    lambda b:b["payload"].update(evaluation_locations=["unknown-location"]),
    lambda b:b["payload"].update(evaluation_blocks=deepcopy(b["payload"]["calibration_blocks"])),
])
def test_replay_location_time_leakage_and_missing_campaign_support_blocked(setup, mutation):
    client, store, identity = setup
    p = project(client)
    campaign = save(client,p,request("measurement_campaign"))
    body = request("replay_protocol")
    body["payload"]["campaign_evidence_id"] = campaign["id"]
    mutation(body)
    response = client.post(f"/api/projects/{p['id']}/evidence",json=body)
    assert response.status_code == 422, response.text
    assert len(store.list(EVIDENCE_KIND,identity["tenant"],p["id"])) == 1


def test_replay_assessment_exact_support_and_dependency_integrity(setup):
    client, store, identity = setup
    p = project(client)
    campaign, protocol = replay_chain(client,p)
    body = assessment(campaign,protocol)
    body["evidence_kind"] = "simulated"
    saved = save(client,p,body)
    assert saved["kind"] == "replay_assessment"
    assert saved["effective_evidence_kind"] == "illustrative"
    assert "qualified" not in saved["summary"] or saved["summary"]["qualified"] is False
    body["payload"]["predictions"][0]["interval_start"] = "2026-10-10T00:00:00Z"
    assert client.post(f"/api/projects/{p['id']}/evidence",json=body).status_code == 422
    tamper(store,identity["tenant"],EVIDENCE_KIND,campaign["id"],lambda r:r.update(title="Changed frozen campaign"),recompute=True)
    assert client.post(f"/api/projects/{p['id']}/evidence",json=assessment(campaign,protocol)).status_code == 503


@pytest.mark.parametrize("target",["payload","summary","rights","project_id","id"])
def test_stored_evidence_tamper_fails_retrieval_listing_and_download(setup, target):
    client, store, identity = setup
    p = project(client)
    saved = save(client,p,request("inventory"))
    tamper(store,identity["tenant"],EVIDENCE_KIND,saved["id"],lambda r:r.update({target:"tampered"}))
    for path in (f"/api/projects/{p['id']}/qualification",f"/api/projects/{p['id']}/evidence/{saved['id']}",saved["download_url"]):
        response = client.get(path)
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "ARTIFACT_INTEGRITY_ERROR"


def test_full_case_archive_matches_frozen_files_and_survives_scenario_edits(setup):
    client, store, identity = setup
    p, scenario = seed(client)
    response = prepare(client,p,scenario)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["status"] == "prepared_unexecuted" and saved["qualified"] is False
    assert saved["scenario_hash"] == scenario["content_hash"]
    assert saved["content_hash"] == digest(logical_record(saved))
    fetched = client.get(saved["download_url"])
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(fetched.content)) as archive:
        prefix = saved["id"]+"/"
        inputs = json.loads(archive.read(prefix+"urban-air-case-inputs.json"))
        assert inputs["scenario"] == scenario
        assert digest(inputs["config"]) == saved["manifest"]["api_binding"]["config_hash"]
        manifest = json.loads(archive.read(prefix+"urban-air-case-manifest.json"))
        assert manifest["case_record"] == saved
        for name, checksum in saved["manifest"]["api_binding"]["file_checksums"].items():
            content = archive.read(prefix+name)
            assert len(content) == checksum["bytes"]
            assert "sha256:"+hashlib.sha256(content).hexdigest() == checksum["sha256"]
        assert len(archive.namelist()) == saved["file_count"]+2
        assert any(name.endswith("system/blockMeshDict") for name in archive.namelist())
        assert saved["manifest"]["geometry"]
        assert all(prefix+solid["surface_path"] in archive.namelist() for solid in saved["manifest"]["geometry"])
    changed = client.post(f"/api/scenarios/{scenario['id']}/operations",json={"expected_version":scenario["version"],
        "operations":[{"type":"building_update","object_id":scenario["buildings"][0]["id"],"changes":{"height_m":24}}]})
    assert changed.status_code == 201
    assert client.get(saved["download_url"]).content == fetched.content
    assert not store.list_runs(identity["tenant"],p["id"])


@pytest.mark.parametrize("value",[True,0,2,"1"])
def test_case_expected_version_is_strict_and_stale_is_rejected(setup, value):
    client, store, identity = setup
    p, scenario = seed(client)
    body = {"scenario_id":scenario["id"],"expected_version":value,"config":CONFIG}
    response = client.post(f"/api/projects/{p['id']}/detailed-cases",json=body)
    assert response.status_code == (409 if value == 2 else 422), response.text
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


@pytest.mark.parametrize("rights",[{"derive":False,"solve":True,"export":True},{"derive":True,"solve":False,"export":True}])
def test_case_requires_derive_and_solve_permissions(setup, rights):
    client, store, identity = setup
    p, scenario = seed_restricted(client,store,identity["tenant"],rights)
    response = prepare(client,p,scenario)
    assert response.status_code == 403
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


def test_case_export_permission_and_scenario_hash_are_authoritative(setup):
    client, store, identity = setup
    p, scenario = seed_restricted(client,store,identity["tenant"],{"derive":True,"solve":True,"export":False})
    response = prepare(client,p,scenario)
    assert response.status_code == 201, response.text
    assert client.get(response.json()["download_url"]).status_code == 403
    with store.transaction() as db:
        modified = deepcopy(scenario)
        modified["sources"][0]["rate_g_s"] *= 2
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?",(canonical(modified),scenario["id"]))
    assert prepare(client,p,scenario).status_code == 503


@pytest.mark.parametrize("config",[{**CONFIG,"unexpected":1},{**CONFIG,"reference_speed_m_s":True},
    {**CONFIG,"reference_speed_m_s":float("nan")},{**CONFIG,"max_cells":True},{**CONFIG,"roughness_length_m":1}])
def test_invalid_case_config_never_persists(setup, config):
    client, store, identity = setup
    p, scenario = seed(client)
    response = client.post(f"/api/projects/{p['id']}/detailed-cases",content=json.dumps({"scenario_id":scenario["id"],
        "expected_version":scenario["version"],"config":config}),headers={"content-type":"application/json"})
    assert response.status_code == 422, response.text
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


@pytest.mark.parametrize("files",[{"../escape":"x"},{"system/../escape":"x"},{"/system/x":"x"},{"system\\x":"x"},
    {"system//x":"x"},{"Allrun":"shell text"},{"":"x"},{"constant/ x":"x"},{"system/x":"a\0b"},
    {"system/x":"a","system/X":"b"},{"system/x":"x"*32_000_001}])
def test_untrusted_compiler_paths_duplicate_names_and_oversize_fail_before_storage(files):
    with pytest.raises(ValueError):
        checked_case_files(files)


@pytest.mark.parametrize("mutation",[
    lambda c:c["files"].update({"../escaped-dictionary":"unsafe"}),
    lambda c:c["manifest"].update(qualified=True),
    lambda c:c["manifest"].update(config_hash="sha256:"+"0"*64),
    lambda c:c["manifest"].update(scenario_hash="sha256:"+"0"*64),
    lambda c:c["files"].update({"system/blockMeshDict":"changed without matching hash"}),
])
def test_invalid_compiler_output_cannot_publish_a_case(tmp_path, monkeypatch, mutation):
    import science.phase_b as compiler
    original_build=compiler.build_case
    def changed_output(*args):
        compiled=original_build(*args)
        mutation(compiled)
        return compiled
    monkeypatch.setattr(compiler,"build_case",changed_output)
    store=Store(tmp_path)
    client,identity=client_for(store)
    with client:
        p,scenario=seed(client)
        response=prepare(client,p,scenario)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "CASE_COMPILER_OUTPUT_INVALID"
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])
    assert not (store.directory/"artifacts").exists()


def test_failed_case_asset_publication_leaves_no_authoritative_record(setup, monkeypatch):
    client, store, identity = setup
    p, scenario = seed(client)
    def fail(*args):
        raise OSError("Injected durable upload failure")
    monkeypatch.setattr(store,"atomic_artifact",fail)
    response = prepare(client,p,scenario)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "CASE_STORAGE_FAILED"
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


@pytest.mark.parametrize("failure",["wrong_checksum","corrupt_durable_bytes"])
def test_case_rechecks_durable_bytes_and_checksum_before_publication(setup, monkeypatch, failure):
    client,store,identity=setup
    p,scenario=seed(client)
    original=store.atomic_artifact
    def invalid_asset(*args):
        pointer,checksum=original(*args)
        if failure == "wrong_checksum":
            return pointer,"sha256:"+"0"*64
        (store.directory/pointer).write_bytes(b'{"unexpected":"corrupt durable bytes"}')
        return pointer,checksum
    monkeypatch.setattr(store,"atomic_artifact",invalid_asset)
    response=prepare(client,p,scenario)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "CASE_STORAGE_FAILED"
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


def test_scenario_change_during_asset_write_cannot_publish_stale_binding(setup, monkeypatch):
    client,store,identity=setup
    p,scenario=seed(client)
    original=store.atomic_artifact
    def changed_source(*args):
        result=original(*args)
        changed=deepcopy(scenario)
        changed["source_label"]="Injected concurrent source metadata change"
        changed["content_hash"]=physical_hash(changed)
        with store.transaction() as db:
            db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?",(canonical(changed),scenario["id"]))
        return result
    monkeypatch.setattr(store,"atomic_artifact",changed_source)
    response=prepare(client,p,scenario)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "ARTIFACT_INTEGRITY_ERROR"
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


def test_campaign_change_during_assessment_cannot_publish_stale_dependency(setup, monkeypatch):
    import api.phase_b as module
    client,store,identity=setup
    p=project(client)
    campaign,protocol=replay_chain(client,p)
    original=module.assess_replay
    def changed_campaign(*args):
        result=original(*args)
        tamper(store,identity["tenant"],EVIDENCE_KIND,campaign["id"],lambda r:r.update(title="Injected concurrent campaign change"),recompute=True)
        return result
    monkeypatch.setattr(module,"assess_replay",changed_campaign)
    response=client.post(f"/api/projects/{p['id']}/evidence",json=assessment(campaign,protocol))
    assert response.status_code == 503 and response.json()["detail"]["code"] == "ARTIFACT_INTEGRITY_ERROR"
    assert len(store.list(EVIDENCE_KIND,identity["tenant"],p["id"])) == 2


def test_case_asset_is_verified_before_pointer_commit_and_failed_database_publish_rolls_back(setup, monkeypatch):
    client, store, identity = setup
    p, scenario = seed(client)
    original_atomic=store.atomic_artifact
    durable=[]
    def verified_first(*args):
        assert not store.list(CASE_KIND,identity["tenant"],p["id"])
        pointer,checksum=original_atomic(*args)
        assert store.read_artifact(pointer,checksum)["case_id"] == args[0]
        durable.append(pointer)
        return pointer,checksum
    monkeypatch.setattr(store,"atomic_artifact",verified_first)
    original_put=Store.put
    def fail_pointer_commit(db, kind, *args):
        if kind == CASE_KIND:
            assert durable
            raise OSError("Injected database commit failure after verified bytes")
        return original_put(db,kind,*args)
    monkeypatch.setattr(Store,"put",staticmethod(fail_pointer_commit))
    with TestClient(client.app,raise_server_exceptions=False) as safe_client:
        response=prepare(safe_client,p,scenario)
    assert response.status_code == 500
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])
    # Unreferenced durable bytes are private, never a published/eligible case.
    assert durable and (store.directory/durable[0]).is_file()


def test_failed_s3_case_upload_never_commits_pointer(setup):
    import boto3
    from botocore.stub import Stubber
    from api.artifacts import S3Artifacts
    client,store,identity=setup
    p,scenario=seed(client)
    service=boto3.client("s3",region_name="ap-south-1",aws_access_key_id="test-only",aws_secret_access_key="test-only")
    store.artifacts=S3Artifacts("urban-test-assets","phase-b-tests",client=service)
    with Stubber(service) as stub:
        stub.add_client_error("put_object","AccessDenied","Injected upload failure",http_status_code=403)
        response=prepare(client,p,scenario)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "CASE_STORAGE_FAILED"
        stub.assert_no_pending_responses()
    assert not store.list(CASE_KIND,identity["tenant"],p["id"])


def test_successful_s3_case_publish_and_download_use_authoritative_sha256(setup, monkeypatch):
    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import Stubber
    from api.artifacts import S3Artifacts
    client,store,identity=setup
    p,scenario=seed(client)
    service=boto3.client("s3",region_name="ap-south-1",aws_access_key_id="test-only",aws_secret_access_key="test-only")
    adapter=S3Artifacts("urban-test-assets","phase-b-tests",client=service)
    store.artifacts=adapter
    exact=[]
    def publish(identifier,fence,payload):
        content=canonical(payload).encode("utf-8")
        exact.append(content)
        assert not store.list(CASE_KIND,identity["tenant"],p["id"])
        with Stubber(service) as stub:
            stub.add_response("put_object",{})
            stub.add_response("get_object",{"Body":StreamingBody(BytesIO(content),len(content)),"ContentLength":len(content)})
            pointer,checksum=adapter.atomic_artifact(identifier,fence,payload)
            stub.assert_no_pending_responses()
        return pointer,checksum
    original_read=store.read_artifact
    def durable_read(pointer,checksum):
        with Stubber(service) as stub:
            content=exact[0]
            stub.add_response("get_object",{"Body":StreamingBody(BytesIO(content),len(content)),"ContentLength":len(content)})
            result=original_read(pointer,checksum)
            stub.assert_no_pending_responses()
        return result
    monkeypatch.setattr(store,"atomic_artifact",publish)
    monkeypatch.setattr(store,"read_artifact",durable_read)
    response=prepare(client,p,scenario)
    assert response.status_code == 201, response.text
    saved=response.json()
    assert saved["bundle_checksum"] == "sha256:"+hashlib.sha256(exact[0]).hexdigest()
    record=store.get(CASE_KIND,saved["id"],identity["tenant"])
    assert record["_artifact"].startswith("s3://urban-test-assets/phase-b-tests/")
    monkeypatch.setattr(store,"read_artifact",original_read)
    with Stubber(service) as stub:
        content=exact[0]
        stub.add_response("get_object",{"Body":StreamingBody(BytesIO(content),len(content)),"ContentLength":len(content)})
        assert client.get(saved["download_url"]).status_code == 200
        stub.assert_no_pending_responses()
    with Stubber(service) as stub:
        content=exact[0]+b" "
        stub.add_response("get_object",{"Body":StreamingBody(BytesIO(content),len(content)),"ContentLength":len(content)})
        assert client.get(saved["download_url"]).status_code == 503
        stub.assert_no_pending_responses()


def test_case_bundle_tamper_and_missing_authoritative_checksum_block_download(setup):
    client, store, identity = setup
    p, scenario = seed(client)
    response = prepare(client,p,scenario)
    assert response.status_code == 201, response.text
    saved = response.json()
    record = store.get(CASE_KIND,saved["id"],identity["tenant"])
    target = store.directory/record["_artifact"]
    bundle = json.loads(target.read_text())
    name = next(iter(bundle["files"]))
    bundle["files"][name] += "\nTampered"
    target.write_text(canonical(bundle),encoding="utf-8")
    assert client.get(saved["download_url"]).status_code == 503
    tamper(store,identity["tenant"],CASE_KIND,saved["id"],lambda r:r.pop("bundle_checksum"),recompute=True)
    assert client.get(saved["download_url"]).status_code == 503


def test_record_count_limit_closes_database_connections_and_is_atomic(setup, monkeypatch):
    import api.phase_b as module
    client, store, identity = setup
    p = project(client)
    monkeypatch.setattr(module,"MAX_RECORDS",1)
    save(client,p,request("inventory"))
    assert client.post(f"/api/projects/{p['id']}/evidence",json=request("inventory")).status_code == 409
    assert len(store.list(EVIDENCE_KIND,identity["tenant"],p["id"])) == 1
    connections=[]
    original_connect=store.connect
    class TrackedConnection:
        def __init__(self, connection):
            self.connection,self.closed=connection,False
        def __getattr__(self, name):
            return getattr(self.connection,name)
        def close(self):
            self.closed=True
            self.connection.close()
    def tracked_connect():
        connection=TrackedConnection(original_connect())
        connections.append(connection)
        return connection
    monkeypatch.setattr(store,"connect",tracked_connect)
    assert client.get(f"/api/projects/{p['id']}/qualification").status_code == 200
    assert connections and all(connection.closed for connection in connections)


def test_openapi_documents_all_strict_payload_types_and_conditional_kind_bindings(setup):
    client, _, _ = setup
    schema = client.get("/api/openapi.json").json()
    components=schema["components"]["schemas"]
    models={"Inventory","MeasurementCampaign","ReplayProtocol","ReplayAssessment"}
    assert models <= components.keys()
    assert {item["$ref"].split("/")[-1] for item in components["EvidenceRequest"]["properties"]["payload"]["oneOf"]} == models
    assert {rule["if"]["properties"]["kind"]["const"] for rule in components["EvidenceRequest"]["allOf"]} == {
        "inventory","measurement_campaign","replay_protocol","replay_assessment"}
    assert components["ReplayAssessment"]["properties"]["time_support"]["const"] == "interval_mean"
    assert components["Observation"]["properties"]["raw_unit"]["enum"] == ["ug/m3","mg/m3","ppb","ppm"]
    assert components["Inventory"]["additionalProperties"] is False


def test_real_postgis_generic_objects_need_no_new_schema(tmp_path):
    url = os.getenv("UA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit disposable local PostGIS DSN required")
    from api.postgres import PostgresStore
    store = PostgresStore(tmp_path,database_url=url,auto_migrate=False)
    client, identity = client_for(store)
    with client:
        p, scenario = seed(client)
        inventory = save(client,p,request("inventory"))
        campaign, protocol = replay_chain(client,p)
        replay = save(client,p,assessment(campaign,protocol))
        case = prepare(client,p,scenario)
        assert case.status_code == 201, case.text
        assert client.get(inventory["download_url"]).status_code == 200
        assert client.get(replay["download_url"]).status_code == 200
        assert client.get(case.json()["download_url"]).status_code == 200
        readiness = client.get(f"/api/projects/{p['id']}/qualification")
        assert readiness.status_code == 200 and len(readiness.json()["evidence"]) == 4
        with closing(store.connect()) as db:
            assert db.execute("SELECT COUNT(*) FROM objects WHERE tenant_id=? AND kind IN (?,?)",
                              (identity["tenant"],EVIDENCE_KIND,CASE_KIND)).fetchone()[0] == 5
        identity["tenant"] = "postgis-other-tenant"
        assert client.get(inventory["download_url"]).status_code == 404
        assert client.get(case.json()["download_url"]).status_code == 404


def publish_native_contract_fixture(client, store, identity, p, scenario, monkeypatch, *, status="executed_unqualified", key="review-fixture"):
    """Worker publication seam; every number below is injected, never real CFD.

    This verifies immutable byte/provenance authority only. Actual native
    acceptance is recorded separately by phase_b_detailed_live_check.py.
    """
    from api import detailed_execution as detailed
    runtime_identity={"distribution":"OpenFOAM Foundation","version":"13",
                      "source_commit":"contract-fixture-not-native","build_sha256":"sha256:"+"a"*64}
    runtime={"available":True,"qualified":False,"runtime_identity":runtime_identity,"runtime_digest":digest(runtime_identity)}
    monkeypatch.setattr(detailed,"native_runtime_readiness",lambda:deepcopy(runtime))
    prepared=prepare(client,p,scenario)
    assert prepared.status_code==201,prepared.text
    case=prepared.json()
    # The detailed router intentionally captures its trusted probe at install.
    # Construct this isolated app only after injecting the explicit test probe.
    worker_client,worker_identity=client_for(store)
    worker_identity["tenant"]=identity["tenant"]
    with worker_client:
        created=worker_client.post(f"/api/projects/{p['id']}/detailed-executions",json={"case_id":case["id"],
            "expected_case_hash":case["content_hash"],"idempotency_key":key})
    assert created.status_code==202,created.text
    job=created.json()
    def injected(bundle, *, execution_id, fence, cancel_check):
        manifest=bundle["manifest"]
        return {"schema_version":"phase-b-native-execution-v1","status":status,"qualified":False,"solver_executed":False,
            "attestation":{"execution_id":execution_id,"fence":fence,"case_bundle_checksum":digest(bundle),
                "runtime_digest":runtime["runtime_digest"],"runtime_identity":runtime_identity,
                "manifest_hash":digest(manifest),"scenario_hash":manifest["scenario_hash"],"config_hash":manifest["config_hash"],
                "prepared_file_hashes":manifest["file_hashes"],"shell":False,"uploaded_commands":False},
            "logs":{"test-fixture.log":"Injected software-contract fixture. No native solver or mesh executed.\n"},
            "mesh":None,"diagnostics":{"numerical_verification_passed":False,"issues":["software_contract_fixture"]},
            "resources":{"wall_seconds":0.1,"measurement_basis":"injected test fixture, not measured native solve"}}
    assert detailed.work_once(store,"review-contract-worker",readiness_fn=lambda:deepcopy(runtime),execute_fn=injected)
    record=store.get(detailed.JOB_KIND,job["id"],identity["tenant"])
    assert record["receipt_id"] is not None
    receipt=store.get(detailed.RECEIPT_KIND,record["receipt_id"],identity["tenant"])
    return case,record,receipt


def test_qualification_separates_actual_stored_diagnostics_from_25_unexecuted_scientific_gates(setup,monkeypatch):
    client,store,identity=setup
    p,scenario=seed(client)
    _,job,receipt=publish_native_contract_fixture(client,store,identity,p,scenario,monkeypatch)
    response=client.get(f"/api/projects/{p['id']}/qualification")
    assert response.status_code==200,response.text
    data=response.json()
    assert data["native_runtime"]["available"] is True and data["detailed_method"]["runtime_available"] is True
    # A stored software contract output contains no actual solver/mesh claim.
    assert data["detailed_method"]["solver_executed"] is False
    assert data["native_package_observations"]["counts"]=={"B01":0,"B02":0,"B04":0,"B09":1,"B12":1}
    assert data["scientific_qualification"]["scientific_gates_passed"] is False
    assert data["scientific_qualification"]["trusted_scientific_gate_receipt_count"]==0
    catalogue=data["scientific_requirements"]
    assert len(catalogue["gates"])==25 and all(gate["state"]=="unexecuted" for gate in catalogue["gates"])
    native=data["native_executions"][0]
    assert native["receipt_id"]==receipt["id"] and native["execution_id"]==job["id"]
    assert native["artifact_readback_verified"] is True and native["numerical_verification_passed"] is False
    assert native["results_eligible_for_comparison"] is False and native["status"]=="diagnostic_only"
    assert "logs" not in native and "frozen" not in native and native["analysis"] is None
    assert data["status"]=="unqualified" and data["research_preview"]["failed_gates"]


@pytest.mark.parametrize("runtime",[{"available":False},{"available":True},
    {"available":True,"qualified":True,"runtime_identity":{"fake":1},"runtime_digest":digest({"fake":1})},
    {"available":True,"qualified":False,"runtime_identity":{"fake":1},"runtime_digest":"sha256:"+"b"*64}])
def test_qualification_runtime_readiness_fails_closed_without_trusted_identity(setup,monkeypatch,runtime):
    from api import detailed_execution as detailed
    client,_,_=setup
    p=project(client)
    monkeypatch.setattr(detailed,"native_runtime_readiness",lambda:deepcopy(runtime))
    response=client.get(f"/api/projects/{p['id']}/qualification")
    assert response.status_code==200,response.text
    assert response.json()["native_runtime"]["available"] is False
    assert response.json()["detailed_method"]["runtime_available"] is False
    assert response.json()["scientific_qualification"]["independent_review_verified"] is False


def test_review_packet_has_frozen_inputs_license_checks_and_retained_failed_attempts(setup,monkeypatch):
    client,store,identity=setup
    p,scenario=seed(client)
    case,job,receipt=publish_native_contract_fixture(client,store,identity,p,scenario,monkeypatch,status="failed")
    inventory=save(client,p,request("inventory"))
    campaign,protocol=replay_chain(client,p)
    replay=save(client,p,assessment(campaign,protocol))
    response=client.get(f"/api/projects/{p['id']}/qualification/review-packet")
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["x-authorization-engine"]=="cedar-policy/4.12.0"
    packet=response.json()
    expected=packet.pop("packet_hash")
    assert expected==digest(packet)
    assert packet["schema_version"]=="phase-b-project-review-manifest-v1"
    assert packet["qualified"] is False and packet["scientific_gates_passed"] is False
    assert packet["independent_review_verified"] is False and packet["preregistered_protocol"] is None
    assert packet["retained_preview_failures"]["failed_gates"]
    assert len(packet["scientific_requirements"]["gates"])==25
    frozen=packet["cases"][0]
    assert frozen["scenario"]==scenario and frozen["case_record"]["id"]==case["id"]
    assert frozen["config"]["background_ug_m3"]==10 and frozen["artifact_readback_verified"] is True
    assert frozen["file_checksums"]==case["manifest"]["api_binding"]["file_checksums"]
    attempts=packet["native_attempts"]
    assert len(attempts)==1 and attempts[0]["status"]=="failed"
    assert attempts[0]["native_execution_hash"]==receipt["native_execution_hash"]
    assert attempts[0]["frozen"]==job["frozen"] and attempts[0]["rights"]==RIGHTS
    assert attempts[0]["resources"]["measurement_basis"].startswith("injected test fixture")
    assert packet["execution_lifecycle"][0]["id"]==job["id"] and packet["execution_lifecycle"][0]["status"]=="failed"
    assert not any(key.startswith("_") for key in packet["execution_lifecycle"][0])
    assert {row["id"] for row in packet["evidence"]}=={inventory["id"],campaign["id"],protocol["id"],replay["id"]}
    for evidence in packet["evidence"]:
        assert "payload" not in evidence and "observations" not in evidence["payload_metadata"]
        assert "predictions" not in evidence["payload_metadata"] and "rows" not in evidence["summary"]
        assert evidence["evidence_authority"]=="declared_import_not_native_solver_or_independent_qualification"


@pytest.mark.parametrize("restricted",["evidence","case","source"])
def test_review_packet_independently_denies_export_for_every_source_without_laundering(setup,monkeypatch,restricted):
    client,store,identity=setup
    if restricted=="case":
        p,scenario=seed_restricted(client,store,identity["tenant"],{**RIGHTS,"export":False})
    else:
        p,scenario=seed(client)
    prepare_response=prepare(client,p,scenario)
    assert prepare_response.status_code==201,prepare_response.text
    body=request("inventory")
    if restricted=="evidence":
        body["rights"]["export"]=False
    save(client,p,body)
    if restricted=="source":
        scenario["source_rights"]["export"]=False
        scenario["content_hash"]=physical_hash(scenario)
        with store.transaction() as db:
            db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=? AND tenant_id=?",
                       (canonical(scenario),scenario["id"],identity["tenant"]))
    response=client.get(f"/api/projects/{p['id']}/qualification/review-packet")
    assert response.status_code==403,response.text
    assert response.json()["detail"]["code"]=="LICENCE_USE_BLOCKED"
    assert "packet_hash" not in response.json()


def test_review_scope_cannot_read_other_tenant_or_include_other_project_attempts(setup,monkeypatch):
    client,store,identity=setup
    p,scenario=seed(client)
    publish_native_contract_fixture(client,store,identity,p,scenario,monkeypatch)
    other=project(client)
    packet=client.get(f"/api/projects/{other['id']}/qualification/review-packet")
    assert packet.status_code==200,packet.text
    assert packet.json()["cases"]==packet.json()["native_attempts"]==packet.json()["evidence"]==[]
    identity["tenant"]="different-review-tenant"
    assert client.get(f"/api/projects/{p['id']}/qualification").status_code==404
    assert client.get(f"/api/projects/{p['id']}/qualification/review-packet").status_code==404


@pytest.mark.parametrize("target",["receipt-bytes","case-bytes","missing-receipt","aliased-tenant","aliased-job","receipt-fence"])
def test_native_catalogue_rejects_corrupt_missing_or_aliased_published_authority(setup,monkeypatch,target):
    from api import detailed_execution as detailed
    client,store,identity=setup
    p,scenario=seed(client)
    case,job,receipt=publish_native_contract_fixture(client,store,identity,p,scenario,monkeypatch)
    with store.transaction() as db:
        if target=="missing-receipt":
            db.execute("DELETE FROM objects WHERE kind=? AND id=?",(detailed.RECEIPT_KIND,receipt["id"]))
        elif target in {"aliased-tenant","aliased-job"}:
            if target=="aliased-tenant":job["_tenant"]="another-tenant"
            else:job["id"]="dexec_"+"f"*32
            detailed._seal(job)
            db.execute("UPDATE objects SET data=? WHERE kind=? AND id=?",(canonical(job),detailed.JOB_KIND,receipt["execution_id"]))
        elif target=="receipt-fence":
            receipt["fence"]=True
            detailed._seal(receipt)
            db.execute("UPDATE objects SET data=? WHERE kind=? AND id=?",(canonical(receipt),detailed.RECEIPT_KIND,receipt["id"]))
    if target in {"receipt-bytes","case-bytes"}:
        record=receipt if target=="receipt-bytes" else store.get(CASE_KIND,case["id"],identity["tenant"])
        (store.directory/record["_artifact"]).write_bytes(b'{"tampered":true}')
    for suffix in ("","/review-packet"):
        response=client.get(f"/api/projects/{p['id']}/qualification{suffix}")
        assert response.status_code==503,response.text
        assert response.json()["detail"]["code"]=="ARTIFACT_INTEGRITY_ERROR"


@pytest.mark.parametrize("claim",["runtime","compiler","diagnostics","resources"])
def test_even_rehashed_native_artifact_cannot_change_frozen_provenance_or_curated_measurements(setup,monkeypatch,claim):
    from api import detailed_execution as detailed
    client,store,identity=setup
    p,scenario=seed(client)
    _,job,receipt=publish_native_contract_fixture(client,store,identity,p,scenario,monkeypatch)
    payload=store.read_artifact(receipt["_artifact"],receipt["artifact_checksum"])
    if claim=="runtime":payload["native_execution"]["attestation"]["runtime_identity"]={"forged":True}
    elif claim=="compiler":payload["native_execution"]["attestation"]["config_hash"]="sha256:"+"f"*64
    elif claim=="diagnostics":payload["receipt"]["diagnostics"]["numerical_verification_passed"]=True
    else:payload["receipt"]["resources"]["wall_seconds"]=99
    payload["receipt"]["native_execution_hash"]=digest(payload["native_execution"])
    pointer,checksum=store.atomic_artifact(job["id"],99,payload)
    updated={**payload["receipt"],"_artifact":pointer,"artifact_checksum":checksum}
    detailed._seal(updated)
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=?",(canonical(updated),detailed.RECEIPT_KIND,receipt["id"]))
    response=client.get(f"/api/projects/{p['id']}/qualification/review-packet")
    assert response.status_code==503,response.text
    assert response.json()["detail"]["code"]=="ARTIFACT_INTEGRITY_ERROR"


def test_upload_cannot_issue_native_gate_receipt_or_approve_review(setup):
    client,store,identity=setup
    p=project(client)
    body=request("inventory")
    body["payload"]["qualified"]=True
    body["payload"]["native_execution_hash"]="sha256:"+"f"*64
    response=client.post(f"/api/projects/{p['id']}/evidence",json=body)
    assert response.status_code==422
    response=client.post(f"/api/projects/{p['id']}/qualification/review-packet",json={"qualified":True,"reviewer":"self"})
    assert response.status_code==405
    result=client.get(f"/api/projects/{p['id']}/qualification").json()
    assert result["native_executions"]==[] and result["scientific_qualification"]["qualified"] is False
    assert not store.list(EVIDENCE_KIND,identity["tenant"],p["id"])


def test_review_preserves_queued_cancellation_without_inventing_solver_receipt(setup,monkeypatch):
    from api import detailed_execution as detailed
    client,store,identity=setup
    p,scenario=seed(client)
    case,_,_=publish_native_contract_fixture(client,store,identity,p,scenario,monkeypatch)
    bound_client,bound_identity=client_for(store)
    bound_identity["tenant"]=identity["tenant"]
    with bound_client:
        queued=bound_client.post(f"/api/projects/{p['id']}/detailed-executions",json={"case_id":case["id"],
            "expected_case_hash":case["content_hash"],"idempotency_key":"cancel-before-publication"})
        assert queued.status_code==202,queued.text
        cancelled=bound_client.post(f"/api/projects/{p['id']}/detailed-executions/{queued.json()['id']}/cancel")
        assert cancelled.status_code==200,cancelled.text
    packet=client.get(f"/api/projects/{p['id']}/qualification/review-packet")
    assert packet.status_code==200,packet.text
    jobs={row["id"]:row for row in packet.json()["execution_lifecycle"]}
    assert jobs[queued.json()["id"]]["status"]=="cancelled"
    assert jobs[queued.json()["id"]]["receipt_id"] is None
    assert len(packet.json()["native_attempts"])==1


def test_review_case_artifact_corruption_fails_closed_even_without_native_attempt(setup):
    client,store,identity=setup
    p,scenario=seed(client)
    prepared=prepare(client,p,scenario)
    assert prepared.status_code==201,prepared.text
    case=store.get(CASE_KIND,prepared.json()["id"],identity["tenant"])
    (store.directory/case["_artifact"]).write_text('{"tampered":true}',encoding="utf-8")
    response=client.get(f"/api/projects/{p['id']}/qualification/review-packet")
    assert response.status_code==503,response.text


def allocation_inputs(client,p,scenario,*,rights=None,species="passive_tracer"):
    body=request("inventory")
    body["payload"]["species"]=species
    body["payload"]["segments"][0]["length_m"]=8
    body["payload"]["segments"][0]["fleet"][0]["factor_g_per_vehicle_km"]=625
    body["rights"]=rights or RIGHTS
    inventory=save(client,p,body)
    context=client.get(f"/api/projects/{p['id']}/source-allocations/context",params={"scenario_id":scenario["id"]})
    assert context.status_code==200,context.text
    meta=context.json()
    source=scenario["sources"][0]
    allocation={"scenario_id":scenario["id"],"expected_version":scenario["version"],"expected_scenario_hash":scenario["content_hash"],
        "inventory_evidence_id":inventory["id"],"expected_inventory_hash":inventory["content_hash"],"replace_manual_sources":True,
        "geometry":{"unit":"m","coordinate_frame_hash":meta["coordinate_frame_hash"],
            "roads":[{"segment_id":"road-1","coordinates":[[source["x"]-4,source["y"]],[source["x"]+4,source["y"]]]}]},
        "controls":{"max_element_length_m":8,"source_height_m":4,"max_source_elements":200}}
    return inventory,meta,allocation


def apply_allocation(client,p,body):
    response=client.post(f"/api/projects/{p['id']}/source-allocations",json=body)
    assert response.status_code==201,response.text
    assert response.headers["x-authorization-engine"]=="cedar-policy/4.12.0"
    return response.json()["allocation"],response.json()["scenario"]


def test_inventory_geometry_allocation_creates_child_freezes_case_and_review_without_upgrading_source(setup):
    client,store,identity=setup
    p,parent=seed(client)
    inventory,meta,body=allocation_inputs(client,p,parent)
    assert meta["coordinate_frame_hash"]==digest(parent["coordinate_frame"])
    assert meta["admitted_species"]==["passive_tracer"] and meta["units"]=="m"
    allocation,child=apply_allocation(client,p,body)
    assert child["id"]!=parent["id"] and child["parent_id"]==parent["id"]
    assert client.get(f"/api/scenarios/{parent['id']}").json()==parent
    assert child["sources"]!=parent["sources"] and len(child["sources"])==1
    assert child["sources"][0]["rate_g_s"]==pytest.approx(5)
    assert child["sources"][0]["z"]==4 and child["source_rights"]==RIGHTS
    assert allocation["effective_evidence_kind"]=="illustrative" and allocation["qualified"] is False
    assert allocation["summary"]["total_range_g_s"]==pytest.approx([3.6,6.6])
    assert allocation["summary"]["range_basis"]=="declared_parameter_bounds_not_probability_or_confidence_interval"
    assert allocation["summary"]["native_source_assignment_verified"] is False
    assert allocation["summary"]["mass_allocation_arithmetic_verified"] is True
    prepared=prepare(client,p,child)
    assert prepared.status_code==201,prepared.text
    case=prepared.json()
    assert case["manifest"]["api_binding"]["source_allocation"]==child["source_allocation"]
    assert client.get(case["download_url"]).status_code==200
    raw=client.get(allocation["download_url"])
    assert raw.status_code==200,raw.text
    assert raw.headers["cache-control"]=="no-store"
    assert raw.json()["inputs"]["inventory_payload"]==inventory["payload"]
    assert raw.json()["inputs"]["plan"]["source_set_hash"]==digest(child["sources"])
    catalogue=client.get(f"/api/projects/{p['id']}/qualification")
    assert catalogue.status_code==200,catalogue.text
    assert catalogue.json()["source_allocations"][0]==allocation
    packet=client.get(f"/api/projects/{p['id']}/qualification/review-packet")
    assert packet.status_code==200,packet.text
    assert packet.json()["source_allocations"][0]["allocation_record"]==allocation
    assert packet.json()["source_allocations"][0]["plan"]["sources"]==child["sources"]
    assert packet.json()["qualified"] is False


@pytest.mark.parametrize("change",["replace-false","replace-int","scenario-version","scenario-hash","inventory-hash","frame","PM2.5","coarse-total"])
def test_allocation_requires_exact_declared_replacement_and_compatible_frozen_support(setup,change):
    from api.phase_b import ALLOCATION_KIND
    client,store,identity=setup
    p,parent=seed(client)
    species="PM2.5" if change=="PM2.5" else "passive_tracer"
    inventory,_,body=allocation_inputs(client,p,parent,species=species)
    if change=="replace-false":body["replace_manual_sources"]=False
    elif change=="replace-int":body["replace_manual_sources"]=1
    elif change=="scenario-version":body["expected_version"]+=1
    elif change=="scenario-hash":body["expected_scenario_hash"]="sha256:"+"f"*64
    elif change=="inventory-hash":body["expected_inventory_hash"]="sha256:"+"f"*64
    elif change=="frame":body["geometry"]["coordinate_frame_hash"]="sha256:"+"f"*64
    elif change=="coarse-total":
        alternative=request("inventory")
        alternative["payload"]=deepcopy(inventory["payload"])
        alternative["payload"]["background"].update(mode="coarse_total_baseline_adjustment",includes_local_emissions=True,local_baseline_ug_m3=2)
        changed=save(client,p,alternative)
        body.update(inventory_evidence_id=changed["id"],expected_inventory_hash=changed["content_hash"])
    response=client.post(f"/api/projects/{p['id']}/source-allocations",json=body)
    assert response.status_code in {409,422},response.text
    assert store.list(ALLOCATION_KIND,identity["tenant"],p["id"])==[]
    assert len(store.list("scenario",identity["tenant"],p["id"]))==1


def test_allocation_preserves_restricted_inventory_permissions_for_case_and_all_exports(setup):
    client,store,identity=setup
    p,parent=seed(client)
    _,_,body=allocation_inputs(client,p,parent,rights={**RIGHTS,"solve":False,"export":False})
    allocation,child=apply_allocation(client,p,body)
    assert child["source_rights"]==allocation["effective_rights"]=={**RIGHTS,"solve":False,"export":False}
    assert prepare(client,p,child).status_code==403
    assert client.get(allocation["download_url"]).status_code==403
    assert client.get(f"/api/projects/{p['id']}/qualification/review-packet").status_code==403


def test_changed_allocated_sources_and_background_require_explicit_rederivation(setup):
    client,_,_=setup
    p,parent=seed(client)
    _,_,body=allocation_inputs(client,p,parent)
    _,child=apply_allocation(client,p,body)
    changed=client.post(f"/api/scenarios/{child['id']}/operations",json={"expected_version":child["version"],
        "operations":[{"type":"source_update","object_id":child["sources"][0]["id"],"changes":{"rate_g_s":2.5}}]})
    assert changed.status_code==201,changed.text
    edited=changed.json()
    rejected=prepare(client,p,edited)
    assert rejected.status_code==409 and rejected.json()["detail"]["code"]=="ALLOCATION_BINDING_STALE"
    assert prepare(client,p,child,{**CONFIG,"background_ug_m3":9}).status_code==422
    body.update(scenario_id=edited["id"],expected_version=edited["version"],expected_scenario_hash=edited["content_hash"])
    replacement,rederived=apply_allocation(client,p,body)
    assert rederived["parent_id"]==edited["id"] and rederived["sources"][0]["rate_g_s"]==5
    assert replacement["id"]!=child["source_allocation"]["allocation_id"]
    assert prepare(client,p,rederived).status_code==201


def test_allocation_scope_and_immutable_plan_bytes_fail_closed(setup):
    from api.phase_b import ALLOCATION_KIND
    client,store,identity=setup
    p,parent=seed(client)
    _,_,body=allocation_inputs(client,p,parent)
    allocation,child=apply_allocation(client,p,body)
    other=project(client)
    assert client.get(f"/api/projects/{other['id']}/source-allocations/{allocation['id']}").status_code==404
    assert client.post(f"/api/projects/{other['id']}/source-allocations",json=body).status_code==404
    record=store.get(ALLOCATION_KIND,allocation["id"],identity["tenant"])
    (store.directory/record["_artifact"]).write_bytes(b'{"forged":true}')
    assert client.get(allocation["download_url"]).status_code==503
    assert prepare(client,p,child).status_code==503
    assert client.get(f"/api/projects/{p['id']}/qualification/review-packet").status_code==503
    identity["tenant"]="another-allocation-tenant"
    assert client.get(allocation["download_url"]).status_code==404


def test_revoked_inventory_solve_permission_prevents_native_receipt_publication(setup,monkeypatch):
    from api import detailed_execution as detailed
    client,store,identity=setup
    p,parent=seed(client)
    inventory,_,body=allocation_inputs(client,p,parent)
    _,child=apply_allocation(client,p,body)
    # Queue a real internal job with a labelled injected output; revoke only the
    # controlled test database's inventory during publication, never live state.
    runtime_identity={"contract_fixture":True}
    runtime={"available":True,"qualified":False,"runtime_identity":runtime_identity,"runtime_digest":digest(runtime_identity)}
    monkeypatch.setattr(detailed,"native_runtime_readiness",lambda:deepcopy(runtime))
    case=prepare(client,p,child).json()
    bound_client,bound_identity=client_for(store)
    bound_identity["tenant"]=identity["tenant"]
    with bound_client:
        created=bound_client.post(f"/api/projects/{p['id']}/detailed-executions",json={"case_id":case["id"],
            "expected_case_hash":case["content_hash"],"idempotency_key":"inventory-revocation"})
        assert created.status_code==202,created.text
    def injected(bundle,*,execution_id,fence,cancel_check):
        return {"schema_version":"phase-b-native-execution-v1","status":"executed_unqualified","qualified":False,
            "attestation":{"execution_id":execution_id,"fence":fence,"case_bundle_checksum":digest(bundle),"runtime_digest":runtime["runtime_digest"]},
            "logs":{"fixture.log":"Software permission race fixture; no native solve"},
            "diagnostics":{"numerical_verification_passed":False},"resources":{}}
    def revoke(_job):
        tamper(store,identity["tenant"],EVIDENCE_KIND,inventory["id"],
            lambda value:(value["rights"].update(solve=False),value["effective_rights"].update(solve=False)),recompute=True)
    assert detailed.work_once(store,"allocation-authority-worker",readiness_fn=lambda:deepcopy(runtime),execute_fn=injected,before_publish=revoke)
    job=store.get(detailed.JOB_KIND,created.json()["id"],identity["tenant"])
    assert job["status"]=="failed" and job["receipt_id"] is None
    assert job["error"]["code"]=="EXECUTION_INTEGRITY_FAILED"
    assert not store.list(detailed.RECEIPT_KIND,identity["tenant"],p["id"])


def test_failed_allocation_artifact_readback_publishes_neither_record_nor_child(setup,monkeypatch):
    from api.phase_b import ALLOCATION_KIND
    client,store,identity=setup
    p,parent=seed(client)
    _,_,body=allocation_inputs(client,p,parent)
    def fail_readback(*args):
        raise OSError("Controlled isolated storage fault")
    monkeypatch.setattr(store,"read_artifact",fail_readback)
    response=client.post(f"/api/projects/{p['id']}/source-allocations",json=body)
    assert response.status_code==503 and response.json()["detail"]["code"]=="ALLOCATION_STORAGE_FAILED"
    assert store.list(ALLOCATION_KIND,identity["tenant"],p["id"])==[]
    assert len(store.list("scenario",identity["tenant"],p["id"]))==1


def test_rederived_allocation_cannot_launder_revoked_ancestor_inventory_export(setup):
    client,store,identity=setup
    p,parent=seed(client)
    first_inventory,_,body=allocation_inputs(client,p,parent)
    _,first_child=apply_allocation(client,p,body)
    _,_,new_body=allocation_inputs(client,p,first_child)
    second,second_child=apply_allocation(client,p,new_body)
    assert client.get(second["download_url"]).status_code==200
    tamper(store,identity["tenant"],EVIDENCE_KIND,first_inventory["id"],
        lambda row:(row["rights"].update(export=False),row["effective_rights"].update(export=False)),recompute=True)
    rejected=client.get(second["download_url"])
    assert rejected.status_code==403 and rejected.json()["detail"]["code"]=="LICENCE_USE_BLOCKED"
    assert client.get(f"/api/projects/{p['id']}/qualification/review-packet").status_code==403


def test_rehashed_allocation_plan_cannot_promote_arithmetic_to_native_source_verification(setup):
    from api.phase_b import ALLOCATION_KIND
    client,store,identity=setup
    p,parent=seed(client)
    _,_,body=allocation_inputs(client,p,parent)
    allocation,child=apply_allocation(client,p,body)
    record=store.get(ALLOCATION_KIND,allocation["id"],identity["tenant"])
    payload=store.read_artifact(record["_artifact"],record["artifact_checksum"])
    payload["plan"]["native_source_assignment_verified"]=True
    payload["plan"]["content_hash"]=digest({key:value for key,value in payload["plan"].items() if key!="content_hash"})
    pointer,checksum=store.atomic_artifact(record["id"],2,payload)
    record.update(_artifact=pointer,artifact_checksum=checksum,plan_hash=payload["plan"]["content_hash"])
    record["summary"]["native_source_assignment_verified"]=True
    record["content_hash"]=digest(logical_record(record))
    child["source_allocation"]["allocation_content_hash"]=record["content_hash"]
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=?",(canonical(record),ALLOCATION_KIND,record["id"]))
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?",(canonical(child),child["id"]))
    response=client.get(record["download_url"] if "download_url" in record else allocation["download_url"])
    assert response.status_code==503 and response.json()["detail"]["code"]=="ARTIFACT_INTEGRITY_ERROR"


def test_building_edit_cannot_bury_road_support_while_leaving_midpoint_source_free(setup):
    client,_,_=setup
    p,parent=seed(client)
    _,_,body=allocation_inputs(client,p,parent)
    _,child=apply_allocation(client,p,body)
    source=child["sources"][0]
    building=child["buildings"][0]
    max_x=max(point[0] for point in building["footprint"])
    centre_y=(min(point[1] for point in building["footprint"])+max(point[1] for point in building["footprint"]))/2
    # Bury the left part of the original eight-metre road but preserve a free
    # midpoint. Ordinary point-only source checks cannot establish road support.
    changed=client.post(f"/api/scenarios/{child['id']}/operations",json={"expected_version":child["version"],
        "operations":[{"type":"building_update","object_id":building["id"],
            "changes":{"dx":source["x"]-1-max_x,"dy":source["y"]-centre_y}}]})
    assert changed.status_code==201,changed.text
    refused=prepare(client,p,changed.json())
    assert refused.status_code==409,refused.text
    assert refused.json()["detail"]["code"]=="ALLOCATION_BINDING_STALE"


def test_inventory_sources_cannot_escape_to_preview_through_quotes_or_marker_omission(setup):
    client,store,identity=setup
    p,parent=seed(client)
    inventory,_,body=allocation_inputs(client,p,parent)
    _,child=apply_allocation(client,p,body)
    conditions={"wind_speed_m_s":3,"wind_from_deg":270,"diffusivity_m2_s":2,"background_ug_m3":10,"duration_s":10}
    path=f"/api/projects/{p['id']}/quote"
    denied=client.post(path,json={"scenario_ids":[child["id"]],"conditions":conditions})
    assert denied.status_code==409 and denied.json()["detail"]["code"]=="INVENTORY_DETAILED_WORKFLOW_REQUIRED"
    assert "Use Detailed execution" in denied.json()["detail"]["message"]
    # A legitimate ordinary quote altered in this isolated test database cannot
    # bypass submission guards by naming an allocation-derived scenario later.
    ordinary=client.post(path,json={"scenario_ids":[parent["id"]],"conditions":conditions})
    assert ordinary.status_code==201,ordinary.text
    quote=ordinary.json()
    quote["scenario_ids"]=[child["id"]]
    child.pop("source_allocation")
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='quote' AND id=?",(canonical(quote),quote["id"]))
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?",(canonical(child),child["id"]))
    tamper(store,identity["tenant"],EVIDENCE_KIND,inventory["id"],
        lambda row:(row["rights"].update(solve=False,export=False),row["effective_rights"].update(solve=False,export=False)),recompute=True)
    submitted=client.post(f"/api/projects/{p['id']}/runs",json={"quote_id":quote["id"],"idempotency_key":"preview-bypass-denied"})
    assert submitted.status_code==409 and submitted.json()["detail"]["code"]=="INVENTORY_DETAILED_WORKFLOW_REQUIRED"
    assert store.list_runs(identity["tenant"],p["id"])==[]


def test_legacy_inventory_bound_preview_worker_and_result_report_exports_fail_closed(setup):
    from api.main import new_run
    from api.worker import work_once
    from api.storage import new_id,utc_now
    client,store,identity=setup
    p,parent=seed(client)
    inventory,_,body=allocation_inputs(client,p,parent)
    _,child=apply_allocation(client,p,body)
    conditions={"wind_speed_m_s":3,"wind_from_deg":270,"diffusivity_m2_s":2,"background_ug_m3":10,"duration_s":10}
    quote={"conditions":conditions,"maximum_seconds":120,"cases":1,"limitations":["Software denial fixture; no simulation"]}
    legacy=new_run(p["id"],parent,quote)
    legacy.update(scenario_id=child["id"])
    legacy["manifest"].update(inputs=deepcopy(child),scenario_id=child["id"],scenario_hash=child["content_hash"])
    with store.transaction() as db:
        Store.insert_run(db,identity["tenant"],legacy)
    called=[]
    def forbidden_simulate(*args,**kwargs):
        called.append(True)
        raise AssertionError("Inventory input must not reach the preview simulation")
    assert work_once(store,"legacy-scope-test",simulate_fn=forbidden_simulate)
    finished=store.run(legacy["id"],identity["tenant"])
    assert finished["status"]=="failed" and finished["error"]["code"]=="INVENTORY_DETAILED_WORKFLOW_REQUIRED"
    assert called==[] and not finished.get("_artifact")
    assert client.get(f"/api/runs/{legacy['id']}").status_code==409
    assert client.post(f"/api/runs/{legacy['id']}/retry").status_code==409
    compared=client.post(f"/api/projects/{p['id']}/compare",json={"baseline_run_id":legacy["id"],"alternative_run_id":legacy["id"]})
    assert compared.status_code==409 and compared.json()["detail"]["code"]=="INVENTORY_DETAILED_WORKFLOW_REQUIRED"
    # Retain a server-stored old report fixture containing bound input manifests
    # to test export authority. It contains no numeric result or actual solve.
    report={"id":new_id("report"),"project_id":p["id"],"created_at":utc_now(),
            "manifest":{"run_manifests":[legacy["manifest"]]},"_html":"PROTECTED_SOFTWARE_TEST_SENTINEL"}
    with store.transaction() as db:
        Store.put(db,"report",identity["tenant"],report,p["id"])
    tamper(store,identity["tenant"],EVIDENCE_KIND,inventory["id"],
        lambda row:(row["rights"].update(export=False),row["effective_rights"].update(export=False)),recompute=True)
    for suffix in ("","/download?format=json","/download?format=html"):
        response=client.get(f"/api/reports/{report['id']}{suffix}")
        assert response.status_code==409 and response.json()["detail"]["code"]=="INVENTORY_DETAILED_WORKFLOW_REQUIRED"
        assert "PROTECTED_SOFTWARE_TEST_SENTINEL" not in response.text and "run_manifests" not in response.text
