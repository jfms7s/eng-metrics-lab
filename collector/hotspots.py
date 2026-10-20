"""Weekly hotspots: churn × complexity per file (spec §9). File level only, never per author."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

from quality_report.complexity import FunctionStat
from quality_report.detect import is_excluded

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
# Below this share of Conventional Commits, fix_commits is null for the repo (decision D6).
MIN_CONVENTIONAL_SHARE = 0.5


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
