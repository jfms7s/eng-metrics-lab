from __future__ import annotations

import json
from pathlib import Path

from quality_report.complexity import _relative
from quality_report.tools import run_tool, warn

REPORT_NAME = "jscpd-report.json"
_GLOB_CHARS = set("*?[")


def jscpd_ignore_globs(exclude: list[str]) -> list[str]:
    """Turn exclude entries into jscpd `--ignore` globs matching top-level and nested paths.

    A bare name (`node_modules`, `go.sum`) may be a directory or a file, so both
    `**/name/**` and `**/name` are emitted. Entries with a wildcard or a slash
    are anchored with `**/` unless they already are.
    """
    globs: list[str] = []
    for entry in exclude:
        core = entry.strip("/")
        if not core:
            continue
        if core.startswith("**/") or core.endswith("/**"):
            globs.append(core if core.startswith("**/") else f"**/{core}")
        elif "/" not in core and not _GLOB_CHARS & set(core):
            globs += [f"**/{core}/**", f"**/{core}"]
        else:
            globs.append(f"**/{core}")
    return globs


_LANGUAGE_FORMATS: dict[str, tuple[str, ...]] = {
    "typescript": ("typescript", "javascript", "tsx", "jsx"),
    "rust": ("rust",),
    "python": ("python",),
    "go": ("go",),
}


def duplication_formats(languages: list[str], override: list[str] | None) -> list[str]:
    """jscpd `--format` names: the config override, else those of the detected languages.

    Restricting jscpd to code formats keeps YAML, Markdown, SQL and the like out of the figure.
    """
    if override is not None:
        return list(override)
    formats: list[str] = []
    for language in languages:
        for fmt in _LANGUAGE_FORMATS.get(language, ()):
            if fmt not in formats:
                formats.append(fmt)
    return formats


def run_jscpd(
    repo: Path, exclude: list[str], min_tokens: int, out_dir: Path, formats: list[str]
) -> Path | None:
    """Run jscpd over the repo; the JSON report path, or None (with a warning) on any failure."""
    cmd = [
        "jscpd",
        "--min-tokens", str(min_tokens),
        "--format", ",".join(formats),
        "--reporters", "json",
        "--output", str(out_dir),
        "--ignore", ",".join(jscpd_ignore_globs(exclude)),
        "--silent",
        str(repo),
    ]  # fmt: skip
    result = run_tool(cmd)
    if result is None:
        return None
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or [
            f"exit code {result.returncode}"
        ]
        warn(f"jscpd failed ({detail[0]}); duplication skipped")
        return None
    report = out_dir / REPORT_NAME
    if not report.is_file():
        warn("jscpd produced no report; duplication skipped")
        return None
    return report


def parse_jscpd_report(path: Path, min_tokens: int) -> dict | None:
    """Summarise `statistics.total` of a jscpd JSON report; None (with a warning) if unusable."""
    try:
        total = json.loads(path.read_text())["statistics"]["total"]
        return {
            "percent": round(float(total["percentage"]), 2),
            "duplicated_lines": int(total["duplicatedLines"]),
            "clones": int(total["clones"]),
            "min_tokens": min_tokens,
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        warn(f"jscpd report {path} unreadable ({exc!r}); duplication skipped")
        return None


def parse_jscpd_clones(path: Path, repo: Path | None = None) -> list[dict]:
    """Every clone pair of a jscpd JSON report as `{"a": {...}, "b": {...}}`; [] if unusable.

    Unreadable reports are not warned about again: `parse_jscpd_report` already does.
    """
    try:
        duplicates = json.loads(path.read_text())["duplicates"]
        return [
            {
                "a": _clone_side(clone["firstFile"], repo),
                "b": _clone_side(clone["secondFile"], repo),
            }
            for clone in duplicates
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _clone_side(side: dict, repo: Path | None) -> dict:
    return {
        "file": _relative(side["name"], repo),
        "start": int(side["start"]),
        "end": int(side["end"]),
    }
