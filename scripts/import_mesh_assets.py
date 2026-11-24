"""Install the mesh tables and verify their round trip with the bundled models."""

from __future__ import annotations

import json
import sys

import psycopg

from scripts.bundle_generated_examples import EXAMPLES
from server.config import ROOT, Settings
from server.mesh_assets import MESH_REFERENCE, MeshStorageError, PostgresMeshStore

MIGRATION = ROOT / "server/migrations/002_mesh_assets.sql"


def import_bundled_meshes(database_url: str) -> tuple[int, int]:
    if not database_url:
        raise ValueError("请先设置 BRICK_IT_PARTS_DATABASE_URL。")
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(704622, 1)")
        conn.execute(MIGRATION.read_text(encoding="utf-8"))

    store = PostgresMeshStore.for_url(database_url)
    part_count = 0
    hashes = set()
    for example in EXAMPLES:
        path = ROOT / "dist/examples" / example["folder"] / "project.json"
        project = json.loads(path.read_text(encoding="utf-8"))
        referenced = store.reference_project(project)
        if store.restore_project(referenced) != project:
            raise MeshStorageError(f"{example['title']} 的网格还原校验失败。")
        part_count += len(referenced["parts"])
        hashes.update(part[MESH_REFERENCE] for part in referenced["parts"])
    return part_count, len(hashes)


def main() -> int:
    try:
        parts, unique = import_bundled_meshes(Settings.from_env().parts_database_url)
    except (MeshStorageError, ValueError, OSError) as exc:
        print(f"网格导入失败：{exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"PostgreSQL 网格导入失败（{type(exc).__name__}）。请检查连接和建表权限。", file=sys.stderr)
        return 1
    print(f"两个内置示例已通过网格还原校验：{parts} 份网格引用，{unique} 份不同网格。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
