"""PostgreSQL reader for the published parts catalogue."""

from __future__ import annotations

import logging
import re

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool

from .jobs import ServiceError
from .parts import PART_ID_PREFIX, catalogue_page, normalize_search, part_metadata

LOGGER = logging.getLogger(__name__)
SEARCH_WORDS = re.compile(r"[a-z0-9]+")

RELEASE_SQL = """
    SELECT release.id, release.library_total, release.catalogue_total
    FROM brick_it_catalog.catalog_active AS active
    JOIN brick_it_catalog.catalog_releases AS release ON release.id = active.release_id
"""
CATEGORIES_SQL = """
    SELECT category, category_name, count(*) AS count
    FROM brick_it_catalog.part_catalog
    WHERE release_id = %s
    GROUP BY category, category_name
    ORDER BY count DESC, category_name COLLATE "C"
"""
BROWSE_SQL = """
    SELECT ldraw_id, name, category_name, w, d, h
    FROM brick_it_catalog.part_catalog
    WHERE release_id = %(release)s AND (%(category)s::text IS NULL OR category = %(category)s)
    ORDER BY sort_order
    LIMIT %(limit)s OFFSET %(offset)s
"""
SEARCH_SQL = """
    WITH query AS (
        SELECT plainto_tsquery('pg_catalog.simple', %(terms)s) AS words
    ), matches AS MATERIALIZED (
        SELECT ldraw_id, name, category_name, w, d, h, sort_order,
               CASE WHEN ldraw_key = %(id)s THEN 0
                    WHEN ldraw_key LIKE %(prefix)s THEN 1 ELSE 2 END AS priority,
               search_vector, search_terms, popularity, query.words
        FROM brick_it_catalog.part_catalog CROSS JOIN query
        WHERE release_id = %(release)s AND (%(category)s::text IS NULL OR category = %(category)s)
          AND (ldraw_key = %(id)s OR ldraw_key LIKE %(prefix)s OR search_vector @@ query.words)
    ), page AS (
        SELECT ldraw_id, name, category_name, w, d, h, priority, sort_order,
               CASE WHEN priority = 2 THEN
                   ts_rank(search_vector, words, 1) * (1 + 0.5 * log(1.0 + popularity))
               END AS relevance,
               CASE WHEN priority = 2 THEN word_similarity(%(terms)s, search_terms) END AS similarity
        FROM matches
        ORDER BY priority, relevance DESC, similarity DESC, sort_order
        LIMIT %(limit)s OFFSET %(offset)s
    )
    SELECT total, ldraw_id, name, category_name, w, d, h
    FROM (SELECT count(*) AS total FROM matches) AS counts
    LEFT JOIN page ON true
    ORDER BY priority, relevance DESC, similarity DESC, sort_order
"""


def search_terms(text: str) -> str:
    return " ".join(SEARCH_WORDS.findall(text.lower()))


class PostgresCatalogue:
    def __init__(self, database_url: str):
        try:
            connection_options = conninfo_to_dict(database_url).get("options", "")
        except psycopg.Error:
            raise ValueError("BRICK_IT_PARTS_DATABASE_URL is not a valid PostgreSQL connection string") from None
        self.pool = ConnectionPool(
            database_url, min_size=1, max_size=4, timeout=3, open=False,
            kwargs={"connect_timeout": 3, "options": f"{connection_options} -c statement_timeout=5000".strip()},
        )

    def open(self) -> None:
        self.pool.open()

    def close(self) -> None:
        self.pool.close()

    def list(self, q: str = "", category: str = "", offset: int = 0, limit: int = 24) -> dict:
        if offset < 0 or not 1 <= limit <= 60:
            raise ServiceError("INVALID_PAGINATION", "零件分页范围无效。", 422)

        category = category.strip().lower()
        category = None if category in {"", "all"} else category
        q = q.strip()
        part_id, normalized = normalize_search(q)
        terms = search_terms(normalized)

        try:
            with self.pool.connection() as conn:
                release = conn.execute(RELEASE_SQL).fetchone()
                if release is None:
                    raise ServiceError("PARTS_UNAVAILABLE", "PostgreSQL 零件目录尚未导入。", 503)
                release_id, library_total, catalogue_total = release
                categories = [{"id": key, "label": name, "count": count}
                              for key, name, count in conn.execute(CATEGORIES_SQL, (release_id,))]
                category_total = catalogue_total if category is None else sum(
                    row["count"] for row in categories if row["id"] == category
                )
                params = {"release": release_id, "category": category, "offset": offset, "limit": limit}
                if not category_total or (q and not terms):
                    total, items = 0, []
                elif q:
                    params.update(terms=terms, id=part_id if "\x00" not in part_id else None,
                                  prefix=part_id + "%" if PART_ID_PREFIX.fullmatch(part_id) else None)
                    rows = conn.execute(SEARCH_SQL, params).fetchall()
                    total = rows[0][0]
                    items = [part_metadata(*row[1:]) for row in rows if row[1] is not None]
                else:
                    total = category_total
                    items = [part_metadata(*row) for row in conn.execute(BROWSE_SQL, params)]
        except psycopg.Error as exc:
            LOGGER.warning("PostgreSQL parts catalogue read failed (%s)", type(exc).__name__)
            raise ServiceError("PARTS_UNAVAILABLE", "PostgreSQL 零件目录暂时不可用。", 503) from None

        return catalogue_page(items, categories, total=total, library_total=library_total,
                              catalogue_total=catalogue_total, offset=offset, limit=limit)
