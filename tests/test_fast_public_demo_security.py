"""Release regressions for public error redaction and persistence isolation."""
from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from infra.public_demo import COOKIE, Limits, create_app

ORIGIN = "https://demo.example"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for name in ("UA_DATABASE_URL", "DATABASE_URL", "UA_DEMO_SECRET", "SECRET_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def app_at(tmp_path, **kwargs):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<html><body></body></html>", encoding="utf-8")
    return create_app(tmp_path / "data", dist, demo_origin=ORIGIN, start_worker=False, **kwargs)


def test_database_errors_do_not_echo_driver_details(tmp_path, monkeypatch, caplog):
    from api.postgres import PostgresStore
    monkeypatch.setenv("DATABASE_URL", "postgresql://placeholder.invalid/test")
    def failure(*args, **kwargs):
        raise RuntimeError("private-driver-sentinel")
    monkeypatch.setattr(PostgresStore, "__init__", failure)
    app = app_at(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/capabilities")
        assert response.json()["storage_error"] == "DEMO_DATABASE_UNAVAILABLE"
        assert "private-driver-sentinel" not in response.text
    assert "private-driver-sentinel" not in caplog.text


def test_private_database_rejected_before_migration(tmp_path, monkeypatch):
    from api.postgres import PostgresStore
    monkeypatch.setenv("DATABASE_URL", "postgresql://placeholder.invalid/test")
    queries = []
    class Cursor:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row
    class Connection:
        def execute(self, query, args=None):
            queries.append(query)
            if query.startswith("SELECT to_regclass"):
                return Cursor(("urban.objects",))
            if query.startswith("SELECT 1 FROM urban.objects"):
                return Cursor((1,))
            raise AssertionError("Unexpected private database operation")
        def close(self):
            pass
    monkeypatch.setattr(PostgresStore, "connect", lambda self: Connection())
    def migration_forbidden(self):
        raise AssertionError("Migration must not touch private state")
    monkeypatch.setattr(PostgresStore, "migrate", migration_forbidden)
    app = app_at(tmp_path)
    assert app.state.store.adapter_name == "local-sqlite-files"
    assert app.state.storage_error == "DEMO_DATABASE_UNAVAILABLE"
    assert len(queries) == 2


def test_short_environment_cookie_key_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_DEMO_SECRET", "short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        app_at(tmp_path)


def test_corrupt_persisted_cookie_key_refused(tmp_path):
    app_at(tmp_path)
    (tmp_path / "data" / ".demo-secret").write_bytes(b"")
    with pytest.raises(ValueError, match="Persisted demo cookie signing secret is invalid"):
        app_at(tmp_path)


def test_restored_session_cannot_bypass_session_cap(tmp_path):
    app = app_at(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/projects")
        old_cookie = client.cookies.get(COOKIE)
    restarted = app_at(tmp_path, limits=replace(Limits(), sessions=1))
    with TestClient(restarted, base_url=ORIGIN) as first:
        assert first.get("/api/projects").status_code == 200
        with TestClient(restarted, base_url=ORIGIN) as returning:
            returning.cookies.set(COOKIE, old_cookie)
            response = returning.get("/api/projects")
            assert response.status_code == 429
            assert response.json()["detail"]["code"] == "DEMO_SESSION_CAP"
