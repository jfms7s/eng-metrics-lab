"""Flatten a schema-1 report into a quality_reports row and insert it."""
from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

_JSON_COLUMNS = ("dead_by_kind", "cc_over_by", "ratchet_violations", "raw")

_COLUMNS = (
    "repo", "commit", "branch", "generated_at", "ci_system",
    "dup_percent", "dup_lines", "dup_clones",
    "dead_total", "dead_by_kind",
    "cc_threshold", "cc_over", "cc_over_by", "cc_max", "cc_p95", "cc_avg",
    "nloc", "functions",
    "ratchet_result", "ratchet_violations",
    "raw",
)

_INSERT = (
    f"INSERT INTO quality_reports ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(f'%({c})s' for c in _COLUMNS)}) "
    "ON CONFLICT (repo, commit, branch) DO NOTHING"
)


def report_row(report: dict) -> dict[str, object]:
    dup = report.get("duplication") or {}
    dead = report.get("dead_code") or {}
    cx = report.get("complexity") or {}
    size = report.get("size") or {}
    ratchet = report.get("ratchet") or {}
    return {
        "repo": report["repo"],
        "commit": report["commit"],
        "branch": report["branch"],
        "generated_at": report["generated_at"],
        "ci_system": report["ci"]["system"],
        "dup_percent": dup.get("percent"),
        "dup_lines": dup.get("duplicated_lines"),
        "dup_clones": dup.get("clones"),
        "dead_total": dead.get("total"),
        "dead_by_kind": dead.get("by_kind"),
        "cc_threshold": cx.get("threshold"),
        "cc_over": cx.get("functions_over_threshold"),
        "cc_over_by": cx.get("over"),
        "cc_max": cx.get("max"),
        "cc_p95": cx.get("p95"),
        "cc_avg": cx.get("avg"),
        "nloc": size.get("nloc"),
        "functions": size.get("functions"),
        "ratchet_result": ratchet.get("result"),
        "ratchet_violations": ratchet.get("violations"),
        "raw": report,
    }


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


def insert_report(conn: psycopg.Connection, report: dict) -> bool:
    """Insert the report; True if stored, False if (repo, commit, branch) already existed."""
    params = {
        key: Jsonb(value) if key in _JSON_COLUMNS and value is not None else value
        for key, value in report_row(report).items()
    }
    cur = conn.execute(_INSERT, params)
    conn.commit()
    return cur.rowcount == 1
