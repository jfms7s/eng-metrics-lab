import shutil
from pathlib import Path

import pytest

from quality_report.config import DEFAULT_EXCLUDES
from quality_report.duplication import (
    duplication_formats,
    jscpd_ignore_globs,
    parse_jscpd_report,
    run_jscpd,
)


def test_parse_jscpd_report_totals():
    d = parse_jscpd_report(Path("tests/fixtures/jscpd-sample.json"), 50)
    assert set(d) == {"percent", "duplicated_lines", "clones", "min_tokens"}
    assert d["min_tokens"] == 50 and isinstance(d["percent"], float)
    assert d["clones"] >= 1 and d["duplicated_lines"] > 0


def test_parse_jscpd_missing_or_invalid_is_none(tmp_path, capsys):
    assert parse_jscpd_report(tmp_path / "nope.json", 50) is None
    (tmp_path / "bad.json").write_text("{")
    assert parse_jscpd_report(tmp_path / "bad.json", 50) is None
    (tmp_path / "empty.json").write_text("{}")
    assert parse_jscpd_report(tmp_path / "empty.json", 50) is None
    assert capsys.readouterr().err.count("quality-report: warning:") == 3


def test_ignore_globs_cover_top_level_and_nested():
    globs = jscpd_ignore_globs(
        ["node_modules", "*.d.ts", "go.sum", "**/migrations/**", ".quality"]
    )
    assert {
        "**/node_modules/**",
        "**/*.d.ts",
        "**/go.sum",
        "**/migrations/**",
        "**/.quality/**",
    } <= set(globs)


def test_ignore_globs_accept_all_defaults():
    assert len(jscpd_ignore_globs(list(DEFAULT_EXCLUDES))) >= len(DEFAULT_EXCLUDES)


def test_run_jscpd_missing_warns_and_returns_none(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert run_jscpd(tmp_path, [], 50, tmp_path / "out", ["typescript"]) is None
    assert capsys.readouterr().err.startswith("quality-report: warning:")


@pytest.mark.tools
@pytest.mark.skipif(shutil.which("jscpd") is None, reason="jscpd not installed")
def test_run_jscpd_honours_excludes(tmp_path):
    body = "\n".join(
        f"function f{i}(a, b) {{ const x = a + b * {i}; if (x > 3) {{ return x - a; }} return x + b; }}"
        for i in range(40)
    )
    repo = tmp_path / "repo"
    for rel in [
        "src/a.ts",
        "node_modules/x/b.ts",
        "sub/node_modules/c.ts",
        "types.d.ts",
        "package-lock.json",
    ]:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n")
    report = run_jscpd(
        repo, list(DEFAULT_EXCLUDES), 50, tmp_path / "out", ["typescript"]
    )
    assert report is not None
    assert parse_jscpd_report(report, 50)["clones"] == 0  # only src/a.ts survives

    (repo / "src" / "b.ts").write_text(body + "\n")
    report = run_jscpd(
        repo, list(DEFAULT_EXCLUDES), 50, tmp_path / "out2", ["typescript"]
    )
    assert parse_jscpd_report(report, 50)["clones"] == 1


def test_duplication_formats_derived_from_languages():
    assert duplication_formats(["typescript"], None) == [
        "typescript",
        "javascript",
        "tsx",
        "jsx",
    ]
    assert duplication_formats(["go", "python", "rust"], None) == [
        "go",
        "python",
        "rust",
    ]
    assert duplication_formats([], None) == []


def test_duplication_formats_override_wins():
    assert duplication_formats(["typescript"], ["sql"]) == ["sql"]
    assert duplication_formats([], ["yaml"]) == ["yaml"]
    assert duplication_formats(["rust"], []) == []
