import shutil
from pathlib import Path

import pytest

from quality_report.config import DEFAULT_EXCLUDES, load_config
from quality_report.coverage import (
    FRAGMENTS,
    CoverageData,
    FileCoverage,
    go_module_roots,
    load_coverage,
    normalize_path,
    parse_go_coverprofile,
    parse_lcov,
    summarize_coverage,
)
from quality_report.detect import LANGUAGES

FIX = Path(__file__).parent / "fixtures" / "coverage"
EXCLUDE = list(DEFAULT_EXCLUDES)
NO_ENV: dict[str, str] = {}


@pytest.fixture
def repo(tmp_path) -> Path:
    """A writable copy of the mini repo, with an empty `.quality/` for fragments."""
    dest = tmp_path / "repo"
    shutil.copytree(FIX / "repo", dest)
    (dest / ".quality").mkdir()
    return dest


def _fragment(repo: Path, name: str, text: str) -> None:
    (repo / ".quality" / name).write_text(text)


def _lcov(path: str, lines: dict[int, int], branches: list[str] = ()) -> str:
    body = [f"SF:{path}"]
    body += [f"DA:{n},{h}" for n, h in lines.items()]
    body += [f"BRDA:{b}" for b in branches]
    return "\n".join([*body, "end_of_record", ""])


# --- lcov ---


def test_parse_lcov_lines_and_branches():
    text = "SF:src/a.ts\nDA:3,0\nDA:4,2\nBRDA:4,0,0,1\nBRDA:4,0,1,-\nend_of_record\n"
    fc = parse_lcov(text)["src/a.ts"]
    assert fc.lines == {3: 0, 4: 2}
    assert len(fc.branches) == 2
    assert sorted(fc.branches.values()) == [0, 1]


def test_parse_lcov_merges_repeated_file_by_max():
    text = _lcov("src/a.ts", {3: 0, 4: 1}) + _lcov("src/a.ts", {3: 5, 4: 0})
    assert parse_lcov(text)["src/a.ts"].lines == {3: 5, 4: 1}


def test_parse_lcov_merges_repeated_branch_by_max():
    text = _lcov("a.ts", {1: 1}, ["1,0,0,-"]) + _lcov("a.ts", {1: 1}, ["1,0,0,3"])
    assert list(parse_lcov(text)["a.ts"].branches.values()) == [3]


def test_parse_lcov_ignores_summary_and_function_records():
    plain = "SF:a.ts\nDA:1,1\nDA:2,0\nend_of_record\n"
    noisy = (
        "TN:t\nSF:a.ts\nFN:1,f\nFNDA:4,f\nFNF:1\nFNH:1\nDA:1,1\nDA:2,0\n"
        "LF:99\nLH:99\nBRF:7\nBRH:7\nend_of_record\n"
    )
    assert parse_lcov(noisy) == parse_lcov(plain)


def test_parse_lcov_accepts_da_checksum():
    assert parse_lcov("SF:a.py\nDA:1,3,abc123\nend_of_record\n")["a.py"].lines == {1: 3}


@pytest.mark.parametrize(
    "text",
    ["DA:1,1\n", "SF:a.ts\nDA:x,1\nend_of_record\n", "SF:a.ts\nBRDA:1,0\n"],
)
def test_parse_lcov_rejects_malformed_records(text):
    with pytest.raises(ValueError):
        parse_lcov(text)


def test_parse_lcov_brda_branch_expression_with_commas():
    # lcov 2.x may name a branch with an expression that itself contains commas.
    text = "SF:a.ts\nDA:4,1\nBRDA:4,0,(a, b) => true,1\nBRDA:4,0,(a, b) => false,-\n"
    fc = parse_lcov(text + "end_of_record\n")["a.ts"]
    assert sorted(fc.branches.values()) == [0, 1]


def test_parse_lcov_sample_fixture():
    fc = parse_lcov((FIX / "ts.lcov").read_text())["src/a.ts"]
    assert fc.lines == {1: 2, 2: 2, 3: 1, 5: 0}
    assert sorted(fc.branches.values()) == [0, 1]


# --- Go coverprofile ---


def test_parse_go_coverprofile():
    text = (
        "mode: atomic\n"
        "example.com/m/server/a.go:3.10,5.2 2 0\n"
        "example.com/m/server/a.go:4.1,4.9 1 3\n"
    )
    fc = parse_go_coverprofile(text)["example.com/m/server/a.go"]
    assert fc.lines == {3: 0, 4: 3, 5: 0}
    assert fc.branches == {}


def test_parse_go_coverprofile_skips_statementless_blocks():
    fc = parse_go_coverprofile("mode: set\nm/a.go:1.1,3.2 0 0\nm/a.go:2.1,2.5 1 1\n")
    assert fc["m/a.go"].lines == {2: 1}


def test_parse_go_coverprofile_merges_repeated_blocks_by_max():
    # -coverpkg=./... repeats every block once per test binary.
    text = "mode: set\nm/a.go:1.1,1.5 1 0\nm/a.go:1.1,1.5 1 1\nm/a.go:1.1,1.5 1 0\n"
    assert parse_go_coverprofile(text)["m/a.go"].lines == {1: 1}


@pytest.mark.parametrize("line", ["m/a.go 1 1", "m/a.go:1.1,x.2 1 1", "m/a.go:1.1"])
def test_parse_go_coverprofile_rejects_malformed_blocks(line):
    with pytest.raises(ValueError):
        parse_go_coverprofile(f"mode: set\n{line}\n")


# --- path normalisation ---


def test_go_module_roots(repo):
    assert go_module_roots(repo, EXCLUDE) == {"example.com/m": "server"}


def test_go_module_roots_root_module_and_excludes(repo):
    (repo / "go.mod").write_text('module "example.com/root"\n')
    (repo / "vendor" / "dep").mkdir(parents=True)
    (repo / "vendor" / "dep" / "go.mod").write_text("module example.com/dep\n")
    assert go_module_roots(repo, EXCLUDE) == {
        "example.com/root": "",
        "example.com/m": "server",
    }


def test_go_module_roots_duplicate_module_is_dropped(repo, capsys):
    (repo / "tools").mkdir()
    (repo / "tools" / "go.mod").write_text("module example.com/m\n")
    assert go_module_roots(repo, EXCLUDE) == {}
    assert "example.com/m" in capsys.readouterr().err


def test_go_module_roots_skips_dirs_go_ignores(repo, capsys):
    for name in ("testdata", "_old", ".hidden"):
        (repo / "server" / name).mkdir()
        (repo / "server" / name / "go.mod").write_text("module example.com/m\n")
    assert go_module_roots(repo, EXCLUDE) == {"example.com/m": "server"}
    assert capsys.readouterr().err == ""


def test_normalize_relative_and_dot_slash(repo):
    assert normalize_path("./src/a.ts", repo, [], {}) == "src/a.ts"
    assert normalize_path("src/a.ts", repo, [], {}) == "src/a.ts"


def test_normalize_repo_absolute(repo):
    assert normalize_path(f"{repo}/src/a.ts", repo, [], {}) == "src/a.ts"


def test_normalize_host_workspace_prefix(repo):
    raw = "/home/runner/work/r/r/src/a.ts"
    assert normalize_path(raw, repo, ["/home/runner/work/r/r"], {}) == "src/a.ts"
    assert normalize_path(raw, repo, ["/home/runner/work/r/r/"], {}) == "src/a.ts"


def test_normalize_prefix_matches_whole_components_only(repo):
    assert normalize_path("/ws-other/src/a.ts", repo, ["/ws"], {}) is None


def test_normalize_go_import_path_in_subdir(repo):
    modules = {"example.com/m": "server"}
    assert (
        normalize_path("example.com/m/internal/x.go", repo, [], modules)
        == "server/internal/x.go"
    )


def test_normalize_go_import_path_wins_over_a_same_named_repo_path(repo):
    # Module `app` lives in server/, but the repo also has an unrelated app/a.go:
    # the import path app/a.go must not be read as that repo-relative path.
    (repo / "app").mkdir()
    (repo / "app" / "a.go").write_text("package app\n")
    assert normalize_path("app/a.go", repo, [], {"app": "server"}) == "server/a.go"
    assert normalize_path("app/gone.go", repo, [], {"app": "server"}) is None


def test_normalize_go_import_path_longest_module_wins(repo):
    (repo / "server" / "sub").mkdir()
    (repo / "server" / "sub" / "s.go").write_text("package sub\n")
    modules = {"example.com/m": "server", "example.com/m/sub": "server/sub"}
    assert normalize_path("example.com/m/sub/s.go", repo, [], modules) == (
        "server/sub/s.go"
    )


def test_normalize_go_import_path_root_module(repo):
    assert normalize_path(
        "example.com/r/src/a.ts", repo, [], {"example.com/r": ""}
    ) == ("src/a.ts")


def test_normalize_returns_none_for_missing_file(repo):
    assert normalize_path("src/missing.ts", repo, [], {}) is None
    assert normalize_path(f"{repo}/src/missing.ts", repo, [], {}) is None
    assert normalize_path("/elsewhere/src/a.ts", repo, [], {}) is None
    assert normalize_path("src", repo, [], {}) is None  # a directory, not a file


def test_normalize_rejects_paths_outside_repo(repo):
    (repo.parent / "outside.ts").write_text("x\n")
    assert normalize_path("../outside.ts", repo, [], {}) is None
    assert normalize_path("src/../../outside.ts", repo, [], {}) is None
    assert normalize_path(f"{repo}/../outside.ts", repo, [], {}) is None


def test_normalize_collapses_dot_dot_inside_repo(repo):
    assert normalize_path("src/../src/a.ts", repo, [], {}) == "src/a.ts"


# --- load_coverage ---


def test_load_coverage_reads_env_prefixes(repo):
    _fragment(
        repo,
        "coverage-ts.lcov",
        _lcov("/host/ws/src/a.ts", {1: 1}) + _lcov("/ci/ws/src/b.ts", {1: 0}),
    )
    env = {"QUALITY_HOST_WORKSPACE": "/host/ws", "CI_WORKSPACE": "/ci/ws"}
    data = load_coverage(repo, ["typescript"], None, EXCLUDE, env)
    assert data is not None
    assert set(data.files) == {"src/a.ts", "src/b.ts"}
    assert data.unmatched_files == 0


def test_load_coverage_ignores_empty_env_prefix(repo):
    _fragment(repo, "coverage-ts.lcov", _lcov("/elsewhere/src/a.ts", {1: 1}))
    env = {"QUALITY_HOST_WORKSPACE": "", "CI_WORKSPACE": ""}
    assert load_coverage(repo, ["typescript"], None, EXCLUDE, env) is None


def test_coverage_null_when_expected_language_missing(repo, capsys):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 1}))
    assert load_coverage(repo, ["typescript", "rust"], None, EXCLUDE, NO_ENV) is None
    assert "coverage-rust*.lcov" in capsys.readouterr().err


def test_coverage_languages_narrows_expected(repo):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 1}))
    data = load_coverage(repo, ["typescript", "rust"], ["typescript"], EXCLUDE, NO_ENV)
    assert data is not None
    assert data.languages == ["typescript"]
    assert data.untested_languages == ["rust"]


def test_fragment_of_unexpected_language_is_not_read(repo):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 1}))
    _fragment(repo, "coverage-rust.lcov", _lcov("src/b.ts", {1: 0}))
    data = load_coverage(repo, ["typescript", "rust"], ["typescript"], EXCLUDE, NO_ENV)
    assert set(data.files) == {"src/a.ts"}


def test_coverage_null_when_no_languages(repo, capsys):
    assert load_coverage(repo, [], None, EXCLUDE, NO_ENV) is None
    assert "coverage null" in capsys.readouterr().err


def test_coverage_null_when_nothing_matches(repo, capsys):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/nope.ts", {1: 1}))
    assert load_coverage(repo, ["typescript"], None, EXCLUDE, NO_ENV) is None
    assert "coverage null" in capsys.readouterr().err


def test_coverage_null_when_fragment_is_empty(repo, capsys):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 1}))
    _fragment(repo, "coverage-go.out", "mode: set\n")
    assert load_coverage(repo, ["go", "typescript"], None, EXCLUDE, NO_ENV) is None
    assert "go" in capsys.readouterr().err


def test_coverage_null_when_fragment_is_malformed(repo, capsys):
    _fragment(repo, "coverage-ts.lcov", "SF:src/a.ts\nDA:one,1\nend_of_record\n")
    assert load_coverage(repo, ["typescript"], None, EXCLUDE, NO_ENV) is None
    assert "coverage-ts.lcov" in capsys.readouterr().err


def test_coverage_null_when_one_language_matches_nothing(repo, capsys):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 1}))
    _fragment(repo, "coverage-go.out", "mode: set\nother.org/x/a.go:1.1,2.2 1 1\n")
    assert load_coverage(repo, ["go", "typescript"], None, EXCLUDE, NO_ENV) is None
    err = capsys.readouterr().err
    assert "go coverage matched no file" in err and "coverage null" in err


def test_lcov_paths_are_not_read_as_go_import_paths(repo):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 1}))
    _fragment(
        repo,
        "coverage-py.lcov",
        _lcov("src/b.ts", {1: 1}) + _lcov("example.com/m/a.go", {1: 0}),
    )
    _fragment(repo, "coverage-go.out", (FIX / "go.out").read_text())
    data = load_coverage(repo, ["go", "python", "typescript"], None, EXCLUDE, NO_ENV)
    assert data.unmatched_files == 1
    assert data.files["server/a.go"].lines == {3: 2, 4: 2, 5: 0, 6: 0, 7: 2}


def test_go_paths_mapped_into_excluded_dirs_are_dropped(repo, capsys):
    _fragment(
        repo,
        "coverage-go.out",
        (FIX / "go.out").read_text() + "example.com/m/build/gen.go:1.1,2.2 1 0\n",
    )
    data = load_coverage(repo, ["go"], None, EXCLUDE, NO_ENV)
    assert data.unmatched_files == 0
    assert capsys.readouterr().err == ""


def test_unmatched_files_counted(repo, capsys):
    _fragment(
        repo,
        "coverage-ts.lcov",
        _lcov("src/a.ts", {1: 1}) + _lcov("src/gone.ts", {1: 0}),
    )
    data = load_coverage(repo, ["typescript"], None, EXCLUDE, NO_ENV)
    assert data is not None
    assert data.unmatched_files == 1
    assert set(data.files) == {"src/a.ts"}
    assert "1 coverage file" in capsys.readouterr().err


def test_excluded_paths_dropped(repo, capsys):
    (repo / "dist").mkdir()
    (repo / "dist" / "x.js").write_text("x\n")
    _fragment(
        repo,
        "coverage-ts.lcov",
        _lcov("src/a.ts", {1: 1})
        + _lcov("dist/x.js", {1: 0, 2: 0})
        + _lcov("/host/ws/node_modules/dep/i.js", {1: 0}),  # not present here
    )
    env = {"QUALITY_HOST_WORKSPACE": "/host/ws"}
    data = load_coverage(repo, ["typescript"], None, EXCLUDE, env)
    assert set(data.files) == {"src/a.ts"}
    assert data.unmatched_files == 0
    assert capsys.readouterr().err == ""


def test_fragments_of_one_language_merged_by_max(repo):
    _fragment(repo, "coverage-ts.lcov", _lcov("src/a.ts", {1: 0, 2: 4}))
    _fragment(repo, "coverage-ts-web.lcov", _lcov("./src/a.ts", {1: 2, 2: 0}))
    data = load_coverage(repo, ["typescript"], None, EXCLUDE, NO_ENV)
    assert data.files["src/a.ts"].lines == {1: 2, 2: 4}


def test_load_coverage_go_fixture(repo):
    _fragment(repo, "coverage-go.out", (FIX / "go.out").read_text())
    data = load_coverage(repo, ["go"], None, EXCLUDE, NO_ENV)
    assert set(data.files) == {"server/a.go", "server/internal/x.go"}
    assert data.files["server/a.go"].lines == {3: 2, 4: 2, 5: 0, 6: 0, 7: 2}
    assert data.branch_languages == []


def test_branch_languages_from_brda_records(repo):
    _fragment(repo, "coverage-ts.lcov", (FIX / "ts.lcov").read_text())
    _fragment(repo, "coverage-py.lcov", _lcov("src/b.ts", {1: 1}))
    data = load_coverage(repo, ["python", "typescript"], None, EXCLUDE, NO_ENV)
    assert data.languages == ["python", "typescript"]
    assert data.branch_languages == ["typescript"]


# --- summary ---


def test_summarize_coverage():
    data = CoverageData(
        files={
            "src/a.ts": FileCoverage(
                lines={1: 1, 2: 3, 3: 0}, branches={"1,0,0": 1, "1,0,1": 0}
            ),
            "src/b.ts": FileCoverage(lines={1: 2}, branches={}),
        },
        languages=["typescript"],
        untested_languages=[],
        branch_languages=["typescript"],
        unmatched_files=0,
    )
    assert summarize_coverage(data) == {
        "lines_percent": 75.0,
        "lines_covered": 3,
        "lines_total": 4,
        "branches_percent": 50.0,
        "branch_languages": ["typescript"],
        "languages": ["typescript"],
        "untested_languages": [],
        "unmatched_files": 0,
    }


def test_summarize_coverage_rounds_to_two_decimals():
    data = CoverageData(
        files={"a.go": FileCoverage(lines={1: 1, 2: 0, 3: 0}, branches={})},
        languages=["go"],
        untested_languages=[],
        branch_languages=[],
        unmatched_files=0,
    )
    assert summarize_coverage(data)["lines_percent"] == 33.33


def test_summarize_coverage_none():
    assert summarize_coverage(None) is None


def test_branches_null_for_go_only(repo):
    _fragment(repo, "coverage-go.out", (FIX / "go.out").read_text())
    summary = summarize_coverage(load_coverage(repo, ["go"], None, EXCLUDE, NO_ENV))
    assert summary["branches_percent"] is None
    assert summary["branch_languages"] == []
    assert summary["lines_total"] == 8
    assert summary["lines_covered"] == 3


# --- config ---


def test_every_detected_language_has_a_fragment_pattern():
    assert set(FRAGMENTS) == set(LANGUAGES)


def test_config_coverage_languages_validated(tmp_path):
    assert load_config(tmp_path).coverage_languages is None
    (tmp_path / ".quality.yml").write_text("coverage_languages: [typescript, go]\n")
    assert load_config(tmp_path).coverage_languages == ["typescript", "go"]
    (tmp_path / ".quality.yml").write_text("coverage_languages: [typescript, ts]\n")
    with pytest.raises(ValueError, match="ts"):
        load_config(tmp_path)


def test_config_coverage_languages_must_be_list(tmp_path):
    (tmp_path / ".quality.yml").write_text("coverage_languages: typescript\n")
    with pytest.raises(TypeError, match="coverage_languages"):
        load_config(tmp_path)
