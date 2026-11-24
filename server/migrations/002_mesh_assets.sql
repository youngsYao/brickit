CREATE SCHEMA IF NOT EXISTS brick_it_catalog;

CREATE TABLE IF NOT EXISTS brick_it_catalog.mesh_assets (
    sha256 bytea PRIMARY KEY CHECK (octet_length(sha256) = 32),
    payload bytea NOT NULL CHECK (octet_length(payload) >= 96 AND sha256 = pg_catalog.sha256(payload)),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS brick_it_catalog.editor_meshes (
    ldraw_id text NOT NULL,
    sdk_commit text NOT NULL,
    mesh_sha256 bytea NOT NULL REFERENCES brick_it_catalog.mesh_assets (sha256) ON DELETE RESTRICT,
    descriptor jsonb NOT NULL,
    PRIMARY KEY (ldraw_id, sdk_commit)
);
