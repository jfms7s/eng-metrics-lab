"""Create the schema and the three database roles (idempotent)."""

from __future__ import annotations

from importlib import resources

import psycopg
from psycopg import sql

ROLES = {
    "writer": "quality_writer",
    "collector": "quality_collector",
    "grafana": "quality_grafana",
}
# role key -> (privileges, tables)
_TABLE_GRANTS = {
    "writer": ("INSERT", ["quality_reports"]),
    "collector": ("SELECT, INSERT, UPDATE, DELETE", ["quality_reports", "hotspots"]),
    "grafana": ("SELECT", ["quality_reports", "hotspots"]),
}


def _schema_sql() -> str:
    return resources.files("collector").joinpath("schema.sql").read_text()


def _ensure_role(cur: psycopg.Cursor, role: str, password: str) -> None:
    cur.execute("select 1 from pg_roles where rolname = %s", (role,))
    name = sql.Identifier(role)
    if cur.fetchone() is None:
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(name))
    cur.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(name, sql.Literal(password))
    )


def _grant_conflict_key_select(cur: psycopg.Cursor, role: sql.Identifier) -> None:
    # INSERT ... ON CONFLICT (repo, commit, branch) needs SELECT on those key columns.
    # Only the key; the writer still cannot read `raw` or any metric.
    cur.execute(
        sql.SQL("GRANT SELECT (repo, commit, branch) ON quality_reports TO {}").format(
            role
        )
    )


def migrate(admin_url: str, passwords: dict[str, str]) -> None:
    """Apply the schema and roles to the database `admin_url` names."""
    with psycopg.connect(admin_url) as conn:
        cur = conn.cursor()
        cur.execute(_schema_sql())
        for key, role in ROLES.items():
            _ensure_role(cur, role, passwords[key])
        cur.execute("select current_database()")
        database = sql.Identifier(cur.fetchone()[0])
        for key, role in ROLES.items():
            name = sql.Identifier(role)
            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, name)
            )
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(name))
            privileges, tables = _TABLE_GRANTS[key]
            for table in tables:
                cur.execute(
                    sql.SQL("GRANT {} ON {} TO {}").format(
                        sql.SQL(privileges), sql.Identifier(table), name
                    )
                )
        _grant_conflict_key_select(cur, sql.Identifier(ROLES["writer"]))
