"""Import the installed LDraw/BrickLayer catalogue and publish it in PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import sys

import psycopg

from server.config import ROOT, Settings
from server.exporter import SDK_COMMIT
from server.ldraw_library import MARKER_NAME
from server.parts import PartsService
from server.parts_postgres import search_terms

MIGRATION = ROOT / "server" / "migrations" / "001_parts_catalog.sql"


def import_catalogue(database_url: str, service: PartsService) -> tuple[int, int, int]:
    from bricklayer.ldraw import partdata

    if not service.library_root or not service.catalogue:
        raise ValueError("LDraw 零件目录为空。请先安装零件库并检查 LDRAW_LIBRARY_PATH。")
    marker = service.library_root / MARKER_NAME
    snapshot = json.loads(marker.read_text(encoding="utf-8")).get("version") if marker.is_file() else None
    library_total = len(service.library_ids)
    rows = []
    for sort_order, spec in enumerate(service.catalogue):
        part = service.metadata(spec)
        # Match the current SDK's default keyword fields and whole-word tokenization.
        terms = search_terms(f"{spec.category} {spec.name} {spec.subcategory or ''}")
        rows.append((part["ldrawId"], part["name"], part["category"], part["categoryName"],
                     part["w"], part["d"], part["h"], partdata.popularity(spec.ldraw_id), sort_order, terms))

    fingerprint_data = json.dumps(
        [snapshot, SDK_COMMIT, library_total, rows], allow_nan=False, separators=(",", ":")
    )
    fingerprint = hashlib.sha256(fingerprint_data.encode()).hexdigest()
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        # Concurrent imports must publish complete releases in sequence, including on a new database.
        conn.execute("SELECT pg_advisory_xact_lock(704622, 1)")
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
        release = conn.execute(
            "SELECT id FROM brick_it_catalog.catalog_releases WHERE fingerprint = %s", (fingerprint,)
        ).fetchone()
        if release:
            release_id = release[0]
        else:
            release_id = conn.execute("""
                INSERT INTO brick_it_catalog.catalog_releases
                    (fingerprint, ldraw_snapshot, sdk_commit, library_total, catalogue_total)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """, (fingerprint, snapshot, SDK_COMMIT, library_total, len(rows))).fetchone()[0]
            with conn.cursor().copy("""
                COPY brick_it_catalog.part_catalog
                    (release_id, ldraw_id, name, category, category_name, w, d, h, popularity, sort_order, search_terms)
                FROM STDIN
                """) as copy:
                for row in rows:
                    copy.write_row((release_id, *row))
            imported = conn.execute(
                "SELECT count(*) FROM brick_it_catalog.part_catalog WHERE release_id = %s", (release_id,)
            ).fetchone()[0]
            if imported != len(rows):
                raise ValueError("PostgreSQL 零件数量校验失败。")
            conn.execute("ANALYZE brick_it_catalog.part_catalog")
        conn.execute("""
            INSERT INTO brick_it_catalog.catalog_active (release_id) VALUES (%s)
            ON CONFLICT (singleton) DO UPDATE SET release_id = EXCLUDED.release_id
            """, (release_id,))
    return release_id, len(rows), library_total


def main() -> int:
    try:
        settings = Settings.from_env()
        if not settings.parts_database_url:
            raise ValueError("请先设置 BRICK_IT_PARTS_DATABASE_URL。")
        release_id, count, library_total = import_catalogue(settings.parts_database_url, PartsService())
    except (ValueError, OSError, ImportError) as exc:
        print(f"零件目录导入失败：{exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"PostgreSQL 导入失败（{type(exc).__name__}）。请检查数据库连接和建表权限。", file=sys.stderr)
        return 1
    print(f"PostgreSQL 零件目录已发布（版本 {release_id}，{count} 种零件，源库 {library_total} 个主零件文件）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
