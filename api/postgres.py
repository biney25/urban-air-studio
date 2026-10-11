"""PostgreSQL/PostGIS persistence implementing the existing durable Store contract.

Mutations currently use one transaction advisory lock. This conservative bridge
preserves local idempotency/fencing/cancellation semantics across API processes.
Measured contention is the gate for narrower, ordered row-level locks.
"""
from contextlib import closing, contextmanager
import json
import math
import os
from pathlib import Path

from api.storage import Store, canonical, digest

SCHEMA_VERSION = "phase-a-app-v1"
MUTATION_LOCK = 843019260901
MIGRATION = Path(__file__).resolve().parents[1] / "infra/postgres/app-v1.sql"


class IndexedRow(dict):
    """SQLite-compatible row access for scoped pre-existing Store SQL."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return IndexedRow(row) if row is not None else None

    def fetchall(self):
        return [IndexedRow(row) for row in self.cursor.fetchall()]


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, args=None):
        # Query strings are fixed application SQL, never user-generated identifiers.
        # This is only a compatibility translation, not a general SQL interpreter.
        if query.startswith("INSERT OR IGNORE INTO "):
            query = query.replace("INSERT OR IGNORE INTO ", "INSERT INTO ", 1) + " ON CONFLICT DO NOTHING"
        return Cursor(self.connection.execute(query.replace("?", "%s"), args))

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def store_geometry(self, tenant, project, scenario):
        frame = scenario.get("coordinate_frame")
        if not isinstance(frame, dict) or frame.get("unit") != "m" or frame.get("axis_order") != "x_east_y_north":
            raise ValueError("PostGIS scenario requires its explicit local metre coordinate frame")
        frame_hash = digest(frame)
        for group, kind in (("buildings", "building"), ("sources", "source"), ("receptors", "receptor")):
            for item in scenario.get(group, []):
                if kind == "building":
                    points = [list(p) for p in item["footprint"]]
                    if points[0] != points[-1]:
                        points.append(points[0])
                    geometry = {"type":"Polygon", "coordinates":[points]}
                else:
                    geometry = {"type":"Point", "coordinates":[item["x"], item["y"]]}
                self.execute("""INSERT INTO geometry_versions
                    (tenant_id,project_id,scenario_id,object_id,object_kind,frame_hash,frame_data,metadata,geom)
                    VALUES(?,?,?,?,?,?,?::jsonb,?::jsonb,ST_SetSRID(ST_GeomFromGeoJSON(?),0))""",
                    (tenant, project, scenario["id"], item["id"], kind, frame_hash, canonical(frame),
                     canonical(item), canonical(geometry)))


class PostgresStore(Store):
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self, directory=None, database_url=None, artifacts=None, auto_migrate=False):
        self.database_url = database_url or os.environ.get("UA_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not self.database_url or not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("UA_DATABASE_URL or DATABASE_URL must select PostgreSQL")
        self.directory = Path(directory or os.getenv("UA_DATA_DIR", "runtime/local")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = artifacts
        if self.artifacts is None and os.getenv("UA_ASSET_BUCKET"):
            from api.artifacts import S3Artifacts
            self.artifacts = S3Artifacts.from_env()
        self.adapter_name = "postgres-postgis-s3" if self.artifacts is not None else "postgres-postgis-files"
        if auto_migrate:
            self.migrate()
        with closing(self.connect()) as db:
            version = db.execute("SELECT version FROM urban.schema_versions WHERE version=?", (SCHEMA_VERSION,)).fetchone()
            if not version:
                raise RuntimeError("Application database migration is required before serving requests")
            db.execute("SELECT PostGIS_Version()").fetchone()

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row
        options = {"connect_timeout":10, "autocommit":True, "row_factory":dict_row,
                   "options":"-c statement_timeout=15000 -c lock_timeout=15000"}
        if os.getenv("UA_MODE", "local") != "local":
            if os.getenv("UA_DB_SSLROOTCERT"):
                options["sslrootcert"] = os.environ["UA_DB_SSLROOTCERT"]
                options["sslmode"] = "verify-full"
            elif "sslmode=" not in self.database_url:
                options["sslmode"] = "require"
        connection = psycopg.connect(self.database_url, **options)
        connection.execute("SELECT set_config('search_path', 'urban, public', false)")
        return Connection(connection)

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN")
            db.execute("SELECT pg_advisory_xact_lock(?)", (MUTATION_LOCK,))
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        with self.transaction() as db:
            # Checked-in migration contains no runtime/user interpolation.
            sql_path = MIGRATION if MIGRATION.is_file() else (Path(__file__).resolve().parent / "app-v1.sql")
            db.connection.execute(sql_path.read_text(encoding="utf-8"), prepare=False)

    def spatial_objects(self, tenant, project, scenario_id, bounds, limit=200):
        """Bounded, tenant/version-scoped physical-local spatial lookup.

        SRID 0 means the attached custom metre frame; these coordinates must never
        be transformed as WGS84 or compared with a different scenario/frame.
        """
        if len(bounds) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds):
            raise ValueError("Spatial bounds must be four finite local metre values")
        xmin, ymin, xmax, ymax = bounds
        if xmin >= xmax or ymin >= ymax or max(xmax-xmin, ymax-ymin) > 2500:
            raise ValueError("Spatial query exceeds bounded local support")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Spatial query limit must be 1–500")
        with closing(self.connect()) as db:
            return db.execute("""SELECT object_id,object_kind,frame_hash,metadata,
                ST_AsGeoJSON(geom)::jsonb AS geometry FROM geometry_versions
                WHERE tenant_id=? AND project_id=? AND scenario_id=?
                AND geom && ST_MakeEnvelope(?,?,?,?,0)
                AND ST_Intersects(geom,ST_MakeEnvelope(?,?,?,?,0))
                ORDER BY object_kind,object_id LIMIT ?""",
                (tenant, project, scenario_id, *bounds, *bounds, limit)).fetchall()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Explicit Urban Air application PostgreSQL migration")
    parser.add_argument("operation", choices=["migrate"])
    parser.parse_args()
    # Construct only the connection configuration, then apply/check the migration.
    store = object.__new__(PostgresStore)
    store.database_url = os.environ.get("UA_DATABASE_URL") or os.environ["DATABASE_URL"]
    store.migrate()
    print(json.dumps({"event":"database_migrated", "schema_version":SCHEMA_VERSION}))


if __name__ == "__main__":
    main()
