CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS brick_it_catalog;

CREATE TABLE IF NOT EXISTS brick_it_catalog.catalog_releases (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fingerprint text NOT NULL UNIQUE CHECK (length(fingerprint) = 64),
    ldraw_snapshot text,
    sdk_commit text NOT NULL,
    library_total integer NOT NULL,
    catalogue_total integer NOT NULL CHECK (catalogue_total > 0 AND catalogue_total <= library_total),
    imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS brick_it_catalog.part_catalog (
    release_id bigint NOT NULL REFERENCES brick_it_catalog.catalog_releases (id) ON DELETE RESTRICT,
    ldraw_id text NOT NULL,
    ldraw_key text COLLATE "C" GENERATED ALWAYS AS (lower(ldraw_id)) STORED,
    name text NOT NULL,
    category text NOT NULL,
    category_name text NOT NULL,
    w double precision NOT NULL,
    d double precision NOT NULL,
    h double precision NOT NULL,
    popularity integer NOT NULL CHECK (popularity >= 0),
    sort_order integer NOT NULL,
    search_terms text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('pg_catalog.simple', search_terms)) STORED,
    PRIMARY KEY (release_id, ldraw_key),
    UNIQUE (release_id, sort_order)
);

CREATE INDEX IF NOT EXISTS part_catalog_category_order
    ON brick_it_catalog.part_catalog (release_id, category, sort_order);
CREATE INDEX IF NOT EXISTS part_catalog_search
    ON brick_it_catalog.part_catalog USING gin (search_vector);

CREATE TABLE IF NOT EXISTS brick_it_catalog.catalog_active (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    release_id bigint NOT NULL REFERENCES brick_it_catalog.catalog_releases (id) ON DELETE RESTRICT
);
