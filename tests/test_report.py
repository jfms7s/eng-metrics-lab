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
        "ratchet",
    ]


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
        "ratchet": {"result": "pass"},
    }
    lines = format_summary(report, fns)
    assert (
        lines[0]
        == "quality: duplication 1.5%, dead code 4, functions over threshold 12, max CCN 22, ratchet pass"
    )
    assert len(lines) == 11
    assert lines[1] == "  f11.ts:fn11 22"
    assert lines[10] == "  f2.ts:fn2 13"


def test_format_summary_null_metrics_are_na():
    report = {
        "duplication": None,
        "dead_code": None,
        "complexity": None,
        "ratchet": SKIPPED,
    }
    assert format_summary(report, []) == [
        "quality: duplication n/a, dead code n/a, functions over threshold n/a, max CCN n/a, ratchet skipped"
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


def test_dead_code_null_when_ts_fragment_missing(tmp_path):
    repo = _ts_repo(tmp_path)
    assert _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["typescript"]) is None


def test_dead_code_null_when_go_fragment_missing(tmp_path):
    repo = _ts_repo(tmp_path)
    _fragment(repo, (FIX / "knip-sample.json").read_text())
    assert (
        _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["go", "typescript"]) is None
    )


def test_dead_code_null_when_go_fragment_not_yet_parseable(tmp_path):
    repo = _ts_repo(tmp_path)
    (repo / ".quality").mkdir()
    (repo / ".quality" / GO_FRAGMENT).write_text("{}")
    assert _measure_dead_code(repo, list(DEFAULT_EXCLUDES), ["go"]) is None


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
