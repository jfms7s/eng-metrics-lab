from __future__ import annotations

import json
import os
import re
from pathlib import Path

from quality_report.detect import is_excluded
from quality_report.tools import run_tool, warn

KINDS = ("unused_files", "unused_exports", "unused_dependencies", "unused_symbols", "suppressed")

# Shape of `knip --reporter json` (5.x): a top-level `files` list plus an `issues`
# list with one record per file. List-valued fields hold findings directly;
# `enumMembers` / `classMembers` map each enum/class name to its unused members.
_KNIP_LIST_FIELDS = {
    "unused_dependencies": ("dependencies", "devDependencies", "optionalPeerDependencies"),
    "unused_exports": ("exports", "types"),
}
_KNIP_MEMBER_FIELDS = ("enumMembers", "classMembers")

_DEAD_CODE_ALLOW = re.compile(r"#!?\[\s*(?:allow|expect)\s*\([^\]]*\bdead_code\b[^\]]*\)\s*\]")
_MACHETE_CRATE = re.compile(r"^\t\S+$")


def parse_knip(path: Path) -> dict[str, int] | None:
    """Dead-code counts from a knip JSON fragment; None if absent or unusable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text() or "null")
    except (OSError, ValueError) as exc:
        warn(f"knip fragment {path} is not valid JSON ({exc}); skipped")
        return None
    if not isinstance(data, dict):
        warn(f"knip fragment {path} is empty or not a knip report; skipped")
        return None

    counts = dict.fromkeys(("unused_files", "unused_exports", "unused_dependencies", "unused_symbols"), 0)
    counts["unused_files"] = len(data.get("files", []))
    for record in data.get("issues", []):
        for kind, fields in _KNIP_LIST_FIELDS.items():
            counts[kind] += sum(len(record.get(f, [])) for f in fields)
        for field in _KNIP_MEMBER_FIELDS:
            counts["unused_symbols"] += sum(len(m) for m in record.get(field, {}).values())
    return counts


def count_dead_code_allows(repo: Path, exclude: list[str]) -> int:
    """Number of `#[allow/expect(...dead_code...)]` attributes in non-excluded *.rs files."""
    total = 0
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = [d for d in dirs if not is_excluded(rel_root / d, exclude)]
        for name in files:
            if not name.endswith(".rs") or is_excluded(rel_root / name, exclude):
                continue
            try:
                text = (Path(root) / name).read_text(errors="replace")
            except OSError as exc:
                warn(f"could not read {rel_root / name} ({exc}); skipped")
                continue
            total += len(_DEAD_CODE_ALLOW.findall(text))
    return total


def count_machete_crates(output: str) -> int:
    """Count unused crates in cargo-machete stdout (one tab-indented crate name per line)."""
    return sum(1 for line in output.splitlines() if _MACHETE_CRATE.match(line))


def run_cargo_machete(repo: Path) -> int | None:
    """Unused-crate count from cargo-machete; None (with a warning) if it cannot run.

    machete exits non-zero when it finds unused crates, so the exit code is ignored.
    """
    result = run_tool(["cargo", "machete", str(repo)])
    if result is None:
        return None
    if "no such command" in result.stderr:
        warn("cargo-machete not installed; skipped")
        return None
    return count_machete_crates(result.stdout)


def _has_cargo_manifest(repo: Path, exclude: list[str]) -> bool:
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = [d for d in dirs if not is_excluded(rel_root / d, exclude)]
        if "Cargo.toml" in files and not is_excluded(rel_root / "Cargo.toml", exclude):
            return True
    return False


def scan_rust(repo: Path, exclude: list[str]) -> dict[str, int] | None:
    """Rust dead-code signals; None when the repo has no Cargo.toml."""
    if not _has_cargo_manifest(repo, exclude):
        return None
    counts = {"suppressed": count_dead_code_allows(repo, exclude)}
    unused = run_cargo_machete(repo)
    if unused is not None:
        counts["unused_dependencies"] = unused
    return counts


def merge_dead_code(parts: list[dict[str, int] | None]) -> dict | None:
    """Sum per-kind counts across parts; None if no part has data."""
    present = [p for p in parts if p is not None]
    if not present:
        return None
    by_kind = {kind: sum(p.get(kind, 0) for p in present) for kind in KINDS}
    return {"total": sum(by_kind.values()), "by_kind": by_kind}
