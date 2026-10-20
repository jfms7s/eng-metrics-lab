from datetime import UTC, date, datetime

from collector.hotspots import (
    Commit,
    is_conventional,
    is_fix,
    parse_log,
    rank_hotspots,
    week_of,
)
from quality_report.complexity import FunctionStat
from quality_report.config import DEFAULT_EXCLUDES

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


def test_week_of_is_monday_utc():
    assert week_of(datetime(2026, 10, 25, 4, tzinfo=UTC)) == date(2026, 10, 19)
    assert week_of(datetime(2026, 10, 20, 9, tzinfo=UTC)) == date(2026, 10, 19)
    assert week_of(datetime(2026, 10, 19, 0, tzinfo=UTC)) == date(2026, 10, 19)
