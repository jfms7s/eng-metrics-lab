from quality_report.complexity import FunctionStat
from quality_report.coverage import CoverageData, FileCoverage
from quality_report.report import run
from quality_report.testgap import (
    ScoredFunction,
    crap,
    score_functions,
    summarize_test_gap,
)


def _data(files: dict[str, FileCoverage]) -> CoverageData:
    return CoverageData(
        files=files,
        languages=["python"],
        untested_languages=[],
        branch_languages=[],
        unmatched_files=0,
    )


def _fn(start: int = 10, end: int = 20, file: str = "a.py") -> FunctionStat:
    return FunctionStat(file, "f", 10, 5, start=start, end=end)


def test_crap_values():
    assert crap(1, 1.0) == 1.0
    assert crap(10, 0.0) == 110.0
    assert crap(10, 0.5) == 22.5


def test_score_uses_lines_inside_function_range():
    lines = {n: 1 for n in range(10, 15)}
    lines |= {n: 0 for n in range(15, 20)}
    lines |= {5: 0, 6: 0, 25: 1}  # outside the function
    data = _data({"a.py": FileCoverage(lines=lines)})
    scored, without = score_functions([_fn()], data)
    assert without == 0
    assert scored == [ScoredFunction("a.py", "f", 10, 10, 0.5, 22.5)]


def test_function_without_instrumented_lines_is_skipped():
    data = _data({"a.py": FileCoverage(lines={1: 1, 50: 0})})
    assert score_functions([_fn()], data) == ([], 0)


def test_function_in_file_without_data_is_counted_not_scored():
    data = _data({"b.py": FileCoverage(lines={10: 1})})
    assert score_functions([_fn()], data) == ([], 1)


def test_nested_function_lines_excluded_from_outer():
    lines = {1: 1, 2: 1, 19: 1, 20: 1} | {n: 0 for n in range(5, 16)}
    data = _data({"a.py": FileCoverage(lines=lines)})
    outer = FunctionStat("a.py", "outer", 11, 20, start=1, end=20)
    inner = FunctionStat("a.py", "outer.inner", 3, 11, start=5, end=15)
    scored, without = score_functions([outer, inner], data)
    assert without == 0
    by_name = {s.name: s for s in scored}
    assert by_name["outer"].coverage == 1.0
    assert by_name["outer"].crap == 11.0
    assert by_name["outer.inner"].coverage == 0.0


def test_function_of_untested_language_is_not_counted():
    data = _data({"a.py": FileCoverage(lines={10: 1})})
    data.untested_languages = ["rust"]
    rust = FunctionStat("src/lib.rs", "g", 4, 9, start=1, end=9)
    py_missing = FunctionStat("b.py", "h", 4, 9, start=1, end=9)
    assert score_functions([rust], data) == ([], 0)
    assert score_functions([rust, py_missing], data) == ([], 1)


def test_summarize_test_gap():
    scored = [ScoredFunction("a.py", "f", 1, 5, 0.5, c) for c in (2.0, 31.0, 110.0)]
    assert summarize_test_gap(scored, 0) == {
        "metric": "crap",
        "threshold": 30,
        "functions_over_threshold": 2,
        "max": 110.0,
        "functions_scored": 3,
        "functions_without_data": 0,
    }


def test_summarize_test_gap_none_when_nothing_scored():
    assert summarize_test_gap([], 3) is None


def test_test_gap_null_when_coverage_null(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    monkeypatch.setattr("quality_report.report.run_lizard", lambda *a, **k: None)
    monkeypatch.setattr("quality_report.report.run_jscpd", lambda *a, **k: None)
    env = {"QUALITY_REPO": "t", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b"}
    assert run(repo, tmp_path / "out.json", env=env)["test_gap"] is None


def test_measure_test_gap_warns_when_nothing_scored(capsys):
    from quality_report.report import _measure_test_gap

    data = _data({"b.py": FileCoverage(lines={10: 1})})
    assert _measure_test_gap([_fn(), _fn()], data) is None
    err = capsys.readouterr().err
    assert "quality-report: warning:" in err and "2 functions have no coverage" in err
    assert _measure_test_gap([], data) is None
    assert capsys.readouterr().err == ""
