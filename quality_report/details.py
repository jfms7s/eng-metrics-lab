"""The offender details file (`details.json`): what to fix, worst first."""

from __future__ import annotations

from quality_report.complexity import FunctionStat
from quality_report.testgap import CRAP_THRESHOLD, ScoredFunction

SCHEMA_VERSION = 1


def _entry(file: str, name: str, line: int, value: float) -> dict:
    return {"file": file, "function": name, "line": line, "value": value}


def _worst_first(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: (-e["value"], e["file"], e["line"]))


def build_details(
    functions: list[FunctionStat],
    complexity_threshold: int,
    cognitive_threshold: int,
    scored: list[ScoredFunction],
    clones: list[dict],
) -> dict:
    """Offenders only (strictly over each threshold), each list worst first."""
    complexity = [
        _entry(f.file, f.name, f.start, f.ccn)
        for f in functions
        if f.ccn > complexity_threshold
    ]
    cognitive = [
        _entry(f.file, f.name, f.start, f.cognitive)
        for f in functions
        if f.cognitive is not None and f.cognitive > cognitive_threshold
    ]
    test_gap = [
        {**_entry(s.file, s.name, s.start, s.crap), "coverage": s.coverage}
        for s in scored
        if s.crap > CRAP_THRESHOLD
    ]
    return {
        "schema": SCHEMA_VERSION,
        "complexity": _worst_first(complexity),
        "cognitive": _worst_first(cognitive),
        "test_gap": _worst_first(test_gap),
        "duplication": clones,
    }
