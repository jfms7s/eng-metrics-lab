"""quality-collector: load quality-report artifacts from GitHub into Postgres."""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
import requests

from collector.github import (
    Artifact,
    CollectorError,
    download_report,
    list_report_artifacts,
    new_session,
)
from collector.migrate import migrate
from quality_report.store import connect, insert_report
from quality_report.tools import warn

DEFAULT_LOOKBACK_DAYS = 3


@dataclass
class CollectStats:
    inserted: int = 0
    skipped: int = 0
    rejected: int = 0
    failed: int = 0


class _Rejected(Exception):
    """An artifact that must not be stored; the reason is safe to log."""


def _check_trusted(repo: str, artifact: Artifact, report: dict | None = None) -> None:
    """Artifact contents and origin are untrusted input: raise _Rejected unless they match `repo`."""
    if not artifact.from_trusted_repo:
        raise _Rejected("produced by a fork")
    if report is None:
        return
    if report.get("repo") != repo:
        raise _Rejected("report names a different repository")
    if report.get("commit") != artifact.head_sha:
        raise _Rejected("report commit differs from the run's commit")


def _collect_repo(conn, session, owner: str, repo: str, since: datetime) -> CollectStats:
    stats = CollectStats()
    for artifact in list_report_artifacts(session, owner, repo, since):
        try:
            _check_trusted(repo, artifact)
            report = download_report(session, owner, repo, artifact.id)
            _check_trusted(repo, artifact, report)
            stored = insert_report(conn, report)
        except _Rejected as exc:
            # Fork artifacts recur every night, so a rejection is reported but never fails the run.
            warn(f"{repo}: artifact {artifact.id} rejected ({exc})")
            stats.rejected += 1
            continue
        except psycopg.OperationalError:
            raise  # connection-level failure: fatal for the run, handled in _cmd_collect
        except psycopg.Error as exc:
            # A report the database rejects (bad value, constraint) must not poison the run.
            conn.rollback()
            warn(f"{repo}: artifact {artifact.id} failed ({_db_error(exc)})")
            stats.failed += 1
            continue
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            # Message may carry a URL or payload fragment; keep to class (and status, if any).
            status = getattr(getattr(exc, "response", None), "status_code", None)
            warn(f"{repo}: artifact {artifact.id} failed ({type(exc).__name__}"
                 + (f", HTTP {status}" if status else "") + ")")
            stats.failed += 1
            continue
        if stored:
            stats.inserted += 1
        else:
            stats.skipped += 1
    return stats


def collect(conn, session, owner: str, repos: list[str], since: datetime) -> CollectStats:
    """Load every recent report of `repos`; a CollectorError from GitHub propagates."""
    total = CollectStats()
    for repo in repos:
        stats = _collect_repo(conn, session, owner, repo, since)
        print(
            f"{repo} inserted={stats.inserted} skipped={stats.skipped} "
            f"rejected={stats.rejected} failed={stats.failed}"
        )
        total.inserted += stats.inserted
        total.skipped += stats.skipped
        total.rejected += stats.rejected
        total.failed += stats.failed
    return total


def _missing(names: list[str]) -> str | None:
    return next((n for n in names if not os.environ.get(n)), None)


def _db_error(exc: psycopg.Error) -> str:
    # libpq echoes fragments of the connection string (password included): class + SQLSTATE only.
    return type(exc).__name__ + (f" (sqlstate {exc.sqlstate})" if exc.sqlstate else "")


def _cmd_collect(_args: argparse.Namespace) -> int:
    missing = _missing(
        ["QUALITY_COLLECTOR_URL", "GITHUB_TOKEN", "QUALITY_GITHUB_OWNER", "QUALITY_GITHUB_REPOS"]
    )
    if missing:
        print(f"error: {missing} is not set", file=sys.stderr)
        return 2
    repos = [r.strip() for r in os.environ["QUALITY_GITHUB_REPOS"].split(",") if r.strip()]
    if not repos:
        print("error: QUALITY_GITHUB_REPOS names no repositories", file=sys.stderr)
        return 2
    try:
        days = int(os.environ.get("QUALITY_LOOKBACK_DAYS") or DEFAULT_LOOKBACK_DAYS)
    except ValueError:
        print("error: QUALITY_LOOKBACK_DAYS must be an integer", file=sys.stderr)
        return 2
    since = datetime.now(UTC) - timedelta(days=days)
    owner = os.environ["QUALITY_GITHUB_OWNER"]
    session = new_session(os.environ["GITHUB_TOKEN"])

    exit_code = 0
    try:
        with connect(os.environ["QUALITY_COLLECTOR_URL"]) as conn:
            for repo in repos:
                try:
                    stats = collect(conn, session, owner, [repo], since)
                except CollectorError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    exit_code = 1
                    continue
                if stats.failed:
                    exit_code = 1
    except psycopg.Error as exc:
        print(f"error: database error: {_db_error(exc)}", file=sys.stderr)
        return 1
    return exit_code


def _cmd_migrate(_args: argparse.Namespace) -> int:
    missing = _missing([
        "QUALITY_ADMIN_URL", "QUALITY_WRITER_PASSWORD",
        "QUALITY_COLLECTOR_PASSWORD", "QUALITY_GRAFANA_PASSWORD",
    ])
    if missing:
        print(f"error: {missing} is not set", file=sys.stderr)
        return 2
    passwords = {
        "writer": os.environ["QUALITY_WRITER_PASSWORD"],
        "collector": os.environ["QUALITY_COLLECTOR_PASSWORD"],
        "grafana": os.environ["QUALITY_GRAFANA_PASSWORD"],
    }
    try:
        migrate(os.environ["QUALITY_ADMIN_URL"], passwords)
    except psycopg.Error as exc:
        print(f"error: database error: {_db_error(exc)}", file=sys.stderr)
        return 1
    print("migrated")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quality-collector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="load recent quality-report artifacts").set_defaults(
        func=_cmd_collect)
    sub.add_parser("migrate", help="create the schema and roles").set_defaults(func=_cmd_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
