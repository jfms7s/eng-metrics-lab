import json
from pathlib import Path

import pytest

from quality_report.config import DEFAULT_EXCLUDES
from quality_report.deadcode import (
    count_dead_code_allows,
    count_machete_crates,
    find_python_projects,
    has_cargo_manifest,
    merge_dead_code,
    parse_go_deadcode,
    parse_knip,
    python_sources,
    run_cargo_machete,
    run_deptry,
    run_vulture,
    scan_python,
    scan_rust,
)
from quality_report.tools import run_tool

MACHETE_STDOUT = """Analyzing dependencies of crates in ....
cargo-machete found the following unused dependencies in this directory:
mc -- ./Cargo.toml:
\titoa
\tserde
other -- ./sub/Cargo.toml:
\tlog

If you believe cargo-machete has detected an unused dependency incorrectly,
you can add the dependency to the list of dependencies to ignore in the
`[package.metadata.cargo-machete]` section of the appropriate Cargo.toml.
For example:

[package.metadata.cargo-machete]
ignored = ["prost"]

Done!
"""


def _have_machete() -> bool:
    r = run_tool(["cargo-machete", "--version"])
    return r is not None and r.returncode == 0


def test_parse_knip_maps_kinds():
    k = parse_knip(Path("tests/fixtures/knip-sample.json"))
    assert k["unused_files"] == 1 and k["unused_dependencies"] == 1
    assert k["unused_exports"] == 2  # exports + types
    assert k["unused_symbols"] == 1  # enum/class members


def test_parse_knip_empty_file_is_none(tmp_path, capsys):
    (tmp_path / "f.json").write_text("")
    assert parse_knip(tmp_path / "f.json") is None
    assert "warning" in capsys.readouterr().err


def test_parse_knip_invalid_json_is_none(tmp_path, capsys):
    (tmp_path / "f.json").write_text("{nope")
    assert parse_knip(tmp_path / "f.json") is None
    assert "warning" in capsys.readouterr().err


def test_parse_knip_missing_file_is_none_without_warning(tmp_path, capsys):
    assert parse_knip(tmp_path / "absent.json") is None
    assert capsys.readouterr().err == ""


def test_count_dead_code_allows(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.rs").write_text(
        "#[allow(dead_code)]\nfn a(){}\n#[expect(dead_code)]\nfn b(){}\n#[allow(unused, dead_code)]\nfn c(){}\n"
    )
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "x.rs").write_text("#[allow(dead_code)]\n")
    assert count_dead_code_allows(tmp_path, list(DEFAULT_EXCLUDES)) == 3


def test_count_dead_code_allows_ignores_other_lints(tmp_path):
    (tmp_path / "a.rs").write_text(
        "#[allow(unused_variables)]\n#![allow(clippy::all)]\n"
    )
    assert count_dead_code_allows(tmp_path, list(DEFAULT_EXCLUDES)) == 0


def test_count_machete_crates():
    assert count_machete_crates(MACHETE_STDOUT) == 3
    assert (
        count_machete_crates(
            "cargo-machete didn't find any unused dependencies\nDone!\n"
        )
        == 0
    )


def test_has_cargo_manifest(tmp_path):
    assert not has_cargo_manifest(tmp_path, list(DEFAULT_EXCLUDES))
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "Cargo.toml").write_text("")
    assert not has_cargo_manifest(tmp_path, list(DEFAULT_EXCLUDES))
    (tmp_path / "Cargo.toml").write_text("")
    assert has_cargo_manifest(tmp_path, list(DEFAULT_EXCLUDES))


def test_scan_rust_is_none_when_machete_broken(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "a.rs").write_text("#[allow(dead_code)]\n")
    monkeypatch.setattr("quality_report.deadcode.run_cargo_machete", lambda repo: None)
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) is None


def test_scan_rust_counts_suppressed_and_unused(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "a.rs").write_text("#[allow(dead_code)]\n")
    monkeypatch.setattr("quality_report.deadcode.run_cargo_machete", lambda repo: 2)
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) == {
        "suppressed": 1,
        "unused_dependencies": 2,
    }


def test_merge_sums_parts():
    m = merge_dead_code(
        [{"unused_files": 1}, {"unused_dependencies": 2, "suppressed": 3}]
    )
    assert m["total"] == 6 and m["by_kind"]["unused_exports"] == 0


def test_merge_no_expected_parts_is_none():
    assert merge_dead_code([]) is None


def test_merge_any_broken_part_makes_total_null():
    assert merge_dead_code([{"unused_files": 1}, None]) is None
    assert merge_dead_code([None, None]) is None


@pytest.mark.tools
@pytest.mark.skipif(not _have_machete(), reason="cargo-machete not installed")
def test_run_cargo_machete_counts_unused_crate(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main(){}\n")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "mc"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\nitoa = "1"\n'
    )
    assert run_cargo_machete(tmp_path) == 1
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) == {
        "suppressed": 0,
        "unused_dependencies": 1,
    }


def _knip(tmp_path, payload):
    import json

    f = tmp_path / "k.json"
    f.write_text(json.dumps(payload))
    return parse_knip(f)


def test_parse_knip_member_fields_accept_dict_and_list(tmp_path):
    rec = {
        "enumMembers": {"E": [{"name": "a"}, {"name": "b"}]},
        "classMembers": [{"name": "m"}],
    }
    assert _knip(tmp_path, {"files": [], "issues": [rec]})["unused_symbols"] == 3
    rec = {"enumMembers": [{"name": "a"}], "classMembers": {"C": [{"name": "m"}]}}
    assert _knip(tmp_path, {"files": [], "issues": [rec]})["unused_symbols"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"files": [], "issues": [{"enumMembers": 5}]},
        {"files": 3, "issues": []},
        {"files": [], "issues": [{"exports": "x"}]},
        {"files": [], "issues": ["not-a-record"]},
        {"files": [], "issues": 7},
    ],
)
def test_parse_knip_malformed_is_none_with_warning(tmp_path, capsys, payload):
    assert _knip(tmp_path, payload) is None
    assert "warning" in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload", [{"vulture": ["x"]}, {"files": []}, {"issues": []}, {}, []]
)
def test_parse_knip_requires_files_and_issues_keys(tmp_path, capsys, payload):
    assert _knip(tmp_path, payload) is None
    assert "not a knip report" in capsys.readouterr().err


def test_parse_knip_null_fields_count_zero(tmp_path):
    k = _knip(
        tmp_path, {"files": None, "issues": [{"enumMembers": None, "exports": None}]}
    )
    assert k == {
        "unused_files": 0,
        "unused_exports": 0,
        "unused_dependencies": 0,
        "unused_symbols": 0,
    }


def _stub_machete(monkeypatch, rc, stdout="", stderr=""):
    import subprocess

    result = subprocess.CompletedProcess(["cargo-machete"], rc, stdout, stderr)
    monkeypatch.setattr("quality_report.deadcode.run_tool", lambda *a, **k: result)


def test_machete_rc1_with_crates_is_counted(monkeypatch):
    _stub_machete(monkeypatch, 1, MACHETE_STDOUT)
    assert run_cargo_machete(Path(".")) == 3


def test_machete_rc2_is_none_with_warning(monkeypatch, capsys):
    _stub_machete(
        monkeypatch,
        2,
        "",
        "Analyzing dependencies of crates in x...\nError: Errors when walking over directories",
    )
    assert run_cargo_machete(Path(".")) is None
    assert "warning" in capsys.readouterr().err


def test_machete_manifest_error_is_none_with_warning(monkeypatch, capsys):
    out = "Analyzing dependencies of crates in x...\ncargo-machete didn't find any unused dependencies in x. Good job!\n"
    _stub_machete(
        monkeypatch, 0, out, "error when handling x/Cargo.toml: TOML parse error"
    )
    assert run_cargo_machete(Path(".")) is None
    assert "warning" in capsys.readouterr().err


def test_machete_not_installed_is_none_with_warning(monkeypatch, capsys):
    _stub_machete(monkeypatch, 101, "", "cargo-machete: command not found")
    assert run_cargo_machete(Path(".")) is None
    assert "warning" in capsys.readouterr().err


FIX = Path(__file__).parent / "fixtures"


@pytest.mark.tools
def test_scan_python_counts_vulture_and_deptry():
    r = scan_python(FIX / "py-mini", list(DEFAULT_EXCLUDES))
    assert r == {"unused_symbols": 2, "unused_dependencies": 1}


@pytest.mark.tools
def test_scan_python_finds_nested_project():
    nested = FIX / "py-nested"
    assert find_python_projects(nested, list(DEFAULT_EXCLUDES)) == [nested / "backend"]
    assert scan_python(nested, list(DEFAULT_EXCLUDES)) is not None


@pytest.mark.tools
def test_run_vulture_honours_exclude():
    assert run_vulture(FIX / "py-mini", ["src"]) == 0


@pytest.mark.tools
def test_run_vulture_scans_builder_py_with_default_excludes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "builder.py").write_text("import os\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("import os\n")
    assert run_vulture(tmp_path, list(DEFAULT_EXCLUDES)) == 1


@pytest.mark.tools
def test_run_vulture_null_when_a_file_does_not_parse(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")
    (tmp_path / "b.py").write_text("def f(:\n    pass\n")
    assert run_vulture(tmp_path, []) is None


@pytest.mark.tools
def test_run_vulture_counts_despite_syntax_warning(tmp_path):
    (tmp_path / "a.py").write_text('import os\n\n\ndef f():\n    return "\\d"\n')
    assert run_vulture(tmp_path, []) == 1


def test_find_python_projects_skips_excluded_and_deep_dirs(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "a" / "b" / "c" / "d").mkdir(parents=True)
    (tmp_path / "a" / "requirements-dev.txt").write_text("")
    (tmp_path / "a" / "b" / "c" / "d" / "setup.py").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pyproject.toml").write_text("")
    assert find_python_projects(tmp_path, ["node_modules"]) == [
        tmp_path,
        tmp_path / "a",
    ]


def test_scan_python_none_when_vulture_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("quality_report.deadcode.run_vulture", lambda *a, **k: None)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert scan_python(tmp_path, list(DEFAULT_EXCLUDES)) is None


def test_scan_python_none_when_deptry_broken(monkeypatch, tmp_path):
    monkeypatch.setattr("quality_report.deadcode.run_vulture", lambda *a, **k: 0)
    monkeypatch.setattr("quality_report.deadcode.run_deptry", lambda *a, **k: None)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert scan_python(tmp_path, list(DEFAULT_EXCLUDES)) is None


def test_scan_python_sums_deptry_over_projects(monkeypatch, tmp_path):
    monkeypatch.setattr("quality_report.deadcode.run_vulture", lambda *a, **k: 3)
    monkeypatch.setattr("quality_report.deadcode.run_deptry", lambda *a, **k: 2)
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "pyproject.toml").write_text("")
    assert scan_python(tmp_path, []) == {"unused_symbols": 3, "unused_dependencies": 4}


def test_run_deptry_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "")
    assert run_deptry(tmp_path) is None


def test_run_vulture_none_when_missing(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.setenv("PATH", "")
    assert run_vulture(tmp_path, []) is None


VULTURE_FINDINGS = (
    "a.py:1: unused import 'os' (90% confidence)\n"
    "a.py:5: unused variable 'y' (100% confidence)\n"
)


def _stub_tool(monkeypatch, rc, stdout="", stderr="", *, report=None):
    """Replace run_tool with a fake run; `report` is written where deptry would write it."""
    import subprocess

    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        if report is not None:
            Path(args[args.index("--json-output") + 1]).write_text(report)
        return subprocess.CompletedProcess(args, rc, stdout, stderr)

    monkeypatch.setattr("quality_report.deadcode.run_tool", fake)
    return calls


def _py_repo(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")
    return tmp_path


def test_vulture_findings_with_unparseable_file_is_none(monkeypatch, tmp_path, capsys):
    stderr = 'b.py:3: invalid syntax at "def f[T](x: T) -> T:"\n'
    _stub_tool(monkeypatch, 3, VULTURE_FINDINGS, stderr)
    assert run_vulture(_py_repo(tmp_path), []) is None
    err = capsys.readouterr().err
    assert "could not parse or read 1 file" in err and "def f" not in err


def test_vulture_findings_with_unreadable_file_is_none(monkeypatch, tmp_path, capsys):
    stderr = (
        "Error: Could not read file /r/c.py - \nTry to change the encoding to UTF-8.\n"
        'd.py: invalid source code "source code string cannot contain null bytes"\n'
    )
    _stub_tool(monkeypatch, 3, VULTURE_FINDINGS, stderr)
    assert run_vulture(_py_repo(tmp_path), []) is None
    assert "could not parse or read 2 file" in capsys.readouterr().err


def test_vulture_syntax_warning_does_not_null_the_count(monkeypatch, tmp_path):
    stderr = (
        '/r/a.py:4: SyntaxWarning: "\\d" is an invalid escape sequence.\n'
        '  return "\\d"\n'
    )
    _stub_tool(monkeypatch, 3, VULTURE_FINDINGS, stderr)
    assert run_vulture(_py_repo(tmp_path), []) == 2


def test_vulture_exit0_is_zero(monkeypatch, tmp_path):
    _stub_tool(monkeypatch, 0)
    assert run_vulture(_py_repo(tmp_path), []) == 0


@pytest.mark.parametrize("rc", [1, 2])
def test_vulture_exit_1_or_2_is_none(monkeypatch, tmp_path, capsys, rc):
    _stub_tool(monkeypatch, rc, "", 'b.py:3: invalid syntax at "x"\n')
    assert run_vulture(_py_repo(tmp_path), []) is None
    assert "vulture failed" in capsys.readouterr().err


def test_vulture_scans_builder_py_next_to_excluded_build_dir(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "builder.py").write_text("import os\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("import os\n")
    calls = _stub_tool(monkeypatch, 3, "src/builder.py:1: unused import 'os'\n")
    assert run_vulture(tmp_path, list(DEFAULT_EXCLUDES)) == 1
    assert calls[0][-2:] == ["--", "src/builder.py"]
    assert "--exclude" not in calls[0]


def test_python_sources_match_excludes_by_component(tmp_path):
    for rel in (
        "builder.py",
        "distance.py",
        "migrations/m.py",
        "build/x.py",
        "a/b.txt",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    assert python_sources(tmp_path, list(DEFAULT_EXCLUDES)) == [
        Path("builder.py"),
        Path("distance.py"),
    ]


def test_vulture_without_python_files_is_zero_without_running(monkeypatch, tmp_path):
    calls = _stub_tool(monkeypatch, 2)
    assert run_vulture(tmp_path, []) == 0
    assert calls == []


def test_scan_python_without_py_files_counts_deptry_only(monkeypatch, tmp_path):
    # A manifest but no *.py outside excludes: 0 symbols is real because deptry ran.
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("import os\n")
    monkeypatch.setattr("quality_report.deadcode.run_deptry", lambda project: 1)
    assert scan_python(tmp_path, list(DEFAULT_EXCLUDES)) == {
        "unused_symbols": 0,
        "unused_dependencies": 1,
    }


def test_scan_python_with_nothing_to_scan_is_none(tmp_path, capsys):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("")
    assert scan_python(tmp_path, list(DEFAULT_EXCLUDES)) is None
    assert "no *.py file or python project" in capsys.readouterr().err


@pytest.mark.parametrize("rc", [2, 3])
def test_deptry_exit_above_1_is_none(monkeypatch, tmp_path, capsys, rc):
    _stub_tool(monkeypatch, rc, "", "Scanning 3 files...\n", report="[]")
    assert run_deptry(tmp_path) is None
    assert "deptry failed" in capsys.readouterr().err


@pytest.mark.parametrize("report", [None, "", "{not json", '{"a": 1}', "[1]"])
def test_deptry_exit1_with_missing_or_garbage_report_is_none(
    monkeypatch, tmp_path, capsys, report
):
    _stub_tool(monkeypatch, 1, report=report)
    assert run_deptry(tmp_path) is None
    assert "deptry report" in capsys.readouterr().err


def test_deptry_exit1_counts_only_dep002(monkeypatch, tmp_path):
    issues = [
        {"error": {"code": "DEP002", "message": "'a' defined but unused"}},
        {"error": {"code": "DEP001", "message": "'b' imported but missing"}},
        {"error": {"code": "DEP002", "message": "'c' defined but unused"}},
        {"error": {"code": "DEP003", "message": "'d' transitive"}},
    ]
    _stub_tool(monkeypatch, 1, report=json.dumps(issues))
    assert run_deptry(tmp_path) == 2


def test_parse_go_deadcode_counts_non_generated():
    assert parse_go_deadcode(FIX / "go-deadcode-sample.json") == {"unused_symbols": 1}


def test_parse_go_deadcode_clean_run_is_zero(tmp_path):
    path = tmp_path / "deadcode-go.json"
    path.write_text("null")
    assert parse_go_deadcode(path) == {"unused_symbols": 0}


def test_parse_go_deadcode_missing_is_none(tmp_path):
    assert parse_go_deadcode(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    "content", ["", "  \n", "not json", "{}", '[{"Funcs": [{}]}]', "[1]"]
)
def test_parse_go_deadcode_unusable_is_none(tmp_path, content):
    path = tmp_path / "deadcode-go.json"
    path.write_text(content)
    assert parse_go_deadcode(path) is None
