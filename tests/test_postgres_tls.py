"""A remote DSN cannot use misleading credential text to disable transport TLS."""
from unittest.mock import Mock
import pytest
from api.postgres import PostgresStore


@pytest.mark.parametrize("dsn,required", [
    ("postgresql://localhost:password@remote.example/db", True),
    ("postgresql://user:password@remote.example/db?sslmode=disable", True),
    ("postgresql://user:password@remote.example/db?sslmode=prefer", True),
    ("postgresql://user:password@127.0.0.1/db", False),
    ("postgresql://user:password@remote.example/db?sslmode=verify-full", False),
])
def test_remote_tls_cannot_be_bypassed_by_dsn_substring(monkeypatch, dsn, required):
    import psycopg
    monkeypatch.delenv("UA_DB_SSLROOTCERT", raising=False)
    connect = Mock(return_value=Mock())
    monkeypatch.setattr(psycopg, "connect", connect)
    store = object.__new__(PostgresStore)
    store.database_url = dsn
    store.connect()
    assert (connect.call_args.kwargs.get("sslmode") == "require") is required
