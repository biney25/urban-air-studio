"""Deterministic security/admission tests; actual model acceptance is a separate receipt."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from api.main import create_app, local_identity, get_store, required, project_resource, compare
from api.auth import Identity
from api.assistant import make_router, LocalModel, LocalModelUnavailable, MODEL, LIMITS, _GATE
from api.assistant_tools import supported_answer
from api.planning import validate_programme
from science.fixtures import load_fixture


class TestOnlyModel:
    available = True
    handler = None
    def status(self):
        return {"available": self.available, "state": "ready" if self.available else "unavailable", "label": "Study assistant", "model": MODEL, "limits": LIMITS, "message": "Test-only model", "qualified": False}
    def infer(self, message, history, tools):
        if self.handler:
            return self.handler(message, history, tools)
        tools.invoke("inspect_study", {})
        return {"answer": "The saved study is a research preview. Review its blockers before continuing.", "usage": {}}


@pytest.fixture
def setup(monkeypatch):
    directory = Path(__file__).resolve().parents[1] / "tmp" / ("assistant-test-" + uuid.uuid4().hex)
    model = TestOnlyModel()
    monkeypatch.setattr("api.assistant.LocalModel", lambda: model)
    app = create_app(directory)
    principal = {"tenant": "assistant-a", "user": "reviewer-a"}
    app.dependency_overrides[local_identity] = lambda: Identity(principal["tenant"], principal["user"])
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Assistant admission test", "category": "internal_test"}).json()
        response = client.post(f"/api/projects/{project['id']}/imports", json={"filename": "fixture.geojson", "geojson": load_fixture(), "rights": {"derive": True, "solve": True, "export": True}})
        assert response.status_code == 201, response.text
        scenario = response.json()["scenario"]
        yield client, app.state.store, principal, project, scenario, model


def send(setup, **values):
    client, store, principal, project, scenario, model = setup
    return client.post(f"/api/projects/{project['id']}/assistant/messages", json={"message": "Inspect blockers", "scenario_id": scenario["id"], **values})


def test_session_history_is_durable_tenant_project_and_user_scoped(setup):
    client, store, principal, project, scenario, model = setup
    reply = send(setup).json()
    history = f"/api/projects/{project['id']}/assistant/sessions/{reply['session_id']}"
    assert client.get(history).json()["messages"][0]["reply"] == reply
    assert store.list("assistant_message", principal["tenant"], project["id"])[0]["usage"] == {}
    principal["user"] = "foreign-user"
    assert client.get(history).status_code == 404
    principal["user"] = "reviewer-a"
    principal["tenant"] = "foreign-tenant"
    assert client.get(history).status_code == 404
    assert send(setup).status_code == 404


@pytest.mark.parametrize("field,value", [("scenario_id", "scenario_foreign"), ("baseline_run_id", "run_foreign"), ("alternative_run_id", "run_foreign"), ("session_id", "asession_foreign")])
def test_foreign_identifiers_fail_before_inference(setup, field, value):
    _, _, _, _, _, model = setup
    model.handler = lambda *args: pytest.fail("Foreign identity reached model")
    assert send(setup, **{field: value}).status_code == 404


def test_unavailable_and_timeout_are_honest_and_durable(setup):
    _, store, principal, project, _, model = setup
    model.available = False
    response = send(setup)
    assert response.status_code == 200 and response.json()["status"] == "unavailable"
    assert response.json()["tool_calls"] == [] and response.json()["proposal"] is None
    model.available = True
    def timeout(*args):
        raise LocalModelUnavailable("LOCAL_MODEL_TIMEOUT")
    model.handler = timeout
    assert send(setup).json()["status"] == "unavailable"
    assert len(store.list("assistant_message", principal["tenant"], project["id"])) == 2


def test_busy_model_does_not_start_parallel_inference(setup):
    *_, model = setup
    model.handler = lambda *args: pytest.fail("Parallel inference started")
    _GATE.acquire()
    try:
        assert send(setup).json()["status"] == "unavailable"
    finally:
        _GATE.release()


def test_sdk_budget_stop_cannot_be_reported_as_completed(setup):
    *_, model = setup
    def infer(message, history, tools):
        tools.invoke("inspect_study", {})
        return {"answer": "The study inspection is unfinished.", "stop_reason": "limit_turns"}
    model.handler = infer
    reply = send(setup).json()
    assert reply["status"] == "blocked" and reply["tool_calls"]


@pytest.mark.parametrize("answer", ["AQI is 120", "Concentration decreased 20 percent", "See run_foreign", "https://attacker.test", "The air is safe air", "<script>alert(1)</script>"])
def test_unsupported_metrics_foreign_evidence_and_injection_are_blocked(setup, answer):
    *_, model = setup
    def infer(message, history, tools):
        tools.invoke("inspect_study", {})
        return {"answer": answer}
    model.handler = infer
    reply = send(setup).json()
    assert reply["status"] == "blocked", reply


def test_proposal_never_mutates_and_unconfigured_programme_fails_closed(setup):
    client, store, principal, project, scenario, model = setup
    before = deepcopy(store.get("scenario", scenario["id"], principal["tenant"]))
    def infer(message, history, tools):
        tools.invoke("read_geometry", {})
        tools.invoke("propose_change", {"object_id": scenario["sources"][0]["id"], "change": "rate_g_s", "value": scenario["sources"][0]["rate_g_s"] * .9})
        return {"answer": "This proposal requires constraint review. Physical benefit is not established."}
    model.handler = infer
    reply = send(setup).json()
    assert reply["proposal"]["requires_confirmation"] is True
    assert reply["proposal"]["validation"]["valid"] is False
    assert reply["proposal"]["validation"]["programme_status"] == "unconfigured"
    assert store.get("scenario", scenario["id"], principal["tenant"]) == before
    assert len(store.list("scenario", principal["tenant"], project["id"])) == 1


def test_actual_constraint_service_validates_proposal(setup):
    client, store, principal, project, scenario, model = setup
    assert client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision": 0, "question": "Retain capacity", "storey_height_m": 3, "min_floor_area_m2": 1}).status_code == 201
    def infer(message, history, tools):
        tools.invoke("read_geometry", {})
        tools.invoke("propose_change", {"object_id": scenario["sources"][0]["id"], "change": "rate_g_s", "value": scenario["sources"][0]["rate_g_s"] * .9})
        return {"answer": "The proposed source change passes declared constraints and awaits your review."}
    model.handler = infer
    assert send(setup).json()["proposal"]["validation"]["valid"] is True


@pytest.mark.parametrize("arguments", [{"object_id": "foreign", "change": "height_m", "value": 20}, {"object_id": "b1", "change": "shell", "value": 1}])
def test_invalid_objects_and_parameter_names_cannot_create_proposal(setup, arguments):
    *_, model = setup
    def infer(message, history, tools):
        result = tools.invoke("propose_change", arguments)
        assert result["blocked"] is True
        return {"answer": "The proposed operation is blocked by the selected study's rules."}
    model.handler = infer
    reply = send(setup).json()
    assert reply["proposal"] is None and reply["tool_calls"][0]["status"] == "blocked"


def test_shell_and_arbitrary_http_tools_do_not_exist(setup):
    *_, model = setup
    def infer(message, history, tools):
        for name in ("shell", "http", "sql", "run_simulation"):
            with pytest.raises(ValueError):
                tools.invoke(name, {})
        tools.invoke("inspect_study", {})
        return {"answer": "Only the fixed study tools are available."}
    model.handler = infer
    assert send(setup).json()["status"] == "complete"


def test_real_cross_project_snapshot_is_rejected_before_model(setup):
    client, store, principal, project, scenario, model = setup
    other = client.post("/api/projects", json={"name": "Other owned study"}).json()
    imported = client.post(f"/api/projects/{other['id']}/imports", json={"filename": "other.geojson", "geojson": load_fixture(), "rights": {"derive": True, "solve": True, "export": True}}).json()["scenario"]
    model.handler = lambda *args: pytest.fail("Foreign project reached model")
    assert send(setup, scenario_id=imported["id"]).status_code == 404


def test_cedar_denied_derive_and_corrupt_snapshot_fail_closed(setup):
    from api.storage import canonical
    client, store, principal, project, scenario, model = setup
    stored = store.get("scenario", scenario["id"], principal["tenant"])
    stored["source_rights"]["derive"] = False
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?", (canonical(stored), scenario["id"]))
    def infer(message, history, tools):
        result = tools.invoke("read_geometry", {})
        assert result["blocked"] and tools.proposal is None
        return {"answer": "The geometry operation is blocked by the selected study's permission or integrity rules."}
    model.handler = infer
    reply = send(setup).json()
    assert reply["tool_calls"][0]["status"] == "blocked" and reply["evidence"] == []
    stored["source_rights"]["derive"] = True
    stored["content_hash"] = "sha256:" + "0" * 64
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?", (canonical(stored), scenario["id"]))
    assert send(setup).json()["tool_calls"][0]["status"] == "blocked"


def test_infeasible_capacity_is_visible_in_reviewed_proposal(setup):
    client, store, principal, project, scenario, model = setup
    assert client.post(f"/api/projects/{project['id']}/planning-brief", json={"expected_revision": 0, "question": "Declared impossible capacity", "storey_height_m": 3, "min_floor_area_m2": 1000000}).status_code == 201
    def infer(message, history, tools):
        tools.invoke("propose_change", {"object_id": scenario["sources"][0]["id"], "change": "rate_g_s", "value": .001})
        return {"answer": "The proposed edit fails the declared capacity requirement and cannot be applied."}
    model.handler = infer
    validation = send(setup).json()["proposal"]["validation"]
    assert validation["valid"] is False and validation["programme_status"] == "fail"
    assert any(issue["code"] == "CAPACITY_BELOW_REQUIREMENT" for issue in validation["issues"])


def test_tool_call_allowance_and_unchanged_edits_are_enforced(setup):
    *_, scenario, model = setup
    def infer(message, history, tools):
        result = tools.invoke("propose_change", {"object_id": scenario["sources"][0]["id"], "change": "rate_g_s", "value": scenario["sources"][0]["rate_g_s"]})
        assert result["blocked"] and tools.proposal is None
        for _ in range(5):
            tools.invoke("inspect_study", {})
        with pytest.raises(ValueError, match="allowance"):
            tools.invoke("inspect_study", {})
        return {"answer": "The unchanged edit was blocked. The tool allowance is exhausted."}
    model.handler = infer
    assert len(send(setup).json()["tool_calls"]) == 6


def test_session_limit_prevents_more_model_calls(setup):
    *_, model = setup
    first = send(setup).json()
    for _ in range(19):
        assert send(setup, session_id=first["session_id"]).status_code == 200
    model.handler = lambda *args: pytest.fail("Session cap did not stop inference")
    assert send(setup, session_id=first["session_id"]).status_code == 429


def test_subprocess_timeout_kills_child_and_strips_credentials(monkeypatch):
    original = subprocess.Popen
    children = []
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-must-not-cross")
    monkeypatch.setenv("UA_DATABASE_URL", "test-private-store")
    def spawn(args, **options):
        assert "AWS_SECRET_ACCESS_KEY" not in options["env"] and "UA_DATABASE_URL" not in options["env"]
        child = original([sys.executable, "-c", "import time; time.sleep(60)"], **options)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, "Popen", spawn)
    started = time.monotonic()
    with pytest.raises(LocalModelUnavailable, match="TIMEOUT"):
        LocalModel().infer("Inspect", [], None, timeout=.2)
    assert time.monotonic()-started < 5 and children[0].poll() is not None
