import json
from pathlib import Path

from quality_report.ci import RunContext
from quality_report.complexity import FunctionStat
from quality_report.report import build_report, format_summary, run

FIX = Path(__file__).parent / "fixtures"
CTX_ENV = {"QUALITY_REPO": "t", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b"}
SKIPPED = {"result": "skipped", "baseline_commit": None, "violations": []}


def test_build_report_shape():
    r = build_report(RunContext("r", "c", "main", "local", None), ["typescript"], {}, None, None, None, None,
                     SKIPPED)
    assert r["schema"] == 1 and r["duplication"] is None and r["generated_at"].endswith("Z")
    assert r["ci"] == {"system": "local", "run": None}
    assert list(r) == ["schema", "repo", "commit", "branch", "ci", "generated_at", "tools",
                       "languages", "size", "duplication", "dead_code", "complexity", "ratchet"]


def test_run_with_missing_jscpd_sets_duplication_null(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")  # no jscpd, no lizard
    r = run(FIX / "ts-mini", tmp_path / "r.json", env=CTX_ENV)
    assert r["duplication"] is None and r["complexity"] is None and r["size"] is None
    assert r["dead_code"]["by_kind"]["unused_files"] == 1  # fragment still read
    assert r["ratchet"] == SKIPPED
    assert json.loads((tmp_path / "r.json").read_text()) == r


def test_run_writes_jscpd_output_beside_report_not_in_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out" / "r.json"
    monkeypatch.setenv("PATH", "")
    run(repo, out, env=CTX_ENV)
    assert out.is_file() and list(repo.iterdir()) == []


def _repo_with_baseline(tmp_path, content: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".quality-baseline.json").write_text(content)
    return repo


def test_run_embeds_ratchet_from_valid_baseline(tmp_path, monkeypatch):
    baseline = {"schema": 1, "commit": "base", "duplication_percent": None, "dead_code_total": 0,
                "complexity_threshold": None, "functions_over_threshold": None}
    repo = _repo_with_baseline(tmp_path, json.dumps(baseline))
    (repo / ".quality").mkdir()
    (repo / ".quality" / "deadcode-ts.json").write_text((FIX / "knip-sample.json").read_text())
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["ratchet"]["result"] == "fail"
    assert r["ratchet"]["baseline_commit"] == "base"


def test_run_skips_ratchet_for_unreadable_or_wrong_schema_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    for content in ("{not json", json.dumps({"schema": 2}), "[]"):
        repo = _repo_with_baseline(tmp_path, content)
        r = run(repo, tmp_path / "r.json", env=CTX_ENV)
        assert r["ratchet"] == SKIPPED
        assert "ratchet skipped" in capsys.readouterr().err
        (repo / ".quality-baseline.json").unlink()
        repo.rmdir()


def test_format_summary_lists_offenders_highest_first_capped_at_ten():
    fns = [FunctionStat(f"f{i}.ts", f"fn{i}", 11 + i, 5) for i in range(12)] + [FunctionStat("ok.ts", "ok", 3, 2)]
    report = {"duplication": {"percent": 1.5}, "dead_code": {"total": 4},
              "complexity": {"threshold": 10, "functions_over_threshold": 12, "max": 22},
              "ratchet": {"result": "pass"}}
    lines = format_summary(report, fns)
    assert lines[0] == "quality: duplication 1.5%, dead code 4, functions over threshold 12, max CCN 22, ratchet pass"
    assert len(lines) == 11
    assert lines[1] == "  f11.ts:fn11 22"
    assert lines[10] == "  f2.ts:fn2 13"


def test_format_summary_null_metrics_are_na():
    report = {"duplication": None, "dead_code": None, "complexity": None, "ratchet": SKIPPED}
    assert format_summary(report, []) == [
        "quality: duplication n/a, dead code n/a, functions over threshold n/a, max CCN n/a, ratchet skipped"]
