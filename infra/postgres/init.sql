-- Development PostGIS foundation. Application migration/adapter is a separate gate.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS urban;
CREATE TABLE IF NOT EXISTS urban.schema_versions (
  version text PRIMARY KEY,
  installed_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO urban.schema_versions(version) VALUES ('foundation-v1') ON CONFLICT DO NOTHING;

