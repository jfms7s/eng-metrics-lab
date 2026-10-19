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
    summarize_cognitive,
    summarize_complexity,
    summarize_size,
)
from quality_report.config import load_config
from quality_report.coverage import (
    FRAGMENT_DIR,
    CoverageData,
    load_coverage,
    summarize_coverage,
)
from quality_report.deadcode import (
    GO_FRAGMENT,
    merge_dead_code,
    parse_go_deadcode,
    parse_knip,
    scan_python,
    scan_rust,
)
from quality_report.detect import detect_languages
from quality_report.duplication import (
    duplication_formats,
    parse_jscpd_report,
    run_jscpd,
)
from quality_report.testgap import score_functions, summarize_test_gap
from quality_report.tools import warn

SCHEMA_VERSION = 1
BASELINE_FILENAME = ".quality-baseline.json"
KNIP_FRAGMENT = "deadcode-ts.json"
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
    *,
    cognitive: dict | None = None,
    coverage: dict | None = None,
    test_gap: dict | None = None,
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
        "cognitive": cognitive,
        "coverage": coverage,
        "test_gap": test_gap,
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


def _measure_cognitive(functions: list[FunctionStat], threshold: int) -> dict | None:
    summary = summarize_cognitive(functions, threshold)
    if summary is None and functions:
        warn(
            "lizard gave no cognitive complexity (CogC column missing); cognitive null"
        )
    return summary


def _measure_test_gap(
    functions: list[FunctionStat], coverage: CoverageData | None
) -> dict | None:
    if coverage is None:
        return None
    return summarize_test_gap(*score_functions(functions, coverage))


def _measure_duplication(
    repo: Path,
    exclude: list[str],
    min_tokens: int,
    out_dir: Path,
    languages: list[str],
    formats_override: list[str] | None,
) -> dict | None:
    formats = duplication_formats(languages, formats_override)
    if not formats:
        warn(
            "no code languages detected and no duplication_formats set; duplication skipped"
        )
        return None
    report_path = run_jscpd(repo, exclude, min_tokens, out_dir, formats)
    return None if report_path is None else parse_jscpd_report(report_path, min_tokens)


def _measure_dead_code(
    repo: Path, exclude: list[str], languages: list[str]
) -> dict | None:
    """Merged dead code over every detected language; None if any has no usable source.

    A language whose source is missing or broken (a knip or Go fragment that was not
    produced, a crashed scanner) makes the total null, as does a repo with no languages.
    """
    fragments = {
        "typescript": (KNIP_FRAGMENT, parse_knip),
        "go": (GO_FRAGMENT, parse_go_deadcode),
    }
    scanners = {"rust": scan_rust, "python": scan_python}

    def measure(language: str) -> dict[str, int] | None:
        if language in scanners:
            return scanners[language](repo, exclude)
        if language not in fragments:
            warn(f"{language} detected but has no dead-code source; dead_code null")
            return None
        name, parse = fragments[language]
        fragment = repo / FRAGMENT_DIR / name
        if not fragment.exists():
            warn(
                f"{language} detected but {FRAGMENT_DIR}/{name} is missing; dead_code null"
            )
            return None
        return parse(fragment)

    return merge_dead_code([measure(lang) for lang in languages])


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
    languages = detect_languages(repo, config.exclude)
    coverage = load_coverage(
        repo, languages, config.coverage_languages, config.exclude, env
    )

    report = build_report(
        ctx,
        languages,
        tool_versions(),
        summarize_size(functions),
        # jscpd's output goes beside the report, never inside the analysed repo.
        _measure_duplication(
            repo,
            config.exclude,
            config.duplication_min_tokens,
            out.parent / "jscpd",
            languages,
            config.duplication_formats,
        ),
        _measure_dead_code(repo, config.exclude, languages),
        summarize_complexity(functions, config.complexity_threshold),
        _SKIPPED_RATCHET,
        cognitive=_measure_cognitive(functions, config.cognitive_threshold),
        coverage=summarize_coverage(coverage),
        test_gap=_measure_test_gap(functions, coverage),
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
    cog = report.get("cognitive")
    summary = (
        "quality: "
        f"duplication {_fmt(dup and dup['percent'], '%')}, "
        f"dead code {_fmt(dead and dead['total'])}, "
        f"functions over threshold {_fmt(cx and cx['functions_over_threshold'])}, "
        f"max CCN {_fmt(cx and cx['max'])}, "
        f"functions over cognitive {_fmt(cog and cog['functions_over_threshold'])}, "
        f"ratchet {report['ratchet']['result']}"
    )
    lines = [summary]
    if cx is not None:
        offenders = sorted(
            (f for f in functions if f.ccn > cx["threshold"]),
            key=lambda f: (-f.ccn, f.file, f.name),
        )
        lines += [
            f"  {f.file}:{f.name} {f.ccn}" for f in offenders[:MAX_OFFENDERS_PRINTED]
        ]
    return lines


def run_and_print(repo: Path, out: Path, config_path: Path | None) -> dict:
    report, functions = _run(repo, out, config_path, os.environ)
    print("\n".join(format_summary(report, functions)), file=sys.stdout)
    return report
