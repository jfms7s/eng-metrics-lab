from pathlib import Path

import pytest

from quality_report.config import DEFAULT_EXCLUDES
from quality_report.deadcode import (
    count_dead_code_allows,
    count_machete_crates,
    merge_dead_code,
    parse_knip,
    run_cargo_machete,
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
    r = run_tool(["cargo", "machete", "--version"])
    return r is not None and r.returncode == 0


def test_parse_knip_maps_kinds():
    k = parse_knip(Path("tests/fixtures/knip-sample.json"))
    assert k["unused_files"] == 1 and k["unused_dependencies"] == 1
    assert k["unused_exports"] == 2          # exports + types
    assert k["unused_symbols"] == 1          # enum/class members


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
    (tmp_path / "src" / "a.rs").write_text("#[allow(dead_code)]\nfn a(){}\n#[expect(dead_code)]\nfn b(){}\n#[allow(unused, dead_code)]\nfn c(){}\n")
    (tmp_path / "target").mkdir(); (tmp_path / "target" / "x.rs").write_text("#[allow(dead_code)]\n")
    assert count_dead_code_allows(tmp_path, list(DEFAULT_EXCLUDES)) == 3


def test_count_dead_code_allows_ignores_other_lints(tmp_path):
    (tmp_path / "a.rs").write_text("#[allow(unused_variables)]\n#![allow(clippy::all)]\n")
    assert count_dead_code_allows(tmp_path, list(DEFAULT_EXCLUDES)) == 0


def test_count_machete_crates():
    assert count_machete_crates(MACHETE_STDOUT) == 3
    assert count_machete_crates("cargo-machete didn't find any unused dependencies\nDone!\n") == 0


def test_scan_rust_without_cargo_toml_is_none(tmp_path):
    (tmp_path / "a.rs").write_text("#[allow(dead_code)]\n")
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) is None


def test_scan_rust_ignores_excluded_cargo_toml(tmp_path):
    (tmp_path / "target").mkdir(); (tmp_path / "target" / "Cargo.toml").write_text("")
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) is None


def test_scan_rust_reports_suppressed_when_machete_missing(tmp_path, monkeypatch):
    (tmp_path / "Cargo.toml").write_text("")
    (tmp_path / "a.rs").write_text("#[allow(dead_code)]\n")
    monkeypatch.setattr("quality_report.deadcode.run_cargo_machete", lambda repo: None)
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) == {"suppressed": 1}


def test_merge_sums_and_ignores_none():
    m = merge_dead_code([{"unused_files": 1}, None, {"unused_dependencies": 2, "suppressed": 3}])
    assert m["total"] == 6 and m["by_kind"]["unused_exports"] == 0


def test_merge_all_none_is_none():
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
    assert scan_rust(tmp_path, list(DEFAULT_EXCLUDES)) == {"suppressed": 0, "unused_dependencies": 1}


def _knip(tmp_path, payload):
    import json
    f = tmp_path / "k.json"
    f.write_text(json.dumps(payload))
    return parse_knip(f)


def test_parse_knip_member_fields_accept_dict_and_list(tmp_path):
    rec = {"enumMembers": {"E": [{"name": "a"}, {"name": "b"}]}, "classMembers": [{"name": "m"}]}
    assert _knip(tmp_path, {"files": [], "issues": [rec]})["unused_symbols"] == 3
    rec = {"enumMembers": [{"name": "a"}], "classMembers": {"C": [{"name": "m"}]}}
    assert _knip(tmp_path, {"issues": [rec]})["unused_symbols"] == 2


@pytest.mark.parametrize("payload", [
    {"issues": [{"enumMembers": 5}]},
    {"files": 3},
    {"issues": [{"exports": "x"}]},
    {"issues": ["not-a-record"]},
    {"issues": 7},
])
def test_parse_knip_malformed_is_none_with_warning(tmp_path, capsys, payload):
    assert _knip(tmp_path, payload) is None
    assert "warning" in capsys.readouterr().err


def test_parse_knip_null_fields_count_zero(tmp_path):
    k = _knip(tmp_path, {"files": None, "issues": [{"enumMembers": None, "exports": None}]})
    assert k == {"unused_files": 0, "unused_exports": 0, "unused_dependencies": 0, "unused_symbols": 0}


def _stub_machete(monkeypatch, rc, stdout="", stderr=""):
    import subprocess
    result = subprocess.CompletedProcess(["cargo"], rc, stdout, stderr)
    monkeypatch.setattr("quality_report.deadcode.run_tool", lambda *a, **k: result)


def test_machete_rc1_with_crates_is_counted(monkeypatch):
    _stub_machete(monkeypatch, 1, MACHETE_STDOUT)
    assert run_cargo_machete(Path(".")) == 3


def test_machete_rc2_is_none_with_warning(monkeypatch, capsys):
    _stub_machete(monkeypatch, 2, "", "Analyzing dependencies of crates in x...\nError: Errors when walking over directories")
    assert run_cargo_machete(Path(".")) is None
    assert "warning" in capsys.readouterr().err


def test_machete_manifest_error_is_none_with_warning(monkeypatch, capsys):
    out = "Analyzing dependencies of crates in x...\ncargo-machete didn't find any unused dependencies in x. Good job!\n"
    _stub_machete(monkeypatch, 0, out, "error when handling x/Cargo.toml: TOML parse error")
    assert run_cargo_machete(Path(".")) is None
    assert "warning" in capsys.readouterr().err


def test_machete_not_installed_is_none_with_warning(monkeypatch, capsys):
    _stub_machete(monkeypatch, 101, "", "error: no such subcommand: `machete`")
    assert run_cargo_machete(Path(".")) is None
    assert "warning" in capsys.readouterr().err
