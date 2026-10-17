import shutil
from pathlib import Path

import pytest

from quality_report.complexity import (
    FunctionStat,
    lizard_exclude_args,
    parse_lizard_csv,
    run_lizard,
    summarize_complexity,
    summarize_size,
)


def fns(*ccns):
    return [FunctionStat("a.ts", f"f{i}", c, 5) for i, c in enumerate(ccns)]


def test_over_is_strictly_greater():
    s = summarize_complexity(fns(7, 10, 11, 16), threshold=10)
    assert s["functions_over_threshold"] == 2
    assert s["over"] == {"7": 3, "10": 2, "15": 1}


def test_p95_nearest_rank_and_avg():
    s = summarize_complexity(fns(*range(1, 21)), threshold=10)  # 1..20
    assert s["p95"] == 19 and s["max"] == 20 and s["avg"] == 10.5


def test_empty_is_none():
    assert summarize_complexity([], 10) is None and summarize_size([]) is None


def test_parse_lizard_csv_handles_header_and_quoted_names():
    rows = parse_lizard_csv(Path("tests/fixtures/lizard-sample.csv").read_text())
    assert any("," in r.name for r in rows)  # a quoted long_name with commas
    assert all(isinstance(r.ccn, int) for r in rows)
    assert len(rows) == 4  # header skipped
    assert {r.file for r in rows} >= {"a.ts", "src/svc.ts"}  # leading ./ stripped


def test_parse_makes_absolute_paths_repo_relative():
    text = '1,2,3,1,1,"f@1-1@/r/src/a.ts","/r/src/a.ts","f","f ( x )",1,1\n'
    assert parse_lizard_csv(text, Path("/r"))[0].file == "src/a.ts"


def test_size_counts_distinct_files():
    s = summarize_size([FunctionStat("a.ts", "f", 1, 3), FunctionStat("a.ts", "g", 1, 4), FunctionStat("b.ts", "h", 1, 5)])
    assert s == {"files": 2, "nloc": 12, "functions": 3}


def test_exclude_args_cover_top_level_and_nested():
    args = lizard_exclude_args(["node_modules", "**/migrations/**", "*.d.ts"])
    patterns = args[1::2]
    assert args[0::2] == ["-x"] * len(patterns)
    assert {"./node_modules/*", "*/node_modules/*", "./migrations/*", "*/migrations/*", "./*.d.ts", "*/*.d.ts"} <= set(patterns)


def test_run_lizard_missing_warns_and_returns_none(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert run_lizard(tmp_path, []) is None
    assert capsys.readouterr().err.startswith("quality-report: warning:")


@pytest.mark.skipif(shutil.which("lizard") is None, reason="lizard not installed")
def test_run_lizard_honours_excludes(tmp_path):
    # distinct bodies: lizard silently drops duplicate files
    for i, rel in enumerate(["src/a.ts", "node_modules/x/b.ts", "sub/node_modules/c.ts", "vendor/v.ts", "types.d.ts"]):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"function f{i}(x) {{ if (x) {{ return {i} }} return 2 }}\n")
    out = run_lizard(tmp_path, ["node_modules", "vendor", "*.d.ts"])
    assert [f.file for f in parse_lizard_csv(out, tmp_path)] == ["src/a.ts"]


def test_run_tool_timeout_warns_and_returns_none(monkeypatch, capsys):
    import subprocess

    from quality_report.tools import run_tool

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert run_tool(["x"], timeout=1) is None
    assert "timed out" in capsys.readouterr().err
