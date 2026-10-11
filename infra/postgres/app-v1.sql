-- Explicit application migration, separate from bootstrapping the PostGIS extension.
-- Requires CREATE in database/schema during migration, not for the serving role.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS urban;
CREATE TABLE IF NOT EXISTS urban.schema_versions (
    version TEXT PRIMARY KEY, installed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS urban.objects (
    kind TEXT NOT NULL, id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    project_id TEXT, data TEXT NOT NULL,
    project_kind TEXT GENERATED ALWAYS AS ('project'::text) STORED,
    PRIMARY KEY(kind,id), UNIQUE(kind,id,tenant_id), UNIQUE(kind,id,tenant_id,project_id),
    FOREIGN KEY(project_kind,project_id,tenant_id) REFERENCES urban.objects(kind,id,tenant_id)
);
CREATE INDEX IF NOT EXISTS object_scope ON urban.objects(kind,tenant_id,project_id);
CREATE TABLE IF NOT EXISTS urban.submissions (
    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, key TEXT NOT NULL,
    request_hash TEXT NOT NULL, data TEXT NOT NULL,
    project_kind TEXT GENERATED ALWAYS AS ('project'::text) STORED,
    PRIMARY KEY(tenant_id,project_id,key),
    FOREIGN KEY(project_kind,project_id,tenant_id) REFERENCES urban.objects(kind,id,tenant_id)
);
CREATE TABLE IF NOT EXISTS urban.quote_uses (
    quote_id TEXT PRIMARY KEY, submission_key TEXT NOT NULL,
    tenant_id TEXT, project_id TEXT,
    quote_kind TEXT GENERATED ALWAYS AS ('quote'::text) STORED
);
CREATE TABLE IF NOT EXISTS urban.runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
    scenario_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    fence BIGINT NOT NULL DEFAULT 0 CHECK(fence >= 0), lease_until DOUBLE PRECISION,
    worker_id TEXT, artifact TEXT, created_at TEXT NOT NULL,
    scenario_kind TEXT GENERATED ALWAYS AS ('scenario'::text) STORED,
    FOREIGN KEY(scenario_kind,scenario_id,tenant_id,project_id)
        REFERENCES urban.objects(kind,id,tenant_id,project_id),
    CHECK(status IN ('queued','preparing','solving','verifying','complete_unqualified','failed','cancelled'))
);
CREATE INDEX IF NOT EXISTS run_scope ON urban.runs(tenant_id,project_id,created_at);
CREATE INDEX IF NOT EXISTS run_claim ON urban.runs(status,created_at);
CREATE TABLE IF NOT EXISTS urban.usage (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES urban.runs(id),
    seconds DOUBLE PRECISION NOT NULL CHECK(seconds >= 0), created_at TEXT NOT NULL,
    tenant_id TEXT, project_id TEXT
);
-- Scope columns are derived from their parent at insertion, including the legacy
-- Store's explicit usage column list / two-value quote-use insert. Backfill makes
-- repeated migration safe for a previously initialized Phase A fixture schema.
ALTER TABLE urban.quote_uses ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE urban.quote_uses ADD COLUMN IF NOT EXISTS project_id TEXT;
ALTER TABLE urban.quote_uses ADD COLUMN IF NOT EXISTS quote_kind TEXT GENERATED ALWAYS AS ('quote'::text) STORED;
ALTER TABLE urban.usage ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE urban.usage ADD COLUMN IF NOT EXISTS project_id TEXT;
UPDATE urban.quote_uses q SET tenant_id=o.tenant_id,project_id=o.project_id
    FROM urban.objects o WHERE o.kind='quote' AND o.id=q.quote_id;
UPDATE urban.usage u SET tenant_id=r.tenant_id,project_id=r.project_id
    FROM urban.runs r WHERE r.id=u.run_id;
ALTER TABLE urban.quote_uses ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE urban.quote_uses ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE urban.usage ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE urban.usage ALTER COLUMN project_id SET NOT NULL;
CREATE OR REPLACE FUNCTION urban.derive_quote_use_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_tenant TEXT; parent_project TEXT;
BEGIN
    SELECT tenant_id,project_id INTO parent_tenant,parent_project FROM urban.objects
        WHERE kind='quote' AND id=NEW.quote_id;
    IF NOT FOUND OR (NEW.tenant_id IS NOT NULL AND NEW.tenant_id<>parent_tenant)
        OR (NEW.project_id IS NOT NULL AND NEW.project_id<>parent_project) THEN
        RAISE EXCEPTION 'Quote usage scope must match its parent' USING ERRCODE='23503';
    END IF;
    NEW.tenant_id=parent_tenant; NEW.project_id=parent_project;
    RETURN NEW;
END; $$;
CREATE OR REPLACE FUNCTION urban.derive_usage_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_tenant TEXT; parent_project TEXT;
BEGIN
    SELECT tenant_id,project_id INTO parent_tenant,parent_project FROM urban.runs WHERE id=NEW.run_id;
    IF NOT FOUND OR (NEW.tenant_id IS NOT NULL AND NEW.tenant_id<>parent_tenant)
        OR (NEW.project_id IS NOT NULL AND NEW.project_id<>parent_project) THEN
        RAISE EXCEPTION 'Resource usage scope must match its parent' USING ERRCODE='23503';
    END IF;
    NEW.tenant_id=parent_tenant; NEW.project_id=parent_project;
    RETURN NEW;
END; $$;
DROP TRIGGER IF EXISTS derive_quote_use_scope ON urban.quote_uses;
CREATE TRIGGER derive_quote_use_scope BEFORE INSERT OR UPDATE ON urban.quote_uses
    FOR EACH ROW EXECUTE FUNCTION urban.derive_quote_use_scope();
DROP TRIGGER IF EXISTS derive_usage_scope ON urban.usage;
CREATE TRIGGER derive_usage_scope BEFORE INSERT OR UPDATE ON urban.usage
    FOR EACH ROW EXECUTE FUNCTION urban.derive_usage_scope();
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='runs_id_scope_unique' AND conrelid='urban.runs'::regclass) THEN
        ALTER TABLE urban.runs ADD CONSTRAINT runs_id_scope_unique UNIQUE(id,tenant_id,project_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='quote_use_scope_parent' AND conrelid='urban.quote_uses'::regclass) THEN
        ALTER TABLE urban.quote_uses ADD CONSTRAINT quote_use_scope_parent
            FOREIGN KEY(quote_kind,quote_id,tenant_id,project_id) REFERENCES urban.objects(kind,id,tenant_id,project_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='usage_scope_parent' AND conrelid='urban.usage'::regclass) THEN
        ALTER TABLE urban.usage ADD CONSTRAINT usage_scope_parent
            FOREIGN KEY(run_id,tenant_id,project_id) REFERENCES urban.runs(id,tenant_id,project_id);
    END IF;
END; $$;
CREATE INDEX IF NOT EXISTS quote_use_scope ON urban.quote_uses(tenant_id,project_id,quote_id);
CREATE INDEX IF NOT EXISTS usage_scope ON urban.usage(tenant_id,project_id,run_id);
CREATE TABLE IF NOT EXISTS urban.geometry_versions (
    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
    object_id TEXT NOT NULL, object_kind TEXT NOT NULL CHECK(object_kind IN ('building','source','receptor')),
    scenario_kind TEXT GENERATED ALWAYS AS ('scenario'::text) STORED,
    frame_hash TEXT NOT NULL, frame_data JSONB NOT NULL, metadata JSONB NOT NULL,
    -- A custom per-site local metre frame has no shared EPSG SRID. Frame identity is mandatory.
    geom geometry(Geometry,0) NOT NULL CHECK(ST_IsValid(geom)),
    PRIMARY KEY(tenant_id,project_id,scenario_id,object_id),
    FOREIGN KEY(scenario_kind,scenario_id,tenant_id,project_id)
        REFERENCES urban.objects(kind,id,tenant_id,project_id),
    CHECK(frame_data->>'unit' = 'm'), CHECK(frame_data->>'axis_order' = 'x_east_y_north')
);
CREATE INDEX IF NOT EXISTS geometry_scope ON urban.geometry_versions(tenant_id,project_id,scenario_id);
CREATE INDEX IF NOT EXISTS geometry_spatial ON urban.geometry_versions USING GIST(geom);
INSERT INTO urban.schema_versions(version) VALUES ('phase-a-app-v1') ON CONFLICT DO NOTHING;
