import json
from pathlib import Path

from quality_report.cli import main
from quality_report.feedback import render_feedback

REPORT = {
    "schema": 1,
    "duplication": {"percent": 2.5},
    "dead_code": {"total": 4},
    "complexity": {"functions_over_threshold": 3},
    "cognitive": None,
    "coverage": {"lines_percent": 81.2},
    "test_gap": {"functions_over_threshold": 1},
    "ratchet": {
        "result": "fail",
        "baseline_commit": "abc",
        "violations": [
            {
                "field": "complexity.functions_over_threshold",
                "baseline": 1,
                "current": 3,
                "tolerance": 0,
            },
        ],
    },
}


def _fn(file: str, value: int) -> dict:
    return {"file": file, "function": "f", "line": 1, "value": value}


DETAILS = {
    "schema": 1,
    "complexity": [_fn("a.py", 30), _fn("b.py", 20), _fn("c.py", 12)],
    "cognitive": [],
    "test_gap": [{**_fn("a.py", 99), "coverage": 0.1}],
    "duplication": [
        {
            "a": {"file": "x.py", "start": 1, "end": 9},
            "b": {"file": "y.py", "start": 2, "end": 10},
        }
    ],
}


def test_render_headline_and_ratchet():
    out = render_feedback(REPORT, None, None)
    lines = out.splitlines()
    assert lines[0] == "### quality-report"
    assert "cognitive over n/a" in out
    assert "coverage 81.2%" in out
    assert "ratchet: fail" in out
    assert "- complexity.functions_over_threshold: 3 is worse than baseline 1" in out


def test_render_filters_to_changed_files():
    out = render_feedback(REPORT, DETAILS, {"b.py", "y.py"})
    assert "b.py" in out
    assert "a.py" not in out
    assert "c.py" not in out
    assert "x.py:1-9" in out  # either clone side counts


def test_render_caps_items():
    out = render_feedback(REPORT, DETAILS, None, max_items=2)
    assert out.count("\n- `") == 2
    assert out.rstrip().endswith("… and 3 more")


def _write(tmp_path: Path, details: dict | None = DETAILS) -> list[str]:
    (tmp_path / "report.json").write_text(json.dumps(REPORT))
    args = ["--report", str(tmp_path / "report.json")]
    if details is not None:
        (tmp_path / "details.json").write_text(json.dumps(details))
    return [*args, "--details", str(tmp_path / "details.json")]


def test_feedback_falls_back_when_git_fails(tmp_path, monkeypatch, capsys):
    args = _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["feedback", *args, "--base", "nosuchref"]) == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err
    for name in ("a.py", "b.py", "c.py", "x.py"):
        assert name in captured.out


def test_feedback_without_details_file(tmp_path, capsys):
    args = _write(tmp_path, None)
    assert main(["feedback", *args]) == 0
    out = capsys.readouterr().out
    assert "ratchet: fail" in out
    assert "**" not in out


def test_feedback_missing_report_still_exits_zero(tmp_path, capsys):
    assert main(["feedback", "--report", str(tmp_path / "nope.json")]) == 0
    assert "cannot read report" in capsys.readouterr().err
