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


def _stub_jscpd(monkeypatch, result):
    monkeypatch.setattr("quality_report.report.run_jscpd", lambda *a, **k: result)


def test_run_lists_clones_from_the_report_jscpd_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    _stub_jscpd(monkeypatch, FIX / "jscpd-sample.json")
    out = tmp_path / "out" / "r.json"
    run(FIX / "ts-mini", out, env=CTX_ENV)
    clones = json.loads((out.parent / "details.json").read_text())["duplication"]
    assert clones[0]["a"]["file"] == "src/a.ts"


def test_run_ignores_stale_jscpd_report_when_jscpd_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    out = tmp_path / "out" / "r.json"
    (out.parent / "jscpd").mkdir(parents=True)
    shutil.copy(FIX / "jscpd-sample.json", out.parent / "jscpd" / "jscpd-report.json")
    _stub_jscpd(monkeypatch, None)
    report = run(FIX / "ts-mini", out, env=CTX_ENV)
    assert report["duplication"] is None
    assert json.loads((out.parent / "details.json").read_text())["duplication"] == []


def test_build_details_cognitive_empty_when_report_cognitive_null():
    functions = [_fn("a.ts", "worst", 3, 30, 40)]
    d = build_details(functions, 10, None, [], [])
    assert d["cognitive"] == []
    assert [e["function"] for e in d["complexity"]] == ["worst"]


def test_run_details_cognitive_empty_when_report_cognitive_null(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    rows = (
        '30,60,100,1,30,"big@1-30@a.ts","a.ts","big","big()",1,30,40\n'
        '1,2,9,0,1,"small@40-41@a.ts","a.ts","small","small()",40,41\n'
    )  # second row has no CogC, so the report's cognitive block is null
    monkeypatch.setattr("quality_report.report.run_lizard", lambda *a, **k: rows)
    out = tmp_path / "out" / "r.json"
    report = run(FIX / "ts-mini", out, env=CTX_ENV)
    assert report["cognitive"] is None
    details = json.loads((out.parent / "details.json").read_text())
    assert details["cognitive"] == []
    assert [e["function"] for e in details["complexity"]] == ["big()"]


def test_build_details_duplication_largest_clone_first():
    def clone(af, a0, a1, bf="z", b0=1, b1=2):
        return {
            "a": {"file": af, "start": a0, "end": a1},
            "b": {"file": bf, "start": b0, "end": b1},
        }

    small = clone("a", 1, 5)
    big_b = clone("c", 1, 2, "d", 1, 50)  # size comes from the larger side
    tie_late = clone("b", 20, 30)
    tie_early = clone("b", 3, 13)
    tie_file = clone("a", 40, 50)
    d = build_details([], 10, 15, [], [small, tie_late, big_b, tie_early, tie_file])
    assert d["duplication"] == [big_b, tie_file, tie_early, tie_late, small]
