"""Content-addressed storage for the editor's exact BrickLayer meshes."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import struct
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from functools import partial

import psycopg
from psycopg.types.json import Jsonb

from .exporter import MAX_MESH_VALUES, SDK_COMMIT

LOGGER = logging.getLogger(__name__)
MESH_REFERENCE = "meshSha256"
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
ENCODING_VERSION = 1
HEADER = struct.Struct("<III")
INSERT_MESH = """
    INSERT INTO brick_it_catalog.mesh_assets (sha256, payload)
    VALUES (%s, %b) ON CONFLICT (sha256) DO NOTHING
"""


class MeshStorageError(Exception):
    """A stored mesh is missing, invalid, or temporarily inaccessible."""


def _valid_counts(positions: int, indices: int) -> bool:
    return 9 <= positions <= MAX_MESH_VALUES and 3 <= indices <= MAX_MESH_VALUES and positions % 3 == indices % 3 == 0


def encode_mesh(mesh: dict) -> bytes:
    positions, indices = mesh.get("positions"), mesh.get("indices")
    if not isinstance(positions, list) or not isinstance(indices, list) or not _valid_counts(len(positions), len(indices)):
        raise MeshStorageError("Invalid mesh dimensions")
    if any(type(value) not in (int, float) or not -10000 <= value <= 10000 for value in positions):
        raise MeshStorageError("Invalid mesh vertex")
    if any(type(index) is not int or not 0 <= index < len(positions) // 3 for index in indices):
        raise MeshStorageError("Invalid mesh index")
    return struct.pack(f"<III{len(positions)}d{len(indices)}I", ENCODING_VERSION, len(positions), len(indices),
                       *positions, *indices)


def decode_mesh(payload: bytes) -> dict:
    if len(payload) < HEADER.size:
        raise MeshStorageError("Invalid mesh header")
    version, positions, indices = HEADER.unpack_from(payload)
    index_offset = HEADER.size + positions * 8
    if version != ENCODING_VERSION or not _valid_counts(positions, indices) or len(payload) != index_offset + indices * 4:
        raise MeshStorageError("Invalid mesh payload")
    vertices = list(struct.unpack_from(f"<{positions}d", payload, HEADER.size))
    faces = list(struct.unpack_from(f"<{indices}I", payload, index_offset))
    if any(not math.isfinite(value) or abs(value) > 10000 for value in vertices) or max(faces) >= positions // 3:
        raise MeshStorageError("Invalid mesh geometry")
    return {"positions": vertices, "indices": faces}


class PostgresMeshStore:
    def __init__(self, connect: Callable[[], AbstractContextManager[psycopg.Connection]]):
        self.connect = connect

    @classmethod
    def for_url(cls, database_url: str) -> PostgresMeshStore | None:
        return cls(partial(psycopg.connect, database_url, connect_timeout=3)) if database_url else None

    @contextmanager
    def _transaction(self):
        try:
            with self.connect() as conn:
                conn.execute("SET LOCAL statement_timeout = '5s'")
                yield conn
        except psycopg.Error as exc:
            raise MeshStorageError(f"PostgreSQL mesh access failed ({type(exc).__name__})") from None

    def reference_project(self, project: dict) -> dict:
        """Persist the assets before returning a project record that can reference them."""
        assets = {}
        parts = []
        for part in project.get("parts", []):
            if not isinstance(part.get("mesh"), dict):
                parts.append(part)
                continue
            payload = encode_mesh(part["mesh"])
            digest = hashlib.sha256(payload).digest()
            assets.setdefault(digest, payload)
            parts.append({key: value for key, value in part.items() if key != "mesh"} | {MESH_REFERENCE: digest.hex()})
        if not assets:
            return project

        with self._transaction() as conn:
            existing = {row[0] for row in conn.execute(
                "SELECT sha256 FROM brick_it_catalog.mesh_assets WHERE sha256 = ANY(%s)", (list(assets),)
            )}
            with conn.cursor() as cursor:
                # Take conflicting row locks in the same order for concurrent jobs.
                cursor.executemany(INSERT_MESH, ((digest, assets[digest]) for digest in sorted(assets.keys() - existing)))
        return {**project, "parts": parts}

    def restore_project(self, project: dict) -> dict:
        refs = set()
        for part in project.get("parts", []):
            if MESH_REFERENCE not in part:
                continue
            ref = part[MESH_REFERENCE]
            if not isinstance(ref, str) or not SHA256_HEX.fullmatch(ref):
                raise MeshStorageError("Invalid mesh reference")
            refs.add(bytes.fromhex(ref))
        if not refs:
            return project

        with self._transaction() as conn:
            meshes = {digest.hex(): decode_mesh(payload) for digest, payload in conn.execute(
                "SELECT sha256, payload FROM brick_it_catalog.mesh_assets WHERE sha256 = ANY(%s)",
                (list(refs),), binary=True,
            )}
        if len(meshes) != len(refs):
            raise MeshStorageError("Referenced mesh was not found")
        parts = [{key: value for key, value in part.items() if key != MESH_REFERENCE} | {"mesh": meshes[part[MESH_REFERENCE]]}
                 if MESH_REFERENCE in part else part for part in project["parts"]]
        return {**project, "parts": parts}

    def editor_descriptor(self, ldraw_id: str, build: Callable[[], dict]) -> dict:
        try:
            with self._transaction() as conn:
                cached = conn.execute("""
                    SELECT editor.descriptor, asset.payload
                    FROM brick_it_catalog.editor_meshes AS editor
                    JOIN brick_it_catalog.mesh_assets AS asset ON asset.sha256 = editor.mesh_sha256
                    WHERE editor.ldraw_id = %s AND editor.sdk_commit = %s
                    """, (ldraw_id, SDK_COMMIT), binary=True).fetchone()
            if cached is not None:
                return {**cached[0], "mesh": decode_mesh(cached[1])}
        except MeshStorageError as exc:
            LOGGER.warning("Using local editor mesh: %s", exc)
            return build()

        descriptor = build()
        try:
            payload = encode_mesh(descriptor["mesh"])
            digest = hashlib.sha256(payload).digest()
            metadata = {key: value for key, value in descriptor.items() if key != "mesh"}
            with self._transaction() as conn:
                conn.execute(INSERT_MESH, (digest, payload))
                conn.execute("""
                    INSERT INTO brick_it_catalog.editor_meshes (ldraw_id, sdk_commit, mesh_sha256, descriptor)
                    VALUES (%s, %s, %s, %s) ON CONFLICT (ldraw_id, sdk_commit) DO NOTHING
                    """, (ldraw_id, SDK_COMMIT, digest, Jsonb(metadata)))
        except MeshStorageError as exc:
            LOGGER.warning("Using local editor mesh: %s", exc)
        return descriptor
