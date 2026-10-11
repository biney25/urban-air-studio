"""Capability isolation and immutable review state; no scientific qualification."""
import hashlib
import threading
import time

from fastapi.testclient import TestClient
import pytest

from api.auth import Identity
from api.main import create_app, error, checked_report, get_store, local_identity, required
from api.report_review import GRANT_KIND, KEY_KIND, COMMENT_KIND, make_router, TOKEN_PATTERN
from api.storage import canonical, digest, Store
from science.fixtures import load_fixture


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path)
    identity = {"tenant": "review-owner", "user": "private-owner"}
    app.dependency_overrides[local_identity] = lambda: Identity(identity["tenant"], identity["user"])
    # Allows this narrow suite to run before parent/router wiring is saved.
    if not any(getattr(route, "path", "") == "/api/review/report" for route in app.routes):
        app.include_router(make_router(identity_dependency=local_identity, store_dependency=get_store,
            required=required, error=error, checked_report=checked_report))
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "Explicit review fixture"}).json()
        imported = client.post(f"/api/projects/{project['id']}/imports", json={"filename": "fixture.geojson",
            "geojson": load_fixture(), "rights": {"derive": True, "solve": True, "export": True}})
        assert imported.status_code == 201, imported.text
        scenario = imported.json()["scenario"]
        # This fixture exercises the real frozen-report/export/Cedar gate, with
        # no physical output or invented improvement. Actual result generation
        # is already covered by retained research-export integration evidence.
        report = {"id": "report_review_fixture", "project_id": project["id"], "title": "Illustrative report fixture",
            "created_at": "2026-10-11T00:00:00+00:00", "status": "draft", "limitations": ["No scientific result in this fixture"],
            "comparison": {"metrics": [{"receptor_id": scenario["receptors"][0]["id"], "name": "Fixture assessment", "valid": False}]},
            "manifest": {"planning_question": "Review an explicit fixture", "run_manifests": [{"inputs": scenario}]}}
        report["checksum"] = digest(report)
        report["_html"] = "<!doctype html><html><title>Illustrative fixture</title><p>No scientific result in this fixture.</p></html>"
        report["_html_sha256"] = hashlib.sha256(report["_html"].encode()).hexdigest()
        with app.state.store.transaction() as db:
            Store.put(db, "report", identity["tenant"], report, project["id"])
        yield client, app, app.state.store, identity, project, report


def owner_path(project, report):
    return f"/api/projects/{project['id']}/reports/{report['id']}"


def grant(setup, **fields):
    client, _, _, _, project, report = setup
    response = client.post(owner_path(project, report) + "/review-grants", json=fields)
    assert response.status_code == 201, response.text
    result = response.json()
    assert TOKEN_PATTERN.fullmatch(result["token"])
    return result, {"Authorization": "Bearer " + result["token"]}


def replace(store, kind, tenant, item):
    with store.transaction() as db:
        db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=?",
                   (canonical(item), kind, item["id"], tenant))


def test_mint_once_digest_storage_and_minimal_read(setup):
    client, _, store, identity, project, report = setup
    created, headers = grant(setup, label="Named fixture reviewer")
    second, _ = grant(setup)
    assert second["token"] != created["token"]
    listed = client.get(owner_path(project, report) + "/review-grants")
    assert listed.status_code == 200
    assert all("token" not in row and "token_digest" not in row and "owner_user_id" not in row for row in listed.json())
    with store.connect() as db:
        rows = db.execute("SELECT data FROM objects WHERE kind IN (?,?)", (GRANT_KIND, KEY_KIND)).fetchall()
    assert created["token"] not in str(rows)
    assert all(created["token"] not in row[0] for row in rows)
    read = client.get("/api/review/report", headers=headers)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["report"]["checksum"] == report["checksum"]
    assert body["report"]["planning_question"] == "Review an explicit fixture"
    assert body["report"]["source_notices"] == []
    assert body["permissions"] == {"read_report": True, "comment": True, "download_html": True}
    for private in (identity["tenant"], identity["user"], "source_rights", "run_manifests", "token_digest", '"_html"', str(store.directory)):
        assert private not in read.text
    downloaded = client.get("/api/review/report/download", headers=headers)
    assert downloaded.status_code == 200 and downloaded.text == report["_html"]
    assert "no-store" in downloaded.headers["cache-control"]
    assert "default-src 'none'" in downloaded.headers["content-security-policy"]


def test_recipient_never_falls_back_to_owner_or_url_credentials(setup):
    client, _, _, _, _, _ = setup
    created, _ = grant(setup)
    for path in ("/api/review/report", "/api/review/report/comments", "/api/review/report/download"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer owner-jwt"}).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer " + created["token"] + "X"}).status_code == 401
        assert client.get(path + "?token=" + created["token"]).status_code == 400
        assert created["token"] not in client.get(path + "?token=" + created["token"]).text
    assert client.post("/api/review/report/comments", json={"report_checksum": "sha256:" + "0" * 64,
        "anchor": {"kind": "report"}, "body": "Missing credential"}).status_code == 401


def test_review_credential_cannot_mutate_or_escape_into_normal_api(setup):
    client, app, store, identity, project, report = setup
    _, headers = grant(setup)
    before = len(store.list("project", identity["tenant"]))
    # The parent middleware/identity guard must reject recognised review tokens
    # before the local owner identity override used by this API fixture.
    blocked = [client.get("/api/projects", headers=headers),
        client.post("/api/projects", headers=headers, json={"name": "Forbidden reviewer edit"}),
        client.get(f"/api/reports/{report['id']}", headers=headers),
        client.get(f"/api/reports/{report['id']}/download", headers=headers),
        client.post(f"/api/projects/{project['id']}/quote", headers=headers, json={}),
        client.post(f"/api/projects/{project['id']}/runs", headers=headers, json={}),
        client.post(f"/api/projects/{project['id']}/assistant/messages", headers=headers, json={}),
        client.post(f"/api/scenarios/{report['manifest']['run_manifests'][0]['inputs']['id']}/branch", headers=headers, json={"name": "Forbidden branch"}),
        client.get(f"/api/scenarios/{report['manifest']['run_manifests'][0]['inputs']['id']}/export", headers=headers),
        client.post(owner_path(project, report) + "/review-grants", headers=headers, json={})]
    assert all(response.status_code == 403 for response in blocked), [r.status_code for r in blocked]
    assert len(store.list("project", identity["tenant"])) == before
    assert client.get("/api/review/reports/another", headers=headers).status_code in {403, 404}
    assert client.get("/api/review/report?report_id=another", headers=headers).status_code == 400
    assert client.get("/api/review/report/download?format=json", headers=headers).status_code == 400


def test_expiry_and_revocation_are_checked_on_every_operation(setup):
    client, _, store, identity, project, report = setup
    created, headers = grant(setup, ttl_seconds=60)
    raw = store.get(GRANT_KIND, created["id"], identity["tenant"])
    # The stored immutable grant time is authoritative, not a client timestamp.
    raw["expires_epoch"] = 1
    raw["expires_at"] = "1970-01-01T00:00:01+00:00"
    raw["content_hash"] = digest({k: v for k, v in raw.items() if k != "content_hash"})
    replace(store, GRANT_KIND, identity["tenant"], raw)
    for path in ("/api/review/report", "/api/review/report/comments", "/api/review/report/download"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403 and response.json()["detail"]["code"] == "REVIEW_EXPIRED"
    comment = {"report_checksum": report["checksum"], "anchor": {"kind": "report"}, "body": "Expired"}
    assert client.post("/api/review/report/comments", headers=headers, json=comment).status_code == 403
    created, headers = grant(setup)
    revoke = owner_path(project, report) + f"/review-grants/{created['id']}/revoke"
    response = client.post(revoke)
    assert response.status_code == 200 and response.json()["state"] == "revoked"
    assert client.post(revoke).json()["revoked_at"] == response.json()["revoked_at"]
    for path in ("/api/review/report", "/api/review/report/comments", "/api/review/report/download"):
        assert client.get(path, headers=headers).json()["detail"]["code"] == "REVIEW_REVOKED"
    assert client.post("/api/review/report/comments", headers=headers, json=comment).status_code == 403


def test_owner_scope_and_foreign_report_cannot_manage_grants(setup):
    client, _, _, identity, project, report = setup
    created, headers = grant(setup)
    other_project = client.post("/api/projects", json={"name": "Another owned project"}).json()
    foreign_path = owner_path(other_project, report)
    assert client.post(foreign_path + "/review-grants", json={}).status_code == 404
    assert client.get(foreign_path + "/review-grants").status_code == 404
    assert client.post(foreign_path + f"/review-grants/{created['id']}/revoke").status_code == 404
    identity["tenant"] = "foreign-owner"
    assert client.get(owner_path(project, report) + "/review-grants").status_code == 404
    assert client.post(owner_path(project, report) + f"/review-grants/{created['id']}/revoke").status_code == 404
    # The recipient capability is scoped by its checked server grant, never by
    # a caller's owner identity override or a URL tenant/project parameter.
    assert client.get("/api/review/report", headers=headers).status_code == 200


@pytest.mark.parametrize("kind", [GRANT_KIND, KEY_KIND])
def test_tampered_grant_or_token_index_fails_closed(setup, kind):
    client, _, store, identity, _, _ = setup
    created, headers = grant(setup)
    raw = store.list(kind, identity["tenant"])[0]
    raw["report_id"] = "foreign-report"
    replace(store, kind, identity["tenant"], raw)
    response = client.get("/api/review/report", headers=headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "REVIEW_INTEGRITY_ERROR"
    assert created["token"] not in response.text


@pytest.mark.parametrize("mutation", ["hash", "html", "permissions"])
def test_report_hash_html_and_real_cedar_permissions_rechecked(setup, mutation):
    from api.main import physical_hash
    client, _, store, identity, project, report = setup
    _, headers = grant(setup)
    raw = store.get("report", report["id"], identity["tenant"])
    if mutation == "hash":
        raw["title"] += " tampered"
    elif mutation == "html":
        raw["_html"] += " tampered"
    else:
        inputs = raw["manifest"]["run_manifests"][0]["inputs"]
        inputs["source_rights"]["export"] = False
        inputs["content_hash"] = physical_hash(inputs)
        raw["checksum"] = digest({k: v for k, v in raw.items() if not k.startswith("_") and k != "checksum"})
        # A distinct authoritative fixture binds the restricted report version
        # so this test reaches the real Cedar export decision, not just staleness.
        stored_grant = store.list(GRANT_KIND, identity["tenant"])[0]
        stored_grant["report_checksum"] = raw["checksum"]
        stored_grant["content_hash"] = digest({k: v for k, v in stored_grant.items() if k != "content_hash"})
        replace(store, GRANT_KIND, identity["tenant"], stored_grant)
    replace(store, "report", identity["tenant"], raw)
    response = client.get("/api/review/report", headers=headers)
    assert response.status_code == (403 if mutation == "permissions" else 503), response.text
    if mutation == "permissions":
        assert response.json()["detail"]["authorization"]["decision"] == "Deny"
    else:
        assert response.json()["detail"]["code"] == "REPORT_INTEGRITY_ERROR"
    assert client.get("/api/review/report/download", headers=headers).status_code == response.status_code


def test_rehashed_different_report_version_makes_old_grant_stale(setup):
    client, _, store, identity, _, report = setup
    _, headers = grant(setup)
    raw = store.get("report", report["id"], identity["tenant"])
    raw["title"] = "New explanatory version"
    raw["checksum"] = digest({k: v for k, v in raw.items() if not k.startswith("_") and k != "checksum"})
    replace(store, "report", identity["tenant"], raw)
    response = client.get("/api/review/report", headers=headers)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "REVIEW_REPORT_CHANGED"


def test_comments_anchor_to_report_and_receptor_and_are_append_only(setup):
    client, _, store, identity, project, report = setup
    created, headers = grant(setup, label="Fixture reviewer")
    receptor = report["comparison"]["metrics"][0]["receptor_id"]
    body = {"report_checksum": report["checksum"], "anchor": {"kind": "receptor", "receptor_id": receptor},
            "body": "<script>Literal review text</script>"}
    written = client.post("/api/review/report/comments", headers=headers, json=body)
    assert written.status_code == 201, written.text
    item = written.json()
    assert item["report_checksum"] == report["checksum"] and item["anchor"]["receptor_id"] == receptor
    assert item["label"] == "Fixture reviewer" and item["body"] == body["body"]
    assert client.get("/api/review/report/comments", headers=headers).json() == [item]
    assert client.get(owner_path(project, report) + "/review-comments").json() == [item]
    assert client.get(owner_path(project, report) + "/review-grants").json()[0]["comment_count"] == 1
    assert client.patch("/api/review/report/comments/" + item["id"], headers=headers, json={"body": "edited"}).status_code in {403, 404, 405}
    assert client.delete("/api/review/report/comments/" + item["id"], headers=headers).status_code in {403, 404, 405}
    assert store.get("report", report["id"], identity["tenant"])["checksum"] == report["checksum"]
    assert client.post("/api/review/report/comments", headers=headers, json={**body, "report_checksum": "sha256:" + "0" * 64}).status_code == 409
    assert client.post("/api/review/report/comments", headers=headers, json={**body, "anchor": {"kind": "receptor", "receptor_id": "foreign-receptor"}}).status_code == 422
    assert client.post("/api/review/report/comments", headers=headers, json={**body, "anchor": {"kind": "report", "receptor_id": receptor}}).status_code == 422
    assert client.post("/api/review/report/comments", headers=headers, json={**body, "body": "x" * 2001}).status_code == 422
    assert client.post("/api/review/report/comments", headers=headers, json={**body, "owner_user_id": "forged"}).status_code == 422
    assert client.post("/api/review/report/comments", headers=headers, json={**body, "body": "   "}).status_code == 422


def test_tampered_comment_blocks_review_and_owner_reads(setup):
    client, _, store, identity, project, report = setup
    created, headers = grant(setup)
    response = client.post("/api/review/report/comments", headers=headers, json={"report_checksum": report["checksum"],
        "anchor": {"kind": "report"}, "body": "Original"})
    assert response.status_code == 201
    raw = store.list(COMMENT_KIND, identity["tenant"])[0]
    raw["body"] = "Altered"
    replace(store, COMMENT_KIND, identity["tenant"], raw)
    assert client.get("/api/review/report", headers=headers).status_code == 503
    assert client.get(owner_path(project, report) + "/review-comments").status_code == 503
    assert client.post(owner_path(project, report) + f"/review-grants/{created['id']}/revoke").status_code == 200
    assert client.get("/api/review/report", headers=headers).json()["detail"]["code"] == "REVIEW_REVOKED"


def test_source_notice_projection_keeps_attribution_without_private_package(setup):
    client, _, store, identity, _, report = setup
    raw = store.get("report", report["id"], identity["tenant"])
    notice = {"notice": "Explicit attribution projection fixture: © OpenStreetMap contributors under ODbL 1.0.",
              "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
              "attribution_url": "https://www.openstreetmap.org/copyright"}
    raw["manifest"]["source_export_packages"] = [{"source_compliance": {
        **notice, "original_reviewed_derivative": {"private_fixture_marker": "not a real OSM source bundle"}},
        "scenario": {"private_geometry_fixture_marker": "not recipient JSON"}}]
    raw["checksum"] = digest({k: v for k, v in raw.items() if not k.startswith("_") and k != "checksum"})
    replace(store, "report", identity["tenant"], raw)
    _, headers = grant(setup)
    response = client.get("/api/review/report", headers=headers)
    assert response.status_code == 200
    assert response.json()["report"]["source_notices"] == [notice]
    assert "private_fixture_marker" not in response.text and "private_geometry_fixture_marker" not in response.text


def test_report_grant_and_comment_budgets_are_server_bounded(setup, monkeypatch):
    import api.report_review as module
    client, _, _, _, project, report = setup
    monkeypatch.setattr(module, "MAX_GRANTS", 2)
    monkeypatch.setattr(module, "MAX_COMMENTS", 1)
    _, headers = grant(setup)
    grant(setup)
    assert client.post(owner_path(project, report) + "/review-grants", json={}).status_code == 409
    comment = {"report_checksum": report["checksum"], "anchor": {"kind": "report"}, "body": "A bounded comment"}
    assert client.post("/api/review/report/comments", headers=headers, json=comment).status_code == 201
    assert client.post("/api/review/report/comments", headers=headers, json=comment).status_code == 409


@pytest.mark.parametrize("ttl", [0, 59, 604801, True, "86400"])
def test_ttl_strict_bounded_and_empty_label_denied(setup, ttl):
    client, _, _, _, project, report = setup
    assert client.post(owner_path(project, report) + "/review-grants", json={"ttl_seconds": ttl}).status_code == 422
    assert client.post(owner_path(project, report) + "/review-grants", json={"label": " "}).status_code == 422


def test_read_and_revoke_serialize_under_store_transaction(setup, monkeypatch):
    client, _, store, _, project, report = setup
    created, headers = grant(setup)
    entered, release = threading.Event(), threading.Event()
    original_get = store.get
    def held_get(kind, identifier, tenant, db=None):
        if kind == GRANT_KIND and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original_get(kind, identifier, tenant, db)
    monkeypatch.setattr(store, "get", held_get)
    results = {}
    def read():
        results["read"] = client.get("/api/review/report", headers=headers)
    def revoke():
        results["revoke"] = client.post(owner_path(project, report) + f"/review-grants/{created['id']}/revoke")
    reader = threading.Thread(target=read)
    reader.start()
    assert entered.wait(10)
    worker = threading.Thread(target=revoke)
    worker.start()
    time.sleep(0.1)
    assert "revoke" not in results
    release.set()
    reader.join(10)
    worker.join(10)
    assert not worker.is_alive() and not reader.is_alive()
    assert results["read"].status_code == 200 and results["revoke"].status_code == 200
    results["comment"] = client.post("/api/review/report/comments", headers=headers, json={
        "report_checksum": report["checksum"], "anchor": {"kind": "report"}, "body": "After revocation"})
    assert results["comment"].status_code == 403
