import base64
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest

import collector.hotspots as hot
import collector.main as cmain
from collector.hotspots import (
    Commit,
    FileHotspot,
    HotspotError,
    RepoHotspots,
    git_env,
    is_conventional,
    is_fix,
    measure_repo,
    parse_log,
    production_functions,
    rank_hotspots,
    store_hotspots,
    week_of,
)
from collector.main import main
from quality_report.complexity import FunctionStat
from quality_report.config import DEFAULT_EXCLUDES
from quality_report.rusttests import RustTestCode

EXCLUDE = list(DEFAULT_EXCLUDES)


def entry(name: str, email: str, subject: str, *files: str) -> str:
    return f"\x1e{name}\x1f{email}\x1f{subject}\n\n" + "".join(f"{f}\n" for f in files)


def fn(file: str, ccn: int, nloc: int = 10) -> FunctionStat:
    return FunctionStat(file, f"f{ccn}", ccn, nloc)


def commit(subject: str, *files: str) -> Commit:
    return Commit(subject, frozenset(files))


def test_parse_log_drops_bots_and_authors():
    text = (
        entry("Ana", "ana@example.com", "fix: a", "src/a.ts", "src/b.ts")
        + entry(
            "dependabot[bot]",
            "49699333+dependabot[bot]@users.noreply.github.com",
            "chore: bump",
            "package.json",
        )
        + entry("Renovate Bot", "bot@renovateapp.com", "chore(deps): x", "Cargo.toml")
        + entry("release", "41898282+github-actions@users.noreply.github.com", "v1")
        + entry("semantic-release-bot", "s@example.com", "chore(release): 1.0.0", "a")
        + entry("Release Please", "release-please@example.com", "chore: release", "b")
    )
    commits = parse_log(text)
    assert commits == [commit("fix: a", "src/a.ts", "src/b.ts")]
    assert "Ana" not in repr(commits) and "ana@example.com" not in repr(commits)


def test_parse_log_keeps_ai_assisted_commits():
    text = entry("Claude", "noreply@anthropic.com", "feat: x", "a.py")
    assert parse_log(text) == [commit("feat: x", "a.py")]


def test_parse_log_keeps_a_commit_without_files():
    text = entry("Ana", "a@x", "chore: empty") + entry("Ana", "a@x", "feat: y", "y.py")
    assert parse_log(text) == [commit("chore: empty"), commit("feat: y", "y.py")]


def test_parse_log_keeps_separators_inside_subjects_out():
    # %s never contains \x1e or \x1f; a colon-rich subject parses whole.
    text = entry("Ana", "a@x", "fix(api): a: b: c", "a.py")
    assert parse_log(text) == [commit("fix(api): a: b: c", "a.py")]


def test_parse_log_empty():
    assert parse_log("") == []


def test_fix_and_conventional_forms():
    assert is_fix("fix: x") and is_fix("fix(api)!: x") and is_fix("fix!: x")
    assert not is_fix("fixup! x")
    assert not is_fix("Fix: x")
    assert not is_fix("fix:x")
    assert not is_fix("fixes the thing")
    assert is_conventional("feat(ui): x") and is_conventional("fix: x")
    assert not is_conventional("Add thing")
    assert not is_conventional("WIP: x")


def test_rank_scores_and_orders():
    commits = [
        commit("feat: a", "a.ts"),
        commit("fix: a and b", "a.ts", "b.ts"),
    ]
    functions = [
        fn("a.ts", 4, 5),
        fn("a.ts", 6, 7),
        fn("b.ts", 20, 30),
        fn("c.ts", 5),
    ]
    rows = rank_hotspots(commits, functions, EXCLUDE)
    # a: churn 2/2 × cc 10/20 = 0.5; b: churn 1/2 × cc 20/20 = 0.5; tie → churn desc.
    assert [(h.file, h.rank, h.score) for h in rows] == [
        ("a.ts", 1, 0.5),
        ("b.ts", 2, 0.5),
    ]
    a, b = rows
    assert (a.churn, a.fix_commits, a.complexity, a.nloc) == (2, 1, 10, 12)
    assert (b.churn, b.fix_commits, b.complexity, b.nloc) == (1, 1, 20, 30)


def test_max_complexity_counts_files_without_churn():
    # c.ts has no churn (not stored) but sets the complexity maximum.
    rows = rank_hotspots(
        [commit("feat: a", "a.ts")], [fn("a.ts", 5), fn("c.ts", 20)], EXCLUDE
    )
    assert [(h.file, h.score) for h in rows] == [("a.ts", 0.25)]


def test_score_rounds_to_four_decimals():
    commits = [
        commit("feat: x", "a.ts"),
        commit("feat: y", "a.ts"),
        commit("feat: z", "a.ts", "b.ts"),
    ]
    rows = rank_hotspots(commits, [fn("a.ts", 3), fn("b.ts", 3)], EXCLUDE)
    assert [h.score for h in rows] == [1.0, 0.3333]


def test_rank_tie_breaks_complexity_then_file():
    commits = [commit("feat: x", "b.ts", "a.ts", "c.ts")]
    functions = [fn("a.ts", 2), fn("b.ts", 2), fn("c.ts", 4)]
    rows = rank_hotspots(commits, functions, EXCLUDE)
    assert [h.file for h in rows] == ["c.ts", "a.ts", "b.ts"]
    assert [h.rank for h in rows] == [1, 2, 3]


def test_fix_commits_null_below_half_conventional():
    commits = [
        commit("fix: a", "a.ts"),
        commit("Tweak a", "a.ts"),
        commit("Another tweak", "a.ts"),
    ]
    rows = rank_hotspots(commits, [fn("a.ts", 3)], EXCLUDE)
    assert rows[0].churn == 3
    assert rows[0].fix_commits is None


def test_fix_commits_counted_at_half_conventional():
    commits = [
        commit("fix: a", "a.ts"),
        commit("feat: b", "b.ts"),
        commit("Tweak a", "a.ts"),
        commit("Tweak b", "b.ts"),
    ]
    rows = rank_hotspots(commits, [fn("a.ts", 3), fn("b.ts", 3)], EXCLUDE)
    assert {h.file: h.fix_commits for h in rows} == {"a.ts": 1, "b.ts": 0}


def test_no_commits_gives_no_rows():
    assert rank_hotspots([], [fn("a.ts", 3)], EXCLUDE) == []


def test_no_parsed_functions_gives_no_rows():
    assert rank_hotspots([commit("feat: x", "values.yaml")], [], EXCLUDE) == []


def test_excluded_and_deleted_paths_are_not_candidates():
    commits = [
        commit(
            "feat: x",
            "src/a.ts",
            "dist/a.js",
            "pnpm-lock.yaml",
            "gone.ts",
            "gen/x.ts",
        )
    ]
    functions = [fn("src/a.ts", 3), fn("dist/a.js", 50), fn("gen/x.ts", 50)]
    rows = rank_hotspots(commits, functions, [*EXCLUDE, "gen"])
    assert [h.file for h in rows] == ["src/a.ts"]
    assert rows[0].score == 1.0


def test_production_functions_drop_test_code():
    functions = [
        FunctionStat("src/a.ts", "a", 3, 5, 1, 5),
        FunctionStat("src/a.test.ts", "t", 9, 5, 1, 5),
        FunctionStat("tests/test_x.py", "t", 9, 5, 1, 5),
        FunctionStat("src/lib.rs", "prod", 4, 5, 1, 10),
        FunctionStat("src/lib.rs", "unit_test", 7, 5, 20, 30),
        FunctionStat("crates/core/tests/api.rs", "it", 8, 5, 1, 9),
    ]
    rust = RustTestCode(
        files={"crates/core/tests/api.rs"}, ranges={"src/lib.rs": [(15, 40)]}
    )
    assert [(f.file, f.name) for f in production_functions(functions, rust)] == [
        ("src/a.ts", "a"),
        ("src/lib.rs", "prod"),
    ]


def test_week_of_is_monday_utc():
    assert week_of(datetime(2026, 10, 25, 4, tzinfo=UTC)) == date(2026, 10, 19)
    assert week_of(datetime(2026, 10, 20, 9, tzinfo=UTC)) == date(2026, 10, 19)
    assert week_of(datetime(2026, 10, 19, 0, tzinfo=UTC)) == date(2026, 10, 19)


TOKEN = "ghp_supersecret_token_value"
ENV = {
    "QUALITY_COLLECTOR_URL": "postgresql://u:dbpass@h/quality",
    "GITHUB_TOKEN": TOKEN,
    "QUALITY_GITHUB_OWNER": "jfms7s",
    "QUALITY_HOTSPOT_REPOS": " alpha , ,beta ",
}


def test_git_env_sends_the_token_as_a_header_only():
    env = git_env(TOKEN)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    expected = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"
    assert not any(TOKEN in v for v in env.values())


def _git(repo: Path, *args: str, name: str = "Ana", email: str = "ana@example.com"):
    subprocess.run(
        ["git", "-c", f"user.name={name}", "-c", f"user.email={email}", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


BRANCHY = """def f(x):
    if x > 1:
        return 1
    elif x > 2:
        return 2
    return 3
"""


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text(BRANCHY)
    (repo / "b.py").write_text("def g():\n    return 1\n")
    (repo / "gen").mkdir()
    (repo / "gen" / "x.py").write_text(BRANCHY)
    (repo / ".quality.yml").write_text("exclude: [gen]\n")
    (repo / "tests").mkdir()
    # Different content: lizard skips a file identical to one it already read.
    (repo / "tests" / "test_a.py").write_text("# test\n" + BRANCHY)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feat: start")
    (repo / "a.py").write_text(BRANCHY + "\n")
    (repo / "tests" / "test_a.py").write_text("# test\n" + BRANCHY + "\n")
    _git(repo, "commit", "-q", "-am", "fix: a")
    (repo / "b.py").write_text("def g():\n    return 2\n")
    _git(repo, "commit", "-q", "-am", "chore(deps): bump", name="renovate[bot]")
    return repo


@pytest.mark.tools
def test_measure_repo_on_a_real_repo(git_repo):
    result = measure_repo(git_repo)
    assert (
        result.commits == 2
    )  # the renovate commit is dropped; tests/ and gen/ never rank
    assert [
        (h.file, h.churn, h.fix_commits, h.complexity, h.rank) for h in result.rows
    ] == [
        ("a.py", 2, 1, 3, 1),
        ("b.py", 1, 0, 1, 2),
    ]


def test_measure_repo_fails_when_lizard_fails(git_repo, monkeypatch):
    monkeypatch.setattr(hot, "run_lizard", lambda repo, exclude: None)
    with pytest.raises(HotspotError, match="lizard"):
        measure_repo(git_repo)


def test_measure_repo_fails_on_a_bad_quality_yml(git_repo):
    (git_repo / ".quality.yml").write_text("nope: 1\n")
    with pytest.raises(HotspotError, match=r"\.quality\.yml"):
        measure_repo(git_repo)


def test_measure_repo_fails_outside_a_git_repo(tmp_path):
    with pytest.raises(HotspotError, match="git log failed"):
        measure_repo(tmp_path)


def test_clone_failure_names_no_url_or_token(tmp_path):
    with pytest.raises(HotspotError) as exc:
        hot.clone(str(tmp_path / "missing"), tmp_path / "dest", git_env(TOKEN))
    assert str(exc.value).startswith("git clone failed (exit ")
    assert TOKEN not in str(exc.value) and "missing" not in str(exc.value)


ROWS = [
    FileHotspot("a.py", 2, 1, 3, 6, 1.0, 1),
    FileHotspot("b.py", 1, None, 1, 2, 0.1667, 2),
]


class FakeConn:
    rollbacks = 0

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def cli(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cmain, "connect", lambda url: FakeConn())
    calls = {"clone": [], "store": []}

    def fake_clone(url, dest, env):
        calls["clone"].append(url)
        if "alpha" in url:
            raise HotspotError("git clone failed (exit 128)")

    monkeypatch.setattr(cmain, "clone", fake_clone)
    monkeypatch.setattr(cmain, "measure_repo", lambda path: RepoHotspots(ROWS, 5))
    monkeypatch.setattr(
        cmain,
        "store_hotspots",
        lambda conn, repo, week, rows: calls["store"].append((repo, week, rows)),
    )
    return calls


@pytest.mark.parametrize("missing", list(ENV))
def test_hotspots_missing_env_exits_2(cli, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing)
    assert main(["hotspots"]) == 2
    assert missing in capsys.readouterr().err


def test_hotspots_continue_after_a_failed_clone(cli, capsys):
    assert main(["hotspots"]) == 1
    assert cli["clone"] == [
        "https://github.com/jfms7s/alpha.git",
        "https://github.com/jfms7s/beta.git",
    ]
    week = week_of(datetime.now(UTC))
    assert cli["store"] == [("beta", week, ROWS)]
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"beta week={week} commits=5 files=2 fix_commits=counted"
    ]
    assert "error: alpha: git clone failed (exit 128)" in captured.err
    assert TOKEN not in captured.out + captured.err


def test_hotspots_reports_null_fix_commits_and_empty_repos(cli, monkeypatch, capsys):
    monkeypatch.setenv("QUALITY_HOTSPOT_REPOS", "beta,gamma")
    results = {"beta": RepoHotspots([ROWS[1]], 3), "gamma": RepoHotspots([], 4)}
    monkeypatch.setattr(cmain, "clone", lambda url, dest, env: None)
    monkeypatch.setattr(cmain, "measure_repo", lambda path: results[path.name])
    assert main(["hotspots"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("commits=3 files=1 fix_commits=null")
    assert out[1].endswith("commits=4 files=0 fix_commits=-")
    assert [s[0] for s in cli["store"]] == ["beta", "gamma"]


def test_hotspots_db_rejection_fails_only_that_repo(cli, monkeypatch, capsys):
    monkeypatch.setenv("QUALITY_HOTSPOT_REPOS", "beta,delta")

    def store(conn, repo, week, rows):
        if repo == "beta":
            raise psycopg.errors.NumericValueOutOfRange("value dbpass")
        cli["store"].append(repo)

    monkeypatch.setattr(cmain, "store_hotspots", store)
    assert main(["hotspots"]) == 1
    captured = capsys.readouterr()
    assert cli["store"] == ["delta"]
    assert "error: beta: NumericValueOutOfRange" in captured.err
    assert "dbpass" not in captured.err


def test_hotspots_connection_error_is_secret_safe(cli, monkeypatch, capsys):
    def boom(url):
        raise psycopg.OperationalError(f"could not connect {url}")

    monkeypatch.setattr(cmain, "connect", boom)
    assert main(["hotspots"]) == 1
    err = capsys.readouterr().err
    assert "OperationalError" in err and "dbpass" not in err


def _rows(conn, repo):
    return conn.execute(
        "select week, file, churn, fix_commits, complexity, nloc, score, rank"
        " from hotspots where repo = %s order by week, rank",
        (repo,),
    ).fetchall()


@pytest.mark.db
def test_store_replaces_the_week(collector_conn):
    week, other = date(2026, 10, 19), date(2026, 10, 12)
    store_hotspots(collector_conn, "r", other, ROWS[:1])
    store_hotspots(collector_conn, "r", week, ROWS)
    store_hotspots(collector_conn, "r", week, ROWS[1:])
    assert [
        (w, f, fx, float(s), rk)
        for w, f, _, fx, _, _, s, rk in _rows(collector_conn, "r")
    ] == [
        (other, "a.py", 1, 1.0, 1),
        (week, "b.py", None, 0.1667, 2),
    ]


@pytest.mark.db
def test_store_empty_week_clears_it(collector_conn):
    week = date(2026, 10, 19)
    store_hotspots(collector_conn, "s", week, ROWS)
    store_hotspots(collector_conn, "s", week, [])
    assert _rows(collector_conn, "s") == []


@pytest.mark.db
def test_grafana_can_read_hotspots(collector_conn, grafana_conn):
    store_hotspots(collector_conn, "g", date(2026, 10, 19), ROWS)
    assert grafana_conn.execute(
        "select count(*) from hotspots where repo = 'g'"
    ).fetchone() == (2,)
