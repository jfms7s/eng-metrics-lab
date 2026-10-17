"""Count-based ratchet: a report may not be worse than the committed baseline."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

DUPLICATION_TOLERANCE = 0.1
_EPSILON = 1e-9

_COMPLEXITY_FIELD = "complexity.functions_over_threshold"


def _duplication_current(report: dict) -> float | None:
    dup = report.get("duplication")
    return None if dup is None else dup.get("percent")


def _dead_code_current(report: dict) -> int | None:
    dead = report.get("dead_code")
    return None if dead is None else dead.get("total")


def _complexity_current(report: dict, baseline: dict) -> int | None:
    """Count at the *baseline's* threshold: the report's own count if thresholds match, else `over`."""
    complexity = report.get("complexity")
    threshold = baseline.get("complexity_threshold")
    if complexity is None or threshold is None:
        return None
    if complexity.get("threshold") == threshold:
        return complexity.get("functions_over_threshold")
    over = complexity.get("over") or {}
    key = str(threshold)
    if key not in over:
        print(
            f"ratchet: report has no complexity count at baseline threshold {key}; "
            "skipping complexity",
            file=sys.stderr,
        )
        return None
    return over[key]


def _rows(report: dict, baseline: dict) -> list[tuple[str, object, object, float]]:
    """(field, baseline value, current value, tolerance), only where both are non-null."""
    rows = [
        (
            "duplication.percent",
            baseline.get("duplication_percent"),
            _duplication_current(report),
            DUPLICATION_TOLERANCE,
        ),
        (
            "dead_code.total",
            baseline.get("dead_code_total"),
            _dead_code_current(report),
            0,
        ),
        (
            _COMPLEXITY_FIELD,
            baseline.get("functions_over_threshold"),
            _complexity_current(report, baseline),
            0,
        ),
    ]
    return [r for r in rows if r[1] is not None and r[2] is not None]


def check(report: dict, baseline: dict | None) -> dict:
    if baseline is None:
        return {"result": "skipped", "baseline_commit": None, "violations": []}
    violations = [
        {"field": field, "baseline": base, "current": cur, "tolerance": tol}
        for field, base, cur, tol in _rows(report, baseline)
        if cur > base + tol + _EPSILON
    ]
    return {
        "result": "fail" if violations else "pass",
        "baseline_commit": baseline.get("commit"),
        "violations": violations,
    }


def can_tighten(report: dict, baseline: dict) -> bool:
    """True when some metric present in both is strictly better than the baseline."""
    return any(
        cur < base - tol - _EPSILON for _, base, cur, tol in _rows(report, baseline)
    )


def make_baseline(report: dict, now: datetime) -> dict:
    duplication = report.get("duplication")
    dead_code = report.get("dead_code")
    complexity = report.get("complexity")
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
    }
