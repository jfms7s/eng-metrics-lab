"""Test gap: CRAP score per function from its complexity and line coverage (spec section 5).

CRAP = ccn**2 * (1 - coverage)**3 + ccn. A function's coverage is the share of its
own instrumented lines (inside its start..end range, minus the ranges of functions
nested in it) that were hit.
"""

from __future__ import annotations

from dataclasses import dataclass

from quality_report.complexity import FunctionStat
from quality_report.coverage import CoverageData, FileCoverage
from quality_report.detect import language_of

CRAP_THRESHOLD = 30


@dataclass
class ScoredFunction:
    file: str
    name: str
    start: int
    ccn: int
    coverage: float
    crap: float


def crap(ccn: int, coverage: float) -> float:
    return ccn**2 * (1 - coverage) ** 3 + ccn


def _nested_in(fn: FunctionStat, other: FunctionStat) -> bool:
    """True if `other` lies strictly inside `fn` (same file, narrower range)."""
    return (
        other.file == fn.file
        and other.start >= fn.start
        and other.end <= fn.end
        and (other.start, other.end) != (fn.start, fn.end)
    )


def _coverage_in_range(
    file_cov: FileCoverage, fn: FunctionStat, nested: list[FunctionStat]
) -> float | None:
    """Covered share of the function's own instrumented lines; None if it has none.

    Lines of functions nested inside `fn` belong to those functions, not to `fn`.
    """
    hits = [
        h
        for n, h in file_cov.lines.items()
        if fn.start <= n <= fn.end and not any(o.start <= n <= o.end for o in nested)
    ]
    return sum(h > 0 for h in hits) / len(hits) if hits else None


def score_functions(
    fns: list[FunctionStat], data: CoverageData
) -> tuple[list[ScoredFunction], int]:
    """Score each function; the int counts functions whose file has no coverage data.

    A function with no instrumented lines (a declaration, a type) is neither scored
    nor counted as lacking data, and neither is one in a file of an untested language.
    """
    scored: list[ScoredFunction] = []
    without_data = 0
    for fn in fns:
        file_cov = data.files.get(fn.file)
        if file_cov is None:
            if language_of(fn.file) not in data.untested_languages:
                without_data += 1
            continue
        nested = [o for o in fns if _nested_in(fn, o)]
        coverage = _coverage_in_range(file_cov, fn, nested)
        if coverage is not None:
            score = round(crap(fn.ccn, coverage), 2)
            scored.append(
                ScoredFunction(fn.file, fn.name, fn.start, fn.ccn, coverage, score)
            )
    return scored, without_data


def summarize_test_gap(scored: list[ScoredFunction], without_data: int) -> dict | None:
    """The report's `test_gap` block; None when no function could be scored."""
    if not scored:
        return None
    return {
        "metric": "crap",
        "threshold": CRAP_THRESHOLD,
        "functions_over_threshold": sum(s.crap > CRAP_THRESHOLD for s in scored),
        "max": max(s.crap for s in scored),
        "functions_scored": len(scored),
        "functions_without_data": without_data,
    }
