from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from pathlib import Path

from quality_report.tools import run_tool, warn

OVER_THRESHOLDS = (7, 10, 15)
COGNITIVE_OVER_THRESHOLDS = (10, 15, 25)

# lizard CSV columns: NLOC, CCN, token, PARAM, length, location, file, function,
# long_name, start, end, and (with -Ecognitive) CogC
_NLOC, _CCN, _FILE, _LONG_NAME, _START, _END, _COGNITIVE = 0, 1, 6, 8, 9, 10, 11
_MIN_COLUMNS = 9


@dataclass
class FunctionStat:
    file: str
    name: str
    ccn: int
    nloc: int
    start: int = 0
    end: int = 0
    cognitive: int | None = None


def lizard_exclude_args(exclude: list[str]) -> list[str]:
    """Turn exclude entries into lizard `-x` args.

    lizard fnmatches `-x` against the paths it walks (`./node_modules/x.ts`,
    run from the repo root). Each entry is emitted anchored at the root and
    nested, both as the path itself and as a directory prefix.
    """
    args: list[str] = []
    for entry in exclude:
        core = entry.removeprefix("**/").removesuffix("/**").strip("/")
        if not core:
            continue
        for pattern in (f"./{core}", f"./{core}/*", f"*/{core}", f"*/{core}/*"):
            args += ["-x", pattern]
    return args


def run_lizard(repo: Path, exclude: list[str]) -> str | None:
    """Run `lizard --csv` over the repo; None (with a warning) if it is missing or fails."""
    result = run_tool(
        ["lizard", "--csv", "-Ecognitive", *lizard_exclude_args(exclude), "."], cwd=repo
    )
    if result is None:
        return None
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or [
            f"exit code {result.returncode}"
        ]
        warn(f"lizard failed ({detail[0]}); complexity skipped")
        return None
    return result.stdout


def _relative(path: str, repo: Path | None) -> str:
    if repo is not None and Path(path).is_absolute():
        try:
            return Path(path).relative_to(repo).as_posix()
        except ValueError:
            return path
    return path.removeprefix("./")


def parse_lizard_csv(text: str, repo: Path | None = None) -> list[FunctionStat]:
    """Parse lizard CSV rows; skips a header row and malformed rows."""
    stats: list[FunctionStat] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < _MIN_COLUMNS:
            continue
        try:
            ccn, nloc = int(row[_CCN]), int(row[_NLOC])
        except ValueError:
            continue  # header row
        start, end, cognitive = _optional_ints(row)
        stats.append(
            FunctionStat(
                _relative(row[_FILE], repo),
                row[_LONG_NAME],
                ccn,
                nloc,
                start,
                end,
                cognitive,
            )
        )
    return stats


def _optional_ints(row: list[str]) -> tuple[int, int, int | None]:
    """start, end and CogC from the trailing columns; 0, 0, None when absent or unparsable."""
    try:
        start, end = int(row[_START]), int(row[_END])
    except (IndexError, ValueError):
        return 0, 0, None
    try:
        return start, end, int(row[_COGNITIVE])
    except (IndexError, ValueError):
        return start, end, None


def summarize_complexity(fns: list[FunctionStat], threshold: int) -> dict | None:
    if not fns:
        return None
    ccns = sorted(f.ccn for f in fns)
    p95 = ccns[math.ceil(0.95 * len(ccns)) - 1]
    return {
        "metric": "cyclomatic",
        "threshold": threshold,
        "functions_over_threshold": sum(c > threshold for c in ccns),
        "over": {str(t): sum(c > t for c in ccns) for t in OVER_THRESHOLDS},
        "max": ccns[-1],
        "p95": p95,
        "avg": round(sum(ccns) / len(ccns), 2),
    }


def summarize_cognitive(fns: list[FunctionStat], threshold: int) -> dict | None:
    """Cognitive-complexity summary; None unless every function has a CogC value."""
    if not fns or any(f.cognitive is None for f in fns):
        return None
    values = sorted(f.cognitive for f in fns if f.cognitive is not None)
    p95 = values[math.ceil(0.95 * len(values)) - 1]
    return {
        "metric": "cognitive",
        "threshold": threshold,
        "functions_over_threshold": sum(v > threshold for v in values),
        "over": {str(t): sum(v > t for v in values) for t in COGNITIVE_OVER_THRESHOLDS},
        "max": values[-1],
        "p95": p95,
        "avg": round(sum(values) / len(values), 2),
    }


def summarize_size(fns: list[FunctionStat]) -> dict | None:
    if not fns:
        return None
    return {
        "files": len({f.file for f in fns}),
        "nloc": sum(f.nloc for f in fns),
        "functions": len(fns),
    }
