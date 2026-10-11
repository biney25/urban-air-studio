"""Durable store contract, with explicit local and configured cloud adapters."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


TERMINAL = {"complete_unqualified", "failed", "cancelled"}
ACTIVE = {"queued", "preparing", "solving", "verifying"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


class Store:
    def __new__(cls, directory=None):
        # Explicit directories always select local storage for isolated tests/tools.
        if cls is Store and directory is None and (os.getenv("UA_DATABASE_URL") or os.getenv("DATABASE_URL")):
            from api.postgres import PostgresStore
            return object.__new__(PostgresStore)
        return super().__new__(cls)

    def __init__(self, directory=None):
        self.directory = Path(directory or os.getenv("UA_DATA_DIR", "runtime/local")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "studio.sqlite3"
        self.adapter_name = "local-sqlite-files"
        self.artifacts = None
        if directory is None and os.getenv("UA_ASSET_BUCKET"):
            from api.artifacts import S3Artifacts
            self.artifacts = S3Artifacts.from_env()
            self.adapter_name = "sqlite-s3"
        with closing(self.connect()) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects (
                    kind TEXT NOT NULL, id TEXT NOT NULL, tenant_id TEXT NOT NULL,
                    project_id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(kind,id), UNIQUE(kind,id,tenant_id));
                CREATE INDEX IF NOT EXISTS object_scope ON objects(kind,tenant_id,project_id);
                CREATE TABLE IF NOT EXISTS submissions (
                    tenant_id TEXT, project_id TEXT, key TEXT, request_hash TEXT, data TEXT,
                    PRIMARY KEY(tenant_id,project_id,key));
                CREATE TABLE IF NOT EXISTS quote_uses (quote_id TEXT PRIMARY KEY, submission_key TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    scenario_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    fence INTEGER NOT NULL DEFAULT 0, lease_until REAL,
                    worker_id TEXT, artifact TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS run_scope ON runs(tenant_id,project_id,created_at);
                CREATE TABLE IF NOT EXISTS usage (
                    event_id TEXT PRIMARY KEY, run_id TEXT, seconds REAL NOT NULL, created_at TEXT);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def put(db, kind, tenant, data, project=None):
        db.execute("INSERT INTO objects(kind,id,tenant_id,project_id,data) VALUES(?,?,?,?,?)",
                   (kind, data["id"], tenant, project, canonical(data)))
        if kind == "scenario" and hasattr(db, "store_geometry"):
            db.store_geometry(tenant, project, data)

    def get(self, kind, identifier, tenant, db=None):
        own = db is None
        db = db or self.connect()
        try:
            row = db.execute("SELECT data FROM objects WHERE kind=? AND id=? AND tenant_id=?",
                             (kind, identifier, tenant)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            if own:
                db.close()

    def list(self, kind, tenant, project=None):
        with closing(self.connect()) as db:
            query, args = "SELECT data FROM objects WHERE kind=? AND tenant_id=?", [kind, tenant]
            if project is not None:
                query += " AND project_id=?"
                args.append(project)
            return [json.loads(row[0]) for row in db.execute(query, args).fetchall()]

    def run(self, identifier, tenant=None, db=None):
        own = db is None
        db = db or self.connect()
        try:
            query, args = "SELECT * FROM runs WHERE id=?", [identifier]
            if tenant is not None:
                query += " AND tenant_id=?"
                args.append(tenant)
            row = db.execute(query, args).fetchone()
            return self.decode_run(row) if row else None
        finally:
            if own:
                db.close()

    @staticmethod
    def decode_run(row):
        data = json.loads(row["data"])
        data.update(status=row["status"], cancel_requested=bool(row["cancel_requested"]))
        data["_fence"] = row["fence"]
        data["_artifact"] = row["artifact"]
        data["_tenant"] = row["tenant_id"]
        return data

    def list_runs(self, tenant, project):
        with closing(self.connect()) as db:
            return [self.decode_run(row) for row in db.execute(
                "SELECT * FROM runs WHERE tenant_id=? AND project_id=? ORDER BY created_at DESC",
                (tenant, project)).fetchall()]

    @staticmethod
    def insert_run(db, tenant, data):
        db.execute("INSERT INTO runs(id,tenant_id,project_id,scenario_id,status,data,created_at) VALUES(?,?,?,?,?,?,?)",
                   (data["id"], tenant, data["project_id"], data["scenario_id"], data["status"], canonical(data), data["created_at"]))

    def claim(self, worker, lease_seconds=30):
        """One SQLite write transaction fences the previous owner before a new claim."""
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM runs WHERE status IN ('queued','preparing','solving','verifying') ORDER BY created_at").fetchall()
            for row in rows:
                if row["status"] != "queued" and (row["lease_until"] or 0) > time.time():
                    continue
                data = json.loads(row["data"])
                if row["cancel_requested"]:
                    data.update(stage="cancelled", progress=0, error=None)
                    db.execute("UPDATE runs SET status='cancelled',data=?,lease_until=NULL WHERE id=?",
                               (canonical(data), row["id"]))
                    continue
                if data["attempt"] > 3 or (row["status"] != "queued" and data["attempt"] >= 3):
                    data.update(stage="failed", error={"code":"WORKER_LOST", "message":"Worker lease lost with the three-attempt allowance exhausted; inspect before a new allowance"})
                    db.execute("UPDATE runs SET status='failed',data=?,lease_until=NULL WHERE id=?", (canonical(data), row["id"]))
                    continue
                if row["status"] != "queued":
                    data["attempt"] += 1
                data.update(stage="preparing", progress=0.05, error=None)
                db.execute("UPDATE runs SET status='preparing',data=?,worker_id=?,fence=fence+1,lease_until=? WHERE id=?",
                           (canonical(data), worker, time.time()+lease_seconds, row["id"]))
                return self.run(row["id"], db=db)
        return None

    def transition(self, run_id, fence, status, progress, lease_seconds=30):
        with self.transaction() as db:
            run = self.run(run_id, db=db)
            if not run or run["_fence"] != fence or run["status"] in TERMINAL or run["cancel_requested"]:
                return False
            run.update(stage=status, progress=progress)
            clean = {k:v for k,v in run.items() if not k.startswith("_")}
            db.execute("UPDATE runs SET status=?,data=?,lease_until=? WHERE id=? AND fence=?",
                       (status, canonical(clean), time.time()+lease_seconds, run_id, fence))
            return True

    def heartbeat(self, run_id, fence, lease_seconds=30):
        with self.transaction() as db:
            row = db.execute("SELECT status,cancel_requested,fence FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["fence"] != fence or row["status"] in TERMINAL or row["cancel_requested"]:
                return False
            db.execute("UPDATE runs SET lease_until=? WHERE id=? AND fence=?", (time.time()+lease_seconds, run_id, fence))
            return True

    def finish(self, run_id, fence, status, error=None, artifact=None, seconds=0, artifact_checksum=None):
        """Cancellation wins if accepted before this result pointer commit."""
        with self.transaction() as db:
            run = self.run(run_id, db=db)
            if not run or run["_fence"] != fence or run["status"] in TERMINAL:
                return False
            if run["cancel_requested"]:
                status, artifact, error = "cancelled", None, None
            run.update(stage=status, progress=1 if status == "complete_unqualified" else run["progress"], error=error,
                       finished_at=utc_now(), resource_use={"worker_seconds":round(seconds,6), "billed_currency":None})
            if status == "complete_unqualified":
                run["artifact_checksum"] = artifact_checksum
            clean = {k:v for k,v in run.items() if not k.startswith("_")}
            db.execute("INSERT OR IGNORE INTO usage(event_id,run_id,seconds,created_at) VALUES(?,?,?,?)",
                       (f"{run_id}:{fence}", run_id, seconds, utc_now()))
            db.execute("UPDATE runs SET status=?,data=?,artifact=?,lease_until=NULL WHERE id=? AND fence=?",
                       (status, canonical(clean), artifact, run_id, fence))
            return status == "complete_unqualified"

    def atomic_artifact(self, run_id, fence, payload):
        if self.artifacts is not None:
            return self.artifacts.atomic_artifact(run_id, fence, payload)
        content = canonical(payload).encode("utf-8")
        relative = Path("artifacts") / run_id / str(fence) / "bundle.json"
        target = self.directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        try:
            # Shared mutation authority serializes same-fence publication without
            # relying on hard-link privileges in restricted Windows workspaces.
            with self.transaction():
                if target.exists():
                    if target.read_bytes() != content:
                        raise ValueError("An immutable attempt artifact already exists with different content")
                else:
                    os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(relative), "sha256:" + hashlib.sha256(content).hexdigest()

    def read_artifact(self, relative, expected_checksum=None):
        if self.artifacts is not None:
            return self.artifacts.read_artifact(relative, expected_checksum)
        target = (self.directory / relative).resolve()
        if not target.is_relative_to(self.directory / "artifacts"):
            raise ValueError("Artifact outside local storage")
        content = target.read_bytes()
        if expected_checksum and "sha256:"+hashlib.sha256(content).hexdigest() != expected_checksum:
            raise ValueError("Committed artifact checksum mismatch")
        return json.loads(content.decode("utf-8"))
