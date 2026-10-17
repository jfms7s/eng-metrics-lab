"""Assemble the schema-1 quality report (spec section 6) and drive one run."""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from quality_report import ratchet
from quality_report.ci import RunContext, resolve_context, tool_versions
from quality_report.complexity import (
    FunctionStat,
    parse_lizard_csv,
    run_lizard,
    summarize_complexity,
    summarize_size,
)
from quality_report.config import load_config
from quality_report.deadcode import merge_dead_code, parse_knip, scan_rust
from quality_report.detect import detect_languages
from quality_report.duplication import parse_jscpd_report, run_jscpd
from quality_report.tools import warn

SCHEMA_VERSION = 1
BASELINE_FILENAME = ".quality-baseline.json"
FRAGMENT_DIR = ".quality"
FRAGMENT_GLOB = "deadcode-*.json"
MAX_OFFENDERS_PRINTED = 10

_SKIPPED_RATCHET = {"result": "skipped", "baseline_commit": None, "violations": []}


def build_report(
    ctx: RunContext,
    languages: list[str],
    tools: dict[str, str],
    size: dict | None,
    duplication: dict | None,
    dead_code: dict | None,
    complexity: dict | None,
    ratchet_result: dict,
) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "repo": ctx.repo,
        "commit": ctx.commit,
        "branch": ctx.branch,
        "ci": {"system": ctx.ci_system, "run": ctx.run},
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": tools,
        "languages": languages,
        "size": size,
        "duplication": duplication,
        "dead_code": dead_code,
        "complexity": complexity,
        "ratchet": ratchet_result,
    }


def _load_baseline(repo: Path) -> dict | None:
    path = repo / BASELINE_FILENAME
    if not path.is_file():
        return None
    try:
        baseline = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        warn(f"{BASELINE_FILENAME} is unreadable ({exc}); ratchet skipped")
        return None
    if not isinstance(baseline, dict) or baseline.get("schema") != 1:
        warn(f"{BASELINE_FILENAME} has an unsupported schema; ratchet skipped")
        return None
    return baseline


def _measure_complexity(repo: Path, exclude: list[str]) -> list[FunctionStat]:
    csv_text = run_lizard(repo, exclude)
    return [] if csv_text is None else parse_lizard_csv(csv_text, repo)


def _measure_duplication(repo: Path, exclude: list[str], min_tokens: int, out_dir: Path) -> dict | None:
    report_path = run_jscpd(repo, exclude, min_tokens, out_dir)
    return None if report_path is None else parse_jscpd_report(report_path, min_tokens)


def _measure_dead_code(repo: Path, exclude: list[str]) -> dict | None:
    fragments = sorted((repo / FRAGMENT_DIR).glob(FRAGMENT_GLOB))
    parts = [parse_knip(path) for path in fragments]
    parts.append(scan_rust(repo, exclude))
    return merge_dead_code(parts)


def run(
    repo: Path,
    out: Path,
    config_path: Path | None = None,
    env: Mapping[str, str] = os.environ,
) -> dict:
    """Measure `repo`, write the report to `out` and return it. Never raises on a missing tool."""
    report, _ = _run(repo, out, config_path, env)
    return report


def _run(
    repo: Path, out: Path, config_path: Path | None, env: Mapping[str, str]
) -> tuple[dict, list[FunctionStat]]:
    config = load_config(repo, config_path)
    ctx = resolve_context(env, repo)
    functions = _measure_complexity(repo, config.exclude)

    report = build_report(
        ctx,
        detect_languages(repo, config.exclude),
        tool_versions(),
        summarize_size(functions),
        # jscpd's output goes beside the report, never inside the analysed repo.
        _measure_duplication(repo, config.exclude, config.duplication_min_tokens, out.parent / "jscpd"),
        _measure_dead_code(repo, config.exclude),
        summarize_complexity(functions, config.complexity_threshold),
        _SKIPPED_RATCHET,
    )
    baseline = _load_baseline(repo)
    if baseline is not None:
        report["ratchet"] = ratchet.check(report, baseline)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    return report, functions


def _fmt(value: object, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


def format_summary(report: dict, functions: list[FunctionStat]) -> list[str]:
    """One summary line, then the worst functions over the threshold (highest CCN first)."""
    dup, dead, cx = report["duplication"], report["dead_code"], report["complexity"]
    summary = (
        "quality: "
        f"duplication {_fmt(dup and dup['percent'], '%')}, "
        f"dead code {_fmt(dead and dead['total'])}, "
        f"functions over threshold {_fmt(cx and cx['functions_over_threshold'])}, "
        f"max CCN {_fmt(cx and cx['max'])}, "
        f"ratchet {report['ratchet']['result']}"
    )
    lines = [summary]
    if cx is not None:
        offenders = sorted((f for f in functions if f.ccn > cx["threshold"]),
                           key=lambda f: (-f.ccn, f.file, f.name))
        lines += [f"  {f.file}:{f.name} {f.ccn}" for f in offenders[:MAX_OFFENDERS_PRINTED]]
    return lines


def run_and_print(repo: Path, out: Path, config_path: Path | None) -> dict:
    report, functions = _run(repo, out, config_path, os.environ)
    print("\n".join(format_summary(report, functions)), file=sys.stdout)
    return report
