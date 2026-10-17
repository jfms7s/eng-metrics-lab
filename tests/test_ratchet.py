import json
from datetime import UTC, datetime

import pytest

from quality_report.cli import main
from quality_report.ratchet import can_tighten, check, make_baseline

B = {"schema": 1, "commit": "base", "duplication_percent": 3.4, "dead_code_total": 42,
     "complexity_threshold": 10, "functions_over_threshold": 57}


def R(dup=3.4, dead=42, over10=57, over7=100, thr=10, branch="main"):
    return {"branch": branch, "commit": "abc1234",
            "duplication": None if dup is None else {"percent": dup},
            "dead_code": None if dead is None else {"total": dead},
            "complexity": {"threshold": thr, "functions_over_threshold": over10,
                           "over": {"7": over7, "10": over10, "15": 1}}}


@pytest.mark.parametrize("report,result", [
    (R(), "pass"), (R(dup=3.5), "pass"), (R(dup=3.51), "fail"),
    (R(dead=43), "fail"), (R(over10=58), "fail"), (R(dead=None), "pass"),
    (R(dup=None), "pass"),
])
def test_ratchet_table(report, result):
    assert check(report, B)["result"] == result


def test_ratchet_without_baseline_is_skipped():
    assert check(R(), None) == {"result": "skipped", "baseline_commit": None, "violations": []}


def test_ratchet_compares_at_baseline_threshold():
    assert check(R(thr=7, over10=57, over7=500), B)["result"] == "pass"


def test_violation_lists_field_values():
    v = check(R(dead=45), B)["violations"]
    assert v == [{"field": "dead_code.total", "baseline": 42, "current": 45, "tolerance": 0}]
    assert check(R(), B)["baseline_commit"] == "base"


def test_null_complexity_and_null_baseline_metric_skip_rows():
    r = R(over10=999)
    r["complexity"] = None
    assert check(r, B)["result"] == "pass"
    assert check(R(dead=999), {**B, "dead_code_total": None})["result"] == "pass"


def test_unknown_baseline_threshold_skips_complexity_with_note(capsys):
    res = check(R(over10=999), {**B, "complexity_threshold": 12})
    assert res["result"] == "pass"
    assert "12" in capsys.readouterr().err


def test_can_tighten_only_when_strictly_better():
    assert can_tighten(R(dead=41), B) and not can_tighten(R(), B)
    assert can_tighten(R(dup=3.2), B)
    assert not can_tighten(R(dup=3.35), B)  # within tolerance
    assert can_tighten(R(over10=50), B)
    assert not can_tighten(R(dead=None), B)  # only rows present in both


def test_make_baseline_shape_and_nulls():
    now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    assert make_baseline(R(), now) == {
        "schema": 1, "recorded_at": "2026-11-02T10:00:00Z", "commit": "abc1234",
        "duplication_percent": 3.4, "dead_code_total": 42,
        "complexity_threshold": 10, "functions_over_threshold": 57}
    b = make_baseline(R(dup=None, dead=None), now)
    assert b["duplication_percent"] is None and b["dead_code_total"] is None


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return str(p)


def test_cli_ratchet_exit_codes(tmp_path, capsys):
    base = _write(tmp_path, "b.json", B)
    ok = _write(tmp_path, "ok.json", R())
    bad = _write(tmp_path, "bad.json", R(dead=45))
    assert main(["ratchet", "--baseline", base, "--report", ok]) == 0
    assert main(["ratchet", "--baseline", base, "--report", bad]) == 1
    out = capsys.readouterr().out
    assert "dead_code.total" in out and "fail" in out
    assert main(["ratchet", "--baseline", str(tmp_path / "none.json"), "--report", ok]) == 0
    assert "skipped" in capsys.readouterr().out
    assert main(["ratchet", "--baseline", base, "--report", str(tmp_path / "none.json")]) == 2
    old = _write(tmp_path, "old.json", {**B, "schema": 2})
    assert main(["ratchet", "--baseline", old, "--report", ok]) == 2


def test_cli_tighten_hint_only_on_main(tmp_path, capsys):
    base = _write(tmp_path, "b.json", B)
    better = _write(tmp_path, "m.json", R(dead=10))
    other = _write(tmp_path, "o.json", R(dead=10, branch="feat/x"))
    main(["ratchet", "--baseline", base, "--report", better])
    assert f"quality-report baseline --from {better} > .quality-baseline.json" in capsys.readouterr().out
    main(["ratchet", "--baseline", base, "--report", other])
    assert "tightened" not in capsys.readouterr().out


def test_cli_baseline_prints_json(tmp_path, capsys):
    rep = _write(tmp_path, "r.json", R())
    assert main(["baseline", "--from", rep]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == 1 and out["commit"] == "abc1234"
    assert out["recorded_at"].endswith("Z")
