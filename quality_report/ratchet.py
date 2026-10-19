"""Count-based ratchet: a report may not be worse than the committed baseline."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

DUPLICATION_TOLERANCE = 0.1
COVERAGE_TOLERANCE = 0.1
_EPSILON = 1e-9

_COMPLEXITY_FIELD = "complexity.functions_over_threshold"


def _duplication_current(report: dict) -> float | None:
    dup = report.get("duplication")
    return None if dup is None else dup.get("percent")


def _dead_code_current(report: dict) -> int | None:
    dead = report.get("dead_code")
    return None if dead is None else dead.get("total")


def _over_current(block: dict | None, threshold: int | None, label: str) -> int | None:
    """Count at the *baseline's* threshold: the report's own count if thresholds match, else `over`."""
    if block is None or threshold is None:
        return None
    if block.get("threshold") == threshold:
        return block.get("functions_over_threshold")
    over = block.get("over") or {}
    key = str(threshold)
    if key not in over:
        print(
            f"ratchet: report has no {label} count at baseline threshold {key}; "
            f"skipping {label}",
            file=sys.stderr,
        )
        return None
    return over[key]


def _complexity_current(report: dict, baseline: dict) -> int | None:
    return _over_current(
        report.get("complexity"), baseline.get("complexity_threshold"), "complexity"
    )


def _cognitive_current(report: dict, baseline: dict) -> int | None:
    return _over_current(
        report.get("cognitive"), baseline.get("cognitive_threshold"), "cognitive"
    )


def _coverage_current(report: dict) -> float | None:
    coverage = report.get("coverage")
    return None if coverage is None else coverage.get("lines_percent")


def _rows(
    report: dict, baseline: dict
) -> list[tuple[str, object, object, float, bool]]:
    """(field, baseline, current, tolerance, higher_is_worse), only where both are non-null."""
    rows = [
        (
            "duplication.percent",
            baseline.get("duplication_percent"),
            _duplication_current(report),
            DUPLICATION_TOLERANCE,
            True,
        ),
        (
            "dead_code.total",
            baseline.get("dead_code_total"),
            _dead_code_current(report),
            0,
            True,
        ),
        (
            _COMPLEXITY_FIELD,
            baseline.get("functions_over_threshold"),
            _complexity_current(report, baseline),
            0,
            True,
        ),
        (
            "cognitive.functions_over_threshold",
            baseline.get("cognitive_over_threshold"),
            _cognitive_current(report, baseline),
            0,
            True,
        ),
        (
            "coverage.lines_percent",
            baseline.get("coverage_lines_percent"),
            _coverage_current(report),
            COVERAGE_TOLERANCE,
            False,
        ),
    ]
    return [r for r in rows if r[1] is not None and r[2] is not None]


def _worse_by(base: float, cur: float, higher_is_worse: bool) -> float:
    """How much worse `cur` is than `base` in the row's own direction (negative = better)."""
    return cur - base if higher_is_worse else base - cur


def check(report: dict, baseline: dict | None) -> dict:
    if baseline is None:
        return {"result": "skipped", "baseline_commit": None, "violations": []}
    violations = [
        {"field": field, "baseline": base, "current": cur, "tolerance": tol}
        for field, base, cur, tol, higher_is_worse in _rows(report, baseline)
        if _worse_by(base, cur, higher_is_worse) > tol + _EPSILON
    ]
    return {
        "result": "fail" if violations else "pass",
        "baseline_commit": baseline.get("commit"),
        "violations": violations,
    }


def can_tighten(report: dict, baseline: dict) -> bool:
    """True when some metric present in both is strictly better than the baseline."""
    return any(
        _worse_by(base, cur, higher_is_worse) < -tol - _EPSILON
        for _, base, cur, tol, higher_is_worse in _rows(report, baseline)
    )


def make_baseline(report: dict, now: datetime) -> dict:
    duplication = report.get("duplication")
    dead_code = report.get("dead_code")
    complexity = report.get("complexity")
    cognitive = report.get("cognitive")
    coverage = report.get("coverage")
    return {
        "schema": 1,
        "recorded_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": report["commit"],
        "duplication_percent": None if duplication is None else duplication["percent"],
        "dead_code_total": None if dead_code is None else dead_code["total"],
        "complexity_threshold": None if complexity is None else complexity["threshold"],
        "functions_over_threshold": (
            None if complexity is None else complexity["functions_over_threshold"]
        ),
        "cognitive_threshold": None if cognitive is None else cognitive["threshold"],
        "cognitive_over_threshold": (
            None if cognitive is None else cognitive["functions_over_threshold"]
        ),
        "coverage_lines_percent": (
            None if coverage is None else coverage["lines_percent"]
        ),
    }
