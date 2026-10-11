"""Source-containing downloads and report rendering always recheck authority."""
import pytest
from fastapi.testclient import TestClient
from api.main import create_app, local_identity, scenario_version
from api.auth import Identity
from api.storage import canonical
from api.worker import work_once
from science.fixtures import load_fixture


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path)
    identity = {"tenant":"export-a"}
    app.dependency_overrides[local_identity] = lambda: Identity(identity["tenant"], "planner")
    with TestClient(app) as client:
        yield client, app.state.store, identity


def seed(client, store, export=True):
    project = client.post("/api/projects", json={"name":"Report rights trial", "planning_question":"Does the alternative change the declared tracer trial?"}).json()
    response = client.post(f"/api/projects/{project['id']}/imports", json={"filename":"illustrative.geojson", "geojson":load_fixture(), "rights":{"derive":True,"solve":True,"export":True}})
    assert response.status_code == 201, response.text
    scenario = response.json()["scenario"]
    if not export:
        # The import route deliberately rejects incomplete rights. Persist an
        # authoritative restricted fixture with its own immutable identity to
        # exercise export enforcement, rather than bypassing import validation.
        scenario = scenario_version({**scenario, "source_rights":{"derive":True,"solve":True,"export":False}}, project["id"], "Restricted source fixture")
        with store.transaction() as db:
            store.put(db, "scenario", "export-a", scenario, project["id"])
    return project, scenario


def actual_pair(client, store, project, scenario):
    alternative = client.post(f"/api/scenarios/{scenario['id']}/branch", json={"name":"Source-control trial"}).json()
    edited = client.post(f"/api/scenarios/{alternative['id']}/operations", json={"expected_version":alternative["version"],"operations":[{"type":"source_update","object_id":alternative["sources"][0]["id"],"changes":{"rate_g_s":alternative["sources"][0]["rate_g_s"]*.5}}]}).json()
    quote = client.post(f"/api/projects/{project['id']}/quote", json={"scenario_ids":[scenario["id"],edited["id"]], "conditions":{"wind_speed_m_s":2,"wind_from_deg":270,"diffusivity_m2_s":2,"background_ug_m3":0,"duration_s":10}}).json()
    runs = client.post(f"/api/projects/{project['id']}/runs", json={"quote_id":quote["id"],"idempotency_key":"research-export-test"}).json()["runs"]
    for _ in runs:
        assert work_once(store)
    return {"baseline_run_id":runs[0]["id"],"alternative_run_id":runs[1]["id"]}


def test_restricted_source_export_denied_by_actual_cedar(setup):
    client, store, _ = setup
    _, scenario = seed(client, store, False)
    response = client.get(f"/api/scenarios/{scenario['id']}/export")
    assert response.status_code == 403
    assert response.json()["detail"]["authorization"]["decision"] == "Deny"


def test_full_export_is_scoped_and_detects_tampering(setup):
    client, store, identity = setup
    _, scenario = seed(client, store)
    response = client.get(f"/api/scenarios/{scenario['id']}/export")
    assert response.status_code == 200 and response.json()["scenario"]["content_hash"] == scenario["content_hash"]
    assert response.json()["qualified"] is False and response.headers["X-Authorization-Decision-ID"]
    identity["tenant"] = "other"
    assert client.get(f"/api/scenarios/{scenario['id']}/export").status_code == 404
    identity["tenant"] = "export-a"
    raw = store.get("scenario", scenario["id"], identity["tenant"])
    raw["source_rights"]["export"] = False
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='scenario' AND id=?", (canonical(raw), raw["id"]))
    assert client.get(f"/api/scenarios/{scenario['id']}/export").status_code == 503


def test_real_run_bundle_readable_report_library_and_tamper_checks(setup):
    client, store, identity = setup
    project, scenario = seed(client, store)
    pair = actual_pair(client, store, project, scenario)
    exported = client.get(f"/api/runs/{pair['baseline_run_id']}/export")
    assert exported.status_code == 200 and exported.json()["result"]["qualification"] == "research_preview"
    response = client.post(f"/api/projects/{project['id']}/reports", json={**pair,"title":"Readable trial <script>alert(1)</script>"})
    assert response.status_code == 201, response.text
    report = response.json()
    assert client.get(f"/api/projects/{project['id']}/reports").json()[0]["checksum"] == report["checksum"]
    downloaded = client.get(f"/api/reports/{report['id']}/download?format=html")
    assert downloaded.status_code == 200
    html = downloaded.text
    assert "Planning question" in html and "Neighbour check" in html and "inconclusive" in html
    assert "What changed" in html and "rate_g_s" in html and "<svg" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "data:font/woff2;base64" in html and "font-src data:" in downloaded.headers["Content-Security-Policy"]
    raw = store.get("report", report["id"], identity["tenant"])
    raw["_html"] += "tampered"
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind='report' AND id=?", (canonical(raw), raw["id"]))
    assert client.get(f"/api/reports/{report['id']}/download?format=html").status_code == 503


def test_report_cannot_bypass_restricted_input_or_frozen_run_integrity(setup):
    client, store, _ = setup
    project, scenario = seed(client, store, False)
    pair = actual_pair(client, store, project, scenario)
    assert client.post(f"/api/projects/{project['id']}/reports", json=pair).status_code == 403
    assert client.get(f"/api/runs/{pair['baseline_run_id']}/export").status_code == 403
