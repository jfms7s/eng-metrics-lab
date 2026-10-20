"""Weekly hotspots: churn × complexity per file (spec §9). File level only, never per author."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath

import psycopg
import yaml

from quality_report.complexity import FunctionStat, parse_lizard_csv, run_lizard
from quality_report.config import load_config
from quality_report.detect import is_excluded, is_test_file
from quality_report.rusttests import RustTestCode, find_rust_test_code

# One record per commit: \x1e, then author name, e-mail and subject split by \x1f, then
# `--name-only` paths. Neither separator can occur in a subject or a path git prints.
LOG_FORMAT = "%x1e%an%x1f%ae%x1f%s"

# Bots and release automation, matched case-insensitively in the author name or e-mail.
_BOT_MARKERS = (
    "[bot]",
    "renovate",
    "dependabot",
    "github-actions",
    "semantic-release",
    "release-please",
)
_CONVENTIONAL = re.compile(r"^[a-z]+(\([^)]*\))?!?: ")
_FIX = re.compile(r"^fix(\([^)]*\))?!?: ")
CHURN_WINDOW = "90.days"
GIT_TIMEOUT = 300
# Below this share of Conventional Commits, fix_commits is null for the repo (decision D6).
MIN_CONVENTIONAL_SHARE = 0.5


class HotspotError(Exception):
    """A repo whose hotspots can't be computed; the message is safe to log."""


@dataclass(frozen=True)
class Commit:
    subject: str
    files: frozenset[str]


@dataclass
class FileHotspot:
    file: str
    churn: int
    fix_commits: int | None
    complexity: int
    nloc: int
    score: float
    rank: int


def _is_bot(name: str, email: str) -> bool:
    who = f"{name}\x1f{email}".lower()
    return any(marker in who for marker in _BOT_MARKERS)


def parse_log(text: str) -> list[Commit]:
    """Commits from `git log --name-only --format=LOG_FORMAT`, bots removed, authors dropped."""
    commits: list[Commit] = []
    for record in text.split("\x1e")[1:]:
        header, _, body = record.partition("\n")
        name, email, subject = (header.split("\x1f", 2) + ["", ""])[:3]
        if _is_bot(name, email):
            continue
        files = frozenset(line for line in body.splitlines() if line.strip())
        commits.append(Commit(subject, files))
    return commits


def is_conventional(subject: str) -> bool:
    return _CONVENTIONAL.match(subject) is not None


def is_fix(subject: str) -> bool:
    return _FIX.match(subject) is not None


def week_of(moment: datetime) -> date:
    """The Monday of `moment`'s ISO week, the `week` a run's rows are stored under."""
    day = moment.date()
    return day - timedelta(days=day.weekday())


def production_functions(
    functions: list[FunctionStat], rust: RustTestCode
) -> list[FunctionStat]:
    """`functions` minus test code, by the report's test-gap rules (ruling H12).

    Test files by their language's conventions, Rust integration-test files, and Rust
    functions starting inside `#[cfg(test)]` code. A fix commit usually adds a test, so
    test files would rank on the fixes themselves.
    """
    return [
        f
        for f in functions
        if not is_test_file(f.file) and not rust.is_test_line(f.file, f.start)
    ]


def rank_hotspots(
    commits: list[Commit], functions: list[FunctionStat], exclude: list[str]
) -> list[FileHotspot]:
    """Every candidate file with churn, ranked by score (spec §9, rulings H1, H5, H7).

    Candidates are the files lizard parsed that `exclude` keeps; both score maxima are
    taken over them. Files without churn in `commits` are not returned.
    """
    complexity: Counter[str] = Counter()
    nloc: Counter[str] = Counter()
    for f in functions:
        if is_excluded(PurePosixPath(f.file), exclude):
            continue
        complexity[f.file] += f.ccn
        nloc[f.file] += f.nloc
    churn: Counter[str] = Counter()
    fixes: Counter[str] = Counter()
    for c in commits:
        touched = c.files & complexity.keys()
        churn.update(touched)
        if is_fix(c.subject):
            fixes.update(touched)
    if not churn:
        return []

    conventional = sum(is_conventional(c.subject) for c in commits)
    count_fixes = conventional / len(commits) >= MIN_CONVENTIONAL_SHARE
    max_churn = max(churn.values())
    max_complexity = max(complexity.values())
    rows = [
        FileHotspot(
            file=file,
            churn=churn[file],
            fix_commits=fixes[file] if count_fixes else None,
            complexity=complexity[file],
            nloc=nloc[file],
            score=round(
                (churn[file] / max_churn) * (complexity[file] / max_complexity), 4
            ),
            rank=0,
        )
        for file in churn
    ]
    rows.sort(key=lambda h: (-h.score, -h.churn, -h.complexity, h.file))
    for rank, row in enumerate(rows, start=1):
        row.rank = rank
    return rows


@dataclass
class RepoHotspots:
    rows: list[FileHotspot]
    commits: int
    """Non-bot commits in the churn window."""


def git_env(token: str) -> dict[str, str]:
    """Environment for git that authenticates to github.com without the token in argv or a URL."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }


def _git(args: list[str], *, what: str, cwd: Path | None = None, env=None) -> str:
    """Run git; HotspotError naming only `what` and the exit code (stderr may echo URLs)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise HotspotError("git not found") from None
    except subprocess.TimeoutExpired:
        raise HotspotError(f"{what} timed out after {GIT_TIMEOUT}s") from None
    if result.returncode != 0:
        raise HotspotError(f"{what} failed (exit {result.returncode})")
    return result.stdout


def clone(url: str, dest: Path, env: dict[str, str]) -> None:
    """Blob-less clone of the default branch: full history, blobs fetched for HEAD only."""
    _git(
        [
            "clone",
            "--quiet",
            "--filter=blob:none",
            "--single-branch",
            "--no-tags",
            url,
            str(dest),
        ],
        what="git clone",
        env=env,
    )


def measure_repo(path: Path) -> RepoHotspots:
    """Hotspots of the checkout at `path`; HotspotError when git, lizard or `.quality.yml` fails."""
    try:
        config = load_config(path)
    except (ValueError, TypeError, yaml.YAMLError) as exc:
        raise HotspotError(f".quality.yml is invalid ({type(exc).__name__})") from None
    log = _git(
        [
            "-c",
            "core.quotePath=false",
            "log",
            f"--since={CHURN_WINDOW}",
            "--no-merges",
            "--no-renames",
            "--name-only",
            f"--format={LOG_FORMAT}",
        ],
        what="git log",
        cwd=path,
    )
    commits = parse_log(log)
    csv = run_lizard(path, config.exclude)
    if csv is None:
        raise HotspotError("lizard failed")
    functions = production_functions(
        parse_lizard_csv(csv, path), find_rust_test_code(path, config.exclude)
    )
    return RepoHotspots(rank_hotspots(commits, functions, config.exclude), len(commits))


_DELETE = "delete from hotspots where repo = %s and week = %s"
_INSERT = (
    "insert into hotspots (repo, week, file, churn, fix_commits, complexity, nloc, score, rank)"
    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def store_hotspots(
    conn: psycopg.Connection, repo: str, week: date, rows: list[FileHotspot]
) -> None:
    """Replace `repo`'s rows for `week` with `rows`: committed together, or rolled back."""
    try:
        conn.execute(_DELETE, (repo, week))
        with conn.cursor() as cur:
            cur.executemany(
                _INSERT,
                [
                    (
                        repo,
                        week,
                        h.file,
                        h.churn,
                        h.fix_commits,
                        h.complexity,
                        h.nloc,
                        h.score,
                        h.rank,
                    )
                    for h in rows
                ],
            )
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
