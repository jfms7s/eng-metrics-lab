import json
from pathlib import Path

import psycopg
import pytest

from collector.migrate import migrate
from quality_report import cli
from quality_report.deadcode import KINDS
from quality_report.store import insert_report, report_row

SAMPLE = Path(__file__).parent / "fixtures" / "sample-report.json"


def test_sample_fixture_matches_real_report_shape():
    from quality_report.ci import RunContext
    from quality_report.complexity import (
        FunctionStat,
        summarize_complexity,
        summarize_size,
    )
    from quality_report.report import build_report

    sample = json.loads(SAMPLE.read_text())
    fns = [FunctionStat(name="f", file="a.py", ccn=11, nloc=3)]
    ctx = RunContext(repo="r", commit="c", branch="b", ci_system="github", run="1")
    real = build_report(
        ctx, ["python"], {"lizard": "1"}, summarize_size(fns),
        {"percent": 1.0, "duplicated_lines": 1, "clones": 1, "min_tokens": 50},
        {"total": 0, "by_kind": dict.fromkeys(KINDS, 0)},
        summarize_complexity(fns, 10), {"result": "pass", "baseline_commit": None, "violations": []},
    )
    assert set(sample) == set(real)
    for section in ("ci", "size", "duplication", "dead_code", "complexity", "ratchet"):
        assert set(sample[section]) == set(real[section]), section
    assert set(sample["dead_code"]["by_kind"]) == set(KINDS)
    assert sample["dead_code"]["total"] == sum(sample["dead_code"]["by_kind"].values())
    assert set(real["ci"]) == {"system", "run"}
    assert isinstance(sample["tools"], dict)


def test_report_row_maps_columns():
    row = report_row(json.loads(SAMPLE.read_text()))
    assert row["dup_percent"] == 3.4 and row["cc_over"] == 57
    assert row["cc_over_by"] == {"7": 112, "10": 57, "15": 19}
    assert row["ratchet_result"] == "pass" and row["raw"]["schema"] == 1
    assert row["ci_system"] == "github" and row["nloc"] == 18400 and row["functions"] == 950
    assert row["dead_total"] == 23 and row["cc_max"] == 48


def test_report_row_null_metrics():
    r = json.loads(SAMPLE.read_text()) | {"dead_code": None}
    assert report_row(r)["dead_total"] is None
    assert report_row(r)["dead_by_kind"] is None


def test_report_row_all_sections_null():
    r = json.loads(SAMPLE.read_text()) | {
        "duplication": None, "complexity": None, "size": None, "ratchet": None}
    row = report_row(r)
    assert row["dup_percent"] is None and row["cc_over_by"] is None
    assert row["nloc"] is None and row["ratchet_result"] is None


def test_store_requires_db_url(monkeypatch, capsys):
    monkeypatch.delenv("QUALITY_DB_URL", raising=False)
    assert cli.main(["store", str(SAMPLE)]) == 2
    assert "QUALITY_DB_URL" in capsys.readouterr().err


def test_store_unreadable_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QUALITY_DB_URL", "postgresql://u:secret@localhost:1/x")
    assert cli.main(["store", str(tmp_path / "missing.json")]) == 2


def test_store_db_error_hides_url(monkeypatch, capsys):
    monkeypatch.setenv("QUALITY_DB_URL", "postgresql://u:secret@127.0.0.1:1/x?connect_timeout=2")
    assert cli.main(["store", str(SAMPLE)]) == 1
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize("url, secret", [
    ("u:secret@host/db", "secret"),
    ("postgresql://u:pa%zzsecret@host/db", "pa%zzsecret"),
])
def test_store_malformed_url_hides_secret(monkeypatch, capsys, url, secret):
    monkeypatch.setenv("QUALITY_DB_URL", url)
    assert cli.main(["store", str(SAMPLE)]) == 1
    out = capsys.readouterr()
    assert secret not in out.out and secret not in out.err


@pytest.mark.db
def test_migrate_is_idempotent(admin_url, passwords):
    migrate(admin_url, passwords)
    migrate(admin_url, passwords)


@pytest.mark.db
def test_insert_duplicate_is_noop(writer_conn, sample):
    assert insert_report(writer_conn, sample) is True
    assert insert_report(writer_conn, sample) is False


@pytest.mark.db
def test_writer_cannot_read_or_delete(writer_conn):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer_conn.execute("select raw from quality_reports")
    writer_conn.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        writer_conn.execute("delete from quality_reports")
    writer_conn.rollback()


@pytest.mark.db
def test_grafana_can_only_select(grafana_conn):
    grafana_conn.execute("select count(*) from quality_reports")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        grafana_conn.execute(
            "insert into hotspots values ('r', current_date, 'f', 1, null, 1, 1, 1, 1)")
    grafana_conn.rollback()


@pytest.mark.db
def test_stored_row_round_trips(admin_url, passwords, writer_conn, sample):
    other = sample | {"branch": "feature/x"}
    assert insert_report(writer_conn, other) is True
    with psycopg.connect(admin_url) as conn:
        row = conn.execute(
            "select cc_over_by, dup_percent, raw->>'schema' from quality_reports "
            "where branch = 'feature/x'").fetchone()
    assert row[0] == {"7": 112, "10": 57, "15": 19} and float(row[1]) == 3.4 and row[2] == "1"
