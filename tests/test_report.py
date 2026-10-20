import json
from pathlib import Path

from quality_report.ci import RunContext
from quality_report.complexity import FunctionStat
from quality_report.config import DEFAULT_EXCLUDES
from quality_report.deadcode import GO_FRAGMENT
from quality_report.report import (
    _measure_dead_code,
    build_report,
    format_summary,
    run,
)

FIX = Path(__file__).parent / "fixtures"
CTX_ENV = {"QUALITY_REPO": "t", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b"}
SKIPPED = {"result": "skipped", "baseline_commit": None, "violations": []}


def test_build_report_shape():
    r = build_report(
        RunContext("r", "c", "main", "local", None),
        ["typescript"],
        {},
        None,
        None,
        None,
        None,
        SKIPPED,
    )
    assert (
        r["schema"] == 1
        and r["duplication"] is None
        and r["generated_at"].endswith("Z")
    )
    assert r["ci"] == {"system": "local", "run": None}
    assert list(r) == [
        "schema",
        "repo",
        "commit",
        "branch",
        "ci",
        "generated_at",
        "tools",
        "languages",
        "size",
        "duplication",
        "dead_code",
        "complexity",
        "cognitive",
        "coverage",
        "test_gap",
        "ratchet",
    ]
    assert r["coverage"] is None and r["test_gap"] is None


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
    baseline = {
        "schema": 1,
        "commit": "base",
        "duplication_percent": None,
        "dead_code_total": 0,
        "complexity_threshold": None,
        "functions_over_threshold": None,
    }
    repo = _repo_with_baseline(tmp_path, json.dumps(baseline))
    (repo / "package.json").write_text(
        "{}"
    )  # a typescript repo, so the fragment counts
    (repo / ".quality").mkdir()
    (repo / ".quality" / "deadcode-ts.json").write_text(
        (FIX / "knip-sample.json").read_text()
    )
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["ratchet"]["result"] == "fail"
    assert r["ratchet"]["baseline_commit"] == "base"


def test_run_skips_ratchet_for_unreadable_or_wrong_schema_baseline(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PATH", "")
    for content in ("{not json", json.dumps({"schema": 2}), "[]"):
        repo = _repo_with_baseline(tmp_path, content)
        r = run(repo, tmp_path / "r.json", env=CTX_ENV)
        assert r["ratchet"] == SKIPPED
        assert "ratchet skipped" in capsys.readouterr().err
        (repo / ".quality-baseline.json").unlink()
        repo.rmdir()


def test_format_summary_lists_offenders_highest_first_capped_at_ten():
    fns = [FunctionStat(f"f{i}.ts", f"fn{i}", 11 + i, 5) for i in range(12)] + [
        FunctionStat("ok.ts", "ok", 3, 2)
    ]
    report = {
        "duplication": {"percent": 1.5},
        "dead_code": {"total": 4},
        "complexity": {"threshold": 10, "functions_over_threshold": 12, "max": 22},
        "cognitive": {"functions_over_threshold": 3},
        "ratchet": {"result": "pass"},
    }
    lines = format_summary(report, fns)
    assert (
        lines[0]
        == "quality: duplication 1.5%, dead code 4, functions over threshold 12, max CCN 22, functions over cognitive 3, ratchet pass"
    )
    assert len(lines) == 11
    assert lines[1] == "  f11.ts:fn11 22"
    assert lines[10] == "  f2.ts:fn2 13"


def test_format_summary_null_metrics_are_na():
    report = {
        "duplication": None,
        "dead_code": None,
        "complexity": None,
        "cognitive": None,
        "ratchet": SKIPPED,
    }
    assert format_summary(report, []) == [
        "quality: duplication n/a, dead code n/a, functions over threshold n/a, max CCN n/a, functions over cognitive n/a, ratchet skipped"
    ]


def test_run_without_code_languages_skips_duplication_with_warning(
    tmp_path, monkeypatch, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["duplication"] is None
    assert "duplication skipped" in capsys.readouterr().err


def test_run_config_override_lets_duplication_run_without_languages(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".quality.yml").write_text("duplication_formats: [yaml]\n")
    seen = []
    monkeypatch.setattr(
        "quality_report.report.run_jscpd",
        lambda repo, exclude, min_tokens, out_dir, formats: seen.append(formats),
    )
    run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert seen == [["yaml"]]


def _fragment(repo: Path, content: str) -> None:
    (repo / ".quality").mkdir(exist_ok=True)
    (repo / ".quality" / "deadcode-ts.json").write_text(content)


def _rust_repo(tmp_path: Path, monkeypatch, machete) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Cargo.toml").write_text("")
    (repo / "a.rs").write_text("#[allow(dead_code)]\n")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        "quality_report.deadcode.run_cargo_machete", lambda repo: machete
    )
    return repo


def test_dead_code_null_when_knip_fragment_unusable_even_with_rust_ok(
    tmp_path, monkeypatch
):
    repo = _rust_repo(tmp_path, monkeypatch, machete=0)
    (repo / "package.json").write_text("{}")
    _fragment(repo, "")
    assert run(repo, tmp_path / "r.json", env=CTX_ENV)["dead_code"] is None


def test_dead_code_null_when_machete_broken_in_rust_repo(tmp_path, monkeypatch):
    repo = _rust_repo(tmp_path, monkeypatch, machete=None)
    (repo / "package.json").write_text("{}")
    _fragment(repo, (FIX / "knip-sample.json").read_text())
    assert run(repo, tmp_path / "r.json", env=CTX_ENV)["dead_code"] is None


def test_dead_code_missing_fragment_is_not_broken(tmp_path, monkeypatch):
    repo = _rust_repo(tmp_path, monkeypatch, machete=2)
    by_kind = run(repo, tmp_path / "r.json", env=CTX_ENV)["dead_code"]["by_kind"]
    assert by_kind["unused_dependencies"] == 2 and by_kind["suppressed"] == 1


def test_dead_code_ignores_other_fragments(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".quality").mkdir()
    (repo / ".quality" / "deadcode-py.json").write_text('{"vulture": []}')
    monkeypatch.setenv("PATH", "")
    assert run(repo, tmp_path / "r.json", env=CTX_ENV)["dead_code"] is None


def _ts_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "ts"
    repo.mkdir()
    (repo / "package.json").write_text("{}")
    return repo


def test_dead_code_null_when_ts_fragment_missing(tmp_path, capsys):
    repo = _ts_repo(tmp_path)
    assert _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["typescript"]) is None
    err = capsys.readouterr().err
    assert "typescript detected but .quality/deadcode-ts.json is missing" in err


def test_dead_code_null_with_warning_for_language_without_source(tmp_path, capsys):
    assert _measure_dead_code(tmp_path, list(DEFAULT_EXCLUDES), ["cobol"]) is None
    assert "cobol detected but has no dead-code source" in capsys.readouterr().err


def test_dead_code_null_when_go_fragment_missing(tmp_path, capsys):
    repo = _ts_repo(tmp_path)
    _fragment(repo, (FIX / "knip-sample.json").read_text())
    assert (
        _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["go", "typescript"]) is None
    )
    assert "go detected but .quality/deadcode-go.json is missing" in (
        capsys.readouterr().err
    )


def test_dead_code_null_when_go_fragment_unusable(tmp_path):
    repo = _ts_repo(tmp_path)
    (repo / ".quality").mkdir()
    (repo / ".quality" / GO_FRAGMENT).write_text("{}")
    assert _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["go"]) is None


def test_dead_code_with_go_and_ts_fragments(tmp_path):
    repo = _ts_repo(tmp_path)
    _fragment(repo, (FIX / "knip-sample.json").read_text())
    (repo / ".quality" / GO_FRAGMENT).write_text(
        (FIX / "go-deadcode-sample.json").read_text()
    )
    ts_only = _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["typescript"])
    both = _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["go", "typescript"])
    assert both["total"] == ts_only["total"] + 1
    assert both["by_kind"]["unused_symbols"] == ts_only["by_kind"]["unused_symbols"] + 1


def test_dead_code_null_without_languages(tmp_path):
    assert _measure_dead_code(tmp_path, list(DEFAULT_EXCLUDES), []) is None


def test_dead_code_ts_and_rust_shapes_unchanged(tmp_path, monkeypatch):
    repo = _rust_repo(tmp_path, monkeypatch, machete=2)
    _fragment(repo, (FIX / "knip-sample.json").read_text())
    d = _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["rust", "typescript"])
    assert d is not None and d["by_kind"]["unused_dependencies"] >= 2


def test_dead_code_python_counts_flow_into_by_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "quality_report.report.scan_python",
        lambda repo, exclude: {"unused_symbols": 3, "unused_dependencies": 1},
    )
    d = _measure_dead_code(tmp_path, list(DEFAULT_EXCLUDES), ["python"])
    assert d is not None and d["total"] == 4
    assert d["by_kind"]["unused_symbols"] == 3


def test_measure_cognitive_warns_when_cogc_missing(capsys):
    from quality_report.report import _measure_cognitive

    assert _measure_cognitive([FunctionStat("a.ts", "f", 1, 2)], 15) is None
    assert "quality-report: warning:" in capsys.readouterr().err
    assert _measure_cognitive([], 15) is None
    assert capsys.readouterr().err == ""


def test_run_reports_coverage_from_fragment(tmp_path, monkeypatch):
    repo = _ts_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.ts").write_text("export const a = 1;\n")
    (repo / ".quality").mkdir()
    (repo / ".quality" / "coverage-ts.lcov").write_text(
        f"SF:{repo}/src/a.ts\nDA:1,1\nDA:2,0\nend_of_record\n"
    )
    monkeypatch.setenv("PATH", "")
    cov = run(repo, tmp_path / "r.json", env=CTX_ENV)["coverage"]
    assert cov["lines_percent"] == 50.0 and cov["languages"] == ["typescript"]


def test_run_coverage_honours_config_and_host_workspace(tmp_path, monkeypatch):
    repo = _ts_repo(tmp_path)
    (repo / "Cargo.toml").write_text("")
    (repo / ".quality.yml").write_text("coverage_languages: [typescript]\n")
    (repo / "a.ts").write_text("export const a = 1;\n")
    (repo / ".quality").mkdir()
    (repo / ".quality" / "coverage-ts.lcov").write_text(
        "SF:/home/runner/work/r/r/a.ts\nDA:1,1\nend_of_record\n"
    )
    monkeypatch.setenv("PATH", "")
    env = {**CTX_ENV, "QUALITY_HOST_WORKSPACE": "/home/runner/work/r/r"}
    cov = run(repo, tmp_path / "r.json", env=env)["coverage"]
    assert cov["lines_percent"] == 100.0
    assert cov["untested_languages"] == ["rust"]


def test_run_coverage_null_without_fragment(tmp_path, monkeypatch, capsys):
    repo = _ts_repo(tmp_path)
    monkeypatch.setenv("PATH", "")
    assert run(repo, tmp_path / "r.json", env=CTX_ENV)["coverage"] is None
    assert "coverage-ts*.lcov" in capsys.readouterr().err


def _py_scripts_repo(tmp_path: Path) -> Path:
    """Manifest-less Python (helm-argo-cd's scripts/), with a valid coverage fragment."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "tool.py").write_text("def f():\n    return 1\n")
    (repo / ".quality").mkdir()
    (repo / ".quality" / "coverage-py.lcov").write_text(
        "SF:scripts/tool.py\nDA:1,1\nDA:2,0\nend_of_record\n"
    )
    return repo


def test_manifest_less_python_has_null_coverage_without_declaration(
    tmp_path, monkeypatch
):
    repo = _py_scripts_repo(tmp_path)
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["languages"] == [] and r["coverage"] is None
    assert "declared_languages" not in r


def test_declared_language_is_measured_and_shown(tmp_path, monkeypatch):
    repo = _py_scripts_repo(tmp_path)
    (repo / ".quality.yml").write_text("languages: [python]\n")
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["languages"] == ["python"]
    assert r["declared_languages"] == ["python"]
    assert list(r).index("declared_languages") == list(r).index("languages") + 1
    assert r["coverage"]["lines_percent"] == 50.0
    assert r["coverage"]["languages"] == ["python"]


def test_declared_language_adds_to_detected(tmp_path, monkeypatch, capsys):
    repo = _py_scripts_repo(tmp_path)
    (repo / "package.json").write_text("{}")
    (repo / ".quality.yml").write_text(
        "languages: [python, typescript]\ncoverage_languages: [python]\n"
    )
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["languages"] == ["python", "typescript"]
    assert r["declared_languages"] == ["python"]
    assert r["coverage"]["untested_languages"] == ["typescript"]
    assert "typescript declared in .quality.yml languages but already detected" in (
        capsys.readouterr().err
    )


def test_declared_language_without_fragment_nulls_coverage(tmp_path, monkeypatch):
    repo = _py_scripts_repo(tmp_path)
    (repo / ".quality.yml").write_text("languages: [python, go]\n")
    monkeypatch.setenv("PATH", "")
    r = run(repo, tmp_path / "r.json", env=CTX_ENV)
    assert r["declared_languages"] == ["go", "python"]
    assert r["coverage"] is None and r["dead_code"] is None


def test_build_report_declared_languages_follow_languages():
    r = build_report(
        RunContext("r", "c", "main", "local", None),
        ["python"],
        {},
        None,
        None,
        None,
        None,
        SKIPPED,
        declared_languages=["python"],
    )
    keys = list(r)
    assert keys[keys.index("languages") + 1] == "declared_languages"
