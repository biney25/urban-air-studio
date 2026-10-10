"""Real native Cedar decisions, policy tamper and authoritative API failure gates."""
from copy import deepcopy
import shutil
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from api.auth import Identity
from api import authorization
from api.authorization import Authorizer, POLICY_DIRECTORY
from api.phase_b import CASE_KIND, EVIDENCE_KIND
from test_phase_b_api import setup, project, request, save, replay_chain, assessment, seed_restricted, prepare, tamper

RIGHTS={"derive":True,"solve":True,"export":True}


def evaluate(authorizer, **overrides):
    values=dict(identity=Identity("tenant-a","planner"),project_id="project-a",action="export",
                identifier="asset-a",tenant_id="tenant-a",resource_project_id="project-a",rights=RIGHTS)
    values.update(overrides)
    return authorizer.evaluate(**values)


@pytest.mark.parametrize("action",["derive","solve","export"])
def test_real_native_engine_allows_only_explicit_matching_permission(action):
    authorizer=Authorizer()
    assert authorizer.status()["available"]
    assert evaluate(authorizer,action=action)=="allowed"
    rights={**RIGHTS,action:False}
    assert evaluate(authorizer,action=action,rights=rights)=="denied"


@pytest.mark.parametrize("overrides",[
    {"tenant_id":"another-tenant"}, {"resource_project_id":"another-project"},
    {"action":"arbitrary-shell-command"}, {"identifier":""}, {"tenant_id":None},
    {"rights":{"derive":True,"solve":True}}, {"rights":{**RIGHTS,"export":"true"}},
    {"rights":{**RIGHTS,"export":1}}, {"rights":{**RIGHTS,"unknown":True}},
])
def test_scope_unknown_action_and_malformed_entities_never_allow(overrides):
    assert evaluate(Authorizer(),**overrides)!="allowed"


@pytest.mark.parametrize("filename",["phase_b.cedar","phase_b.cedarschema","cedar-provenance.json"])
def test_loaded_policy_contract_tamper_disables_gate(tmp_path,filename):
    folder=tmp_path/"policy"
    shutil.copytree(POLICY_DIRECTORY,folder)
    authorizer=Authorizer(folder)
    assert evaluate(authorizer)=="allowed"
    with (folder/filename).open("ab") as stream:
        stream.write(b"\nchanged")
    assert evaluate(authorizer)=="unavailable"
    assert authorizer.status()["state"]=="unavailable_fail_closed"


def test_missing_package_and_missing_policy_fail_closed(tmp_path,monkeypatch):
    assert evaluate(Authorizer(tmp_path))=="unavailable"
    def missing(_):
        raise ImportError("unavailable")
    monkeypatch.setattr(authorization,"import_module",missing)
    assert evaluate(Authorizer())=="unavailable"


def test_explicit_forbid_overrides_a_real_permit():
    authorizer=Authorizer()
    policy=(POLICY_DIRECTORY/"phase_b.cedar").read_text()
    authorizer.policy_set=authorizer.engine.PolicySet.from_str(policy+"\nforbid(principal, action, resource) when {true};")
    assert evaluate(authorizer)=="denied"


def test_actual_cedar_allow_with_error_diagnostics_is_rejected():
    authorizer=Authorizer()
    policy=(POLICY_DIRECTORY/"phase_b.cedar").read_text()
    # An unknown attribute in a forbid is skipped by Cedar. Native raw Allow
    # must still be blocked by the application's deny-on-diagnostics wrapper.
    authorizer.policy_set=authorizer.engine.PolicySet.from_str(policy+"\nforbid(principal, action, resource) when {resource.nonexistent};")
    original=authorizer.engine.is_authorized
    def raw_without_schema(*args,**kwargs):
        kwargs.pop("schema",None)
        result=original(*args,**kwargs)
        assert result.decision==authorizer.engine.Decision.Allow and result.diagnostics.errors
        return result
    # Override only this instance's adapter; keep the real native evaluator.
    authorizer.engine=SimpleNamespace(**{name:getattr(authorizer.engine,name) for name in ("Decision",)},is_authorized=raw_without_schema)
    assert evaluate(authorizer)=="error"


def test_engine_exception_returns_sanitized_503_proof(monkeypatch):
    authorizer=Authorizer()
    def fail(*_args,**_kwargs):
        raise RuntimeError("private entity/token/path must not appear")
    authorizer.engine=SimpleNamespace(Decision=authorizer.engine.Decision,is_authorized=fail)
    with pytest.raises(HTTPException) as caught:
        authorizer.check().require(Identity("tenant-a","planner"),"project-a",action="export",
            identifier="asset-a",tenant_id="tenant-a",resource_project_id="project-a",rights=RIGHTS)
    assert caught.value.status_code==503
    proof=caught.value.detail["authorization"]
    assert proof["decision"]=="Deny" and proof["reason"]=="authorization_error"
    assert "private" not in str(caught.value.detail)
    assert caught.value.headers["X-Authorization-Decision-ID"]==proof["decision_id"]


def test_actual_readiness_and_permitted_export_expose_policy_proof(setup):
    client,_,_=setup
    p=project(client)
    record=save(client,p,request("inventory"))
    method=client.get(f"/api/projects/{p['id']}/qualification").json()["authorization_method"]
    assert method["available"] and method["local_evaluation"] and method["cloud_service"] is False
    response=client.get(record["download_url"])
    assert response.status_code==200
    assert response.headers["x-authorization-engine"]=="cedar-policy/4.12.0"
    assert response.headers["x-authorization-policy-sha256"]==method["policy_sha256"]


def test_denied_candidate_stops_processing_and_storage(setup,monkeypatch):
    client,store,identity=setup
    p=project(client)
    import api.phase_b
    def forbidden_processing(*_args):
        pytest.fail("Permission denial must happen before processing")
    monkeypatch.setattr(api.phase_b,"process_evidence",forbidden_processing)
    body=request("inventory"); body["rights"]["derive"]=False
    response=client.post(f"/api/projects/{p['id']}/evidence",json=body)
    assert response.status_code==403
    assert response.json()["detail"]["authorization"]["decision"]=="Deny"
    assert store.list(EVIDENCE_KIND,identity["tenant"],p["id"])==[]


def test_unavailable_gate_preserves_owned_views_but_blocks_mutation(setup):
    client,store,identity=setup
    p=project(client)
    client.app.state.authorization.available=False
    response=client.post(f"/api/projects/{p['id']}/evidence",json=request("inventory"))
    assert response.status_code==503 and response.json()["detail"]["code"]=="AUTHORIZATION_UNAVAILABLE"
    assert store.list(EVIDENCE_KIND,identity["tenant"],p["id"])==[]
    assert client.get(f"/api/projects/{p['id']}/qualification").status_code==200


@pytest.mark.parametrize("action",["derive","solve"])
def test_case_permission_denial_prevents_bundle_publication(setup,action):
    client,store,identity=setup
    p,scenario=seed_restricted(client,store,identity["tenant"],{**RIGHTS,action:False})
    response=prepare(client,p,scenario)
    assert response.status_code==403 and response.json()["detail"]["authorization"]["action"]==action
    assert store.list(CASE_KIND,identity["tenant"],p["id"])==[]
    assert not list((store.directory/"artifacts").rglob("*"))


def test_each_frozen_dependency_is_authorized_independently(setup,monkeypatch):
    client,_,_=setup
    p=project(client)
    campaign,protocol=replay_chain(client,p)
    record=save(client,p,assessment(campaign,protocol))
    original=client.app.state.authorization.evaluate
    calls=[]
    def actual(*args,**kwargs):
        calls.append((kwargs["identifier"],kwargs["action"]))
        return original(*args,**kwargs)
    monkeypatch.setattr(client.app.state.authorization,"evaluate",actual)
    assert client.get(record["download_url"]).status_code==200
    assert set(calls)=={(r["id"],"export") for r in (campaign,protocol,record)}


def test_forged_aggregate_rights_cannot_launder_restricted_parent(setup):
    client,store,identity=setup
    p=project(client)
    _,protocol=replay_chain(client,p,{**RIGHTS,"export":False})
    # Simulate an internally miscomputed aggregate with a valid record hash.
    # The independently resolved frozen campaign must still forbid export.
    tamper(store,identity["tenant"],EVIDENCE_KIND,protocol["id"],
        lambda r:r["effective_rights"].update(export=True),recompute=True)
    response=client.get(protocol["download_url"])
    assert response.status_code==403 and response.json()["detail"]["authorization"]["decision"]=="Deny"


def test_wrong_project_remains_404_before_policy_evaluation(setup,monkeypatch):
    client,_,_=setup
    p=project(client); other=project(client)
    record=save(client,p,request("inventory"))
    def never(*_args,**_kwargs):
        pytest.fail("Unscoped record must not reach policy evaluator")
    monkeypatch.setattr(client.app.state.authorization,"evaluate",never)
    response=client.get(f"/api/projects/{other['id']}/evidence/{record['id']}/download")
    assert response.status_code==404 and "authorization" not in response.json()["detail"]
