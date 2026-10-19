"""Markdown feedback for a change: headline numbers, ratchet result, worst offenders."""

from __future__ import annotations

from pathlib import Path

from quality_report import tools

_NA = "n/a"


def changed_files(repo: Path, base: str) -> set[str] | None:
    """Files changed since the merge base of `base` and HEAD; None (warned) if git fails."""
    merge_base = tools.run_tool(["git", "merge-base", base, "HEAD"], cwd=repo)
    if merge_base is None:
        return None
    if merge_base.returncode != 0:
        tools.warn(f"git merge-base {base} HEAD failed; listing all offenders")
        return None
    diff = tools.run_tool(
        ["git", "diff", "--name-only", merge_base.stdout.strip()], cwd=repo
    )
    if diff is None:
        return None
    if diff.returncode != 0:
        tools.warn("git diff failed; listing all offenders")
        return None
    return {line for line in diff.stdout.splitlines() if line}


def _field(report: dict, block: str, key: str, fmt: str = "{}") -> str:
    data = report.get(block)
    if not isinstance(data, dict) or data.get(key) is None:
        return _NA
    return fmt.format(data[key])


def _headline(report: dict) -> str:
    parts = [
        ("duplication", _field(report, "duplication", "percent", "{}%")),
        ("dead code", _field(report, "dead_code", "total")),
        ("cyclomatic over", _field(report, "complexity", "functions_over_threshold")),
        ("cognitive over", _field(report, "cognitive", "functions_over_threshold")),
        ("coverage", _field(report, "coverage", "lines_percent", "{}%")),
        ("CRAP over", _field(report, "test_gap", "functions_over_threshold")),
    ]
    return " | ".join(f"{name} {value}" for name, value in parts)


def _ratchet_lines(report: dict) -> list[str]:
    ratchet = report.get("ratchet")
    if not isinstance(ratchet, dict):
        return []
    lines = [f"ratchet: {ratchet.get('result', _NA)}"]
    for v in ratchet.get("violations") or []:
        lines.append(
            f"- {v['field']}: {v['current']} is worse than baseline {v['baseline']} "
            f"(tolerance {v['tolerance']})"
        )
    return lines


def _function_item(entry: dict) -> str:
    text = f"`{entry['file']}:{entry['line']}` {entry['function']} ({entry['value']}"
    if entry.get("coverage") is not None:
        text += f", coverage {entry['coverage']}"
    return text + ")"


def _clone_item(entry: dict) -> str:
    a, b = entry["a"], entry["b"]
    return (
        f"`{a['file']}:{a['start']}-{a['end']}` duplicates "
        f"`{b['file']}:{b['start']}-{b['end']}`"
    )


_SECTIONS = (
    ("complexity", "Cyclomatic complexity", _function_item),
    ("cognitive", "Cognitive complexity", _function_item),
    ("test_gap", "Test gap (CRAP)", _function_item),
    ("duplication", "Duplication", _clone_item),
)


def _in_changed(entry: dict, changed: set[str] | None) -> bool:
    if changed is None:
        return True
    if "a" in entry:
        return entry["a"]["file"] in changed or entry["b"]["file"] in changed
    return entry["file"] in changed


def render_feedback(
    report: dict,
    details: dict | None,
    changed: set[str] | None,
    max_items: int = 20,
) -> str:
    """Render the report as markdown.

    `max_items` caps the offenders listed across all sections together, in
    section order; the rest are summed in a closing `… and N more`.
    """
    lines = ["### quality-report", "", _headline(report), ""]
    ratchet = _ratchet_lines(report)
    if ratchet:
        lines += [*ratchet, ""]
    if details is None:
        return "\n".join(lines).rstrip() + "\n"

    remaining = max_items
    hidden = 0
    for key, title, item in _SECTIONS:
        entries = [e for e in details.get(key) or [] if _in_changed(e, changed)]
        shown = entries[: max(remaining, 0)]
        hidden += len(entries) - len(shown)
        remaining -= len(shown)
        if shown:
            lines += [f"**{title}**", ""]
            lines += [f"- {item(e)}" for e in shown]
            lines.append("")
    if hidden:
        lines += [f"… and {hidden} more", ""]
    return "\n".join(lines).rstrip() + "\n"
