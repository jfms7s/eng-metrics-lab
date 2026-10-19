import json
import shutil
from pathlib import Path

from quality_report.complexity import FunctionStat
from quality_report.details import build_details
from quality_report.duplication import parse_jscpd_clones
from quality_report.report import run
from quality_report.testgap import ScoredFunction

FIX = Path(__file__).parent / "fixtures"
CTX_ENV = {"QUALITY_REPO": "t", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b"}


def _fn(file, name, start, ccn, cognitive):
    return FunctionStat(file, name, ccn, 10, start, start + 5, cognitive)


def test_build_details_lists_only_offenders_worst_first():
    functions = [
        _fn("b.ts", "at_limit", 1, 10, 15),  # not strictly over either threshold
        _fn("b.ts", "mid", 5, 12, 20),
        _fn("a.ts", "tie", 9, 12, None),
        _fn("a.ts", "worst", 3, 30, 40),
    ]
    scored = [
        ScoredFunction("a.ts", "ok", 1, 5, 1.0, 30.0),  # CRAP == 30 is not over
        ScoredFunction("a.ts", "low", 2, 9, 0.5, 40.0),
        ScoredFunction("b.ts", "gap", 7, 20, 0.1, 342.0),
    ]
    clones = [
        {
            "a": {"file": "x", "start": 1, "end": 2},
            "b": {"file": "y", "start": 3, "end": 4},
        }
    ]
    d = build_details(functions, 10, 15, scored, clones)
    assert list(d) == ["schema", "complexity", "cognitive", "test_gap", "duplication"]
    assert d["schema"] == 1
    assert [
        (e["file"], e["function"], e["line"], e["value"]) for e in d["complexity"]
    ] == [
        ("a.ts", "worst", 3, 30),
        ("a.ts", "tie", 9, 12),
        ("b.ts", "mid", 5, 12),
    ]
    assert d["cognitive"] == [
        {"file": "a.ts", "function": "worst", "line": 3, "value": 40},
        {"file": "b.ts", "function": "mid", "line": 5, "value": 20},
    ]
    assert d["test_gap"] == [
        {"file": "b.ts", "function": "gap", "line": 7, "value": 342.0, "coverage": 0.1},
        {"file": "a.ts", "function": "low", "line": 2, "value": 40.0, "coverage": 0.5},
    ]
    assert d["duplication"] == clones


def test_parse_jscpd_clones():
    clones = parse_jscpd_clones(FIX / "jscpd-sample.json")
    assert clones == [
        {
            "a": {"file": "src/a.ts", "start": 1, "end": 40},
            "b": {"file": "src/b.ts", "start": 1, "end": 40},
        }
    ]


def test_parse_jscpd_clones_relativises_absolute_paths(tmp_path):
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps(
            {
                "duplicates": [
                    {
                        "firstFile": {
                            "name": f"{tmp_path}/src/a.ts",
                            "start": 2,
                            "end": 9,
                        },
                        "secondFile": {"name": "./src/b.ts", "start": 4, "end": 11},
                    }
                ]
            }
        )
    )
    (clone,) = parse_jscpd_clones(report, tmp_path)
    assert clone["a"]["file"] == "src/a.ts" and clone["b"]["file"] == "src/b.ts"


def test_parse_jscpd_clones_unusable_is_empty(tmp_path):
    assert parse_jscpd_clones(tmp_path / "nope.json") == []
    (tmp_path / "bad.json").write_text("{")
    assert parse_jscpd_clones(tmp_path / "bad.json") == []
    (tmp_path / "empty.json").write_text("{}")
    assert parse_jscpd_clones(tmp_path / "empty.json") == []


def test_run_writes_details_next_to_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")  # no lizard, no jscpd: every metric is null
    out = tmp_path / "out" / "r.json"
    run(FIX / "ts-mini", out, env=CTX_ENV)
    text = (out.parent / "details.json").read_text()
    assert text.endswith("}\n")
    assert json.loads(text) == {
        "schema": 1,
        "complexity": [],
        "cognitive": [],
        "test_gap": [],
        "duplication": [],
    }


def test_run_lists_clones_from_jscpd_report_beside_out(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    out = tmp_path / "out" / "r.json"
    (out.parent / "jscpd").mkdir(parents=True)
    shutil.copy(FIX / "jscpd-sample.json", out.parent / "jscpd" / "jscpd-report.json")
    run(FIX / "ts-mini", out, env=CTX_ENV)
    clones = json.loads((out.parent / "details.json").read_text())["duplication"]
    assert clones[0]["a"]["file"] == "src/a.ts"
