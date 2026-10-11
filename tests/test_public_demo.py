"""Deployment wrapper checks using real API persistence and scientific contracts."""
from dataclasses import replace
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from infra.public_demo import COOKIE, Limits, create_app
from science.fixtures import load_fixture

ORIGIN = "https://demo.example"
CONDITIONS = {"wind_speed_m_s": 2, "wind_from_deg": 270, "diffusivity_m2_s": 2,
              "background_ug_m3": 0, "duration_s": 10}


def app_at(tmp_path, **kwargs):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text('<html><body><div id="root"></div></body></html>', encoding="utf-8")
    (dist / "example.js").write_text("console.log('demo')", encoding="utf-8")
    return create_app(tmp_path / "data", dist, demo_origin=ORIGIN, start_worker=False, **kwargs)


def browser(app):
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def seed(client):
    created = client.post("/api/projects", json={"name": "Fictional test study"})
    assert created.status_code == 201, created.text
    project = created.json()
    imported = client.post(f"/api/projects/{project['id']}/imports", json={
        "filename": "fictional.geojson", "geojson": load_fixture(),
        "source_label": "Invented integration fixture", "rights": {"derive": True, "solve": True, "export": True}})
    assert imported.status_code == 201, imported.text
    return project, imported.json()["scenario"]


def test_cookie_isolation_and_native_permission_gates(tmp_path):
    app = app_at(tmp_path)
    with browser(app) as a, browser(app) as b:
        project, scenario = seed(a)
        assert COOKIE in a.cookies
        cookie = a.cookies.get(COOKIE)
        assert b.get("/api/projects").json() == []
        assert cookie != b.cookies.get(COOKIE)
        assert b.get(f"/api/projects/{project['id']}").status_code == 404
        assert b.get(f"/api/scenarios/{scenario['id']}").status_code == 404
        assert a.get(f"/api/scenarios/{scenario['id']}").status_code == 200
        assert a.get("/api/capabilities").json()["ai_available"] is False
        assert a.get(f"/api/projects/{project['id']}/assistant/status").json()["reason_code"] == "DEMO_RUNTIME_NOT_HOSTED"
        assert a.get(f"/api/projects/{project['id']}/detailed-executions/readiness").json()["available"] is False
        assert b.get(f"/api/projects/{project['id']}/assistant/status").status_code == 404


def test_cookie_security_tampering_origin_and_review_no_fallback(tmp_path):
    app = app_at(tmp_path)
    with browser(app) as client:
        response = client.get("/api/projects")
        header = response.headers["set-cookie"]
        for attribute in ["HttpOnly", "Secure", "SameSite=lax", "Path=/"]:
            assert attribute in header
        assert "Domain=" not in header
        token = client.cookies.get(COOKIE)
        denied = client.post("/api/projects", json={"name": "Wrong origin"}, headers={"Origin": "https://evil.example"})
        assert denied.status_code == 403
        denied = client.post("/api/projects", json={"name": "Same site is not same origin"}, headers={"Origin": "https://sub.demo.example"})
        assert denied.status_code == 403
        denied = client.post("/api/projects", json={"name": "Review bearer"}, headers={"Authorization": "Bearer uarv1_untrusted"})
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "REVIEW_SCOPE_DENIED"
        assert client.get("/api/projects").json() == []
        client.cookies.clear()
        client.cookies.set(COOKIE, token[:-1] + ("0" if token[-1] != "0" else "1"))
        assert client.get("/api/projects").status_code == 401


def test_resource_caps_and_real_fixture_quote(tmp_path):
    limits = replace(Limits(), projects_per_session=1, global_active=1)
    app = app_at(tmp_path, limits=limits)
    with browser(app) as client:
        project, scenario = seed(client)
        assert client.post("/api/projects", json={"name": "Excess"}).json()["detail"]["code"] == "DEMO_PROJECT_CAP"
        quote_body = {"scenario_ids": [scenario["id"]], "conditions": CONDITIONS, "tier": "T1"}
        quote = client.post(f"/api/projects/{project['id']}/quote", json=quote_body)
        assert quote.status_code == 201, quote.text
        assert quote.json()["preflight_grid_sizes"][0]["cells"] == 50400
        submission = {"quote_id": quote.json()["id"], "idempotency_key": "public-demo-test-run"}
        submitted = client.post(f"/api/projects/{project['id']}/runs", json=submission)
        assert submitted.status_code == 202, submitted.text
        # A second pending run cannot bypass the global cap through another browser.
        with browser(app) as second:
            project2, scenario2 = seed(second)
            quote2 = second.post(f"/api/projects/{project2['id']}/quote", json={**quote_body, "scenario_ids": [scenario2["id"]]})
            assert quote2.status_code == 201
            denied = second.post(f"/api/projects/{project2['id']}/runs", json={"quote_id": quote2.json()["id"], "idempotency_key": "another-demo-run"})
            assert denied.status_code == 429 and denied.json()["detail"]["code"] == "DEMO_RUN_CAP"
        assert client.post(f"/api/projects/{project['id']}/quote", json={**quote_body, "conditions": {**CONDITIONS, "duration_s": 600}}).status_code == 422
        assert client.post(f"/api/projects/{project['id']}/detailed-executions", json={}).status_code == 503
        assert client.post(f"/api/projects/{project['id']}/assistant/messages", json={}).status_code == 503


def test_limits_body_storage_session_and_static_api404(tmp_path):
    app = app_at(tmp_path, limits=replace(Limits(), sessions=1, body_bytes=100))
    with browser(app) as client:
        index = client.get("/projects/example")
        assert index.status_code == 200 and "Public research demo" in index.text
        assert "Native CFD and local AI are not hosted" in index.text
        assert "wasm-unsafe-eval" in index.headers["content-security-policy"]
        assert "https://tiles.openfreemap.org" in index.headers["content-security-policy"]
        assert client.get("/example.js").headers["content-type"].startswith("text/javascript")
        assert client.get("/api/does-not-exist").status_code == 404
        assert client.get("/api/health").json()["production_ready"] is False
        assert client.get("/api/ready").status_code == 200
        assert client.post("/api/projects", content=b"x" * 101).status_code == 413
        with browser(app) as second:
            assert second.get("/api/projects").json()["detail"]["code"] == "DEMO_SESSION_CAP"
    other = tmp_path / "storage"
    other.mkdir()
    app2 = app_at(other, limits=replace(Limits(), storage_bytes=10))
    with browser(app2) as client:
        assert client.post("/api/projects", json={"name": "Full"}).json()["detail"]["code"] == "DEMO_STORAGE_CAP"


def test_rate_cap_and_no_private_directory_adoption(tmp_path):
    app = app_at(tmp_path, limits=replace(Limits(), requests_per_minute=2))
    with browser(app) as client:
        assert client.get("/api/projects").status_code == 200
        assert client.get("/api/projects").status_code == 200
        denied = client.get("/api/projects")
        assert denied.status_code == 429 and denied.headers["retry-after"] == "60"
    private = tmp_path / "private"
    private.mkdir()
    (private / "studio.sqlite3").write_text("existing private data")
    with pytest.raises(ValueError, match="non-empty directory"):
        create_app(private, demo_origin=ORIGIN, start_worker=False)


def test_real_t1_worker_starts_and_commits_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_MODE", "local")
    app = app_at(tmp_path)
    # Use the same factory with the genuine daemon enabled for the end-to-end run.
    app = create_app(tmp_path / "worker-data", tmp_path / "dist", demo_origin=ORIGIN)
    with browser(app) as client:
        project, scenario = seed(client)
        quote = client.post(f"/api/projects/{project['id']}/quote", json={"scenario_ids": [scenario["id"]], "conditions": CONDITIONS})
        assert quote.status_code == 201, quote.text
        submitted = client.post(f"/api/projects/{project['id']}/runs", json={"quote_id": quote.json()["id"], "idempotency_key": "daemon-worker-run"})
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["runs"][0]["id"]
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status = client.get(f"/api/runs/{run_id}").json()
            if status["status"] in {"complete_unqualified", "failed", "cancelled"}:
                break
            time.sleep(0.5)
        assert status["status"] == "complete_unqualified", status
        assert status["result"]["qualification"] == "research_preview"
        assert status["result"]["diagnostics"]["numerical_verification_passed"] is True
        assert app.state.demo_worker.is_alive()
    assert not app.state.demo_worker.is_alive()
