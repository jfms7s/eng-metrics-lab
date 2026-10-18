from __future__ import annotations

import json
import os
import re
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from quality_report.detect import MAX_DEPTH, is_excluded
from quality_report.tools import run_tool, warn

KINDS = (
    "unused_files",
    "unused_exports",
    "unused_dependencies",
    "unused_symbols",
    "suppressed",
)

# Shape of `knip --reporter json` (5.x): a top-level `files` list plus an `issues`
# list with one record per file. List-valued fields hold findings directly;
# `enumMembers` / `classMembers` map each enum/class name to its unused members.
_KNIP_LIST_FIELDS = {
    "unused_dependencies": (
        "dependencies",
        "devDependencies",
        "optionalPeerDependencies",
    ),
    "unused_exports": ("exports", "types"),
}
_KNIP_MEMBER_FIELDS = ("enumMembers", "classMembers")

_DEAD_CODE_ALLOW = re.compile(
    r"#!?\[\s*(?:allow|expect)\s*\([^\]]*\bdead_code\b[^\]]*\)\s*\]"
)
GO_FRAGMENT = "deadcode-go.json"
_PYTHON_MANIFESTS = ("pyproject.toml", "setup.py", "requirements*.txt")
_VULTURE_FOUND_EXIT = 3
# vulture's per-file error lines on stderr: an unreadable file, a file with null bytes,
# and a syntax error (`path:line: msg`, but not Python's `path:line: XWarning: msg`).
_VULTURE_FILE_ERROR = re.compile(
    r"^(?:Error: Could not read file "
    r"|\S[^:]*: invalid source code "
    r"|\S[^:]*:\d+: (?!\w*Warning: ))"
)
_DEPTRY_FOUND_EXIT = 1
_DEPTRY_UNUSED_DEPENDENCY = "DEP002"
_MACHETE_CRATE = re.compile(r"^\t\S+$")


def parse_knip(path: Path) -> dict[str, int] | None:
    """Dead-code counts from a knip JSON fragment; None if absent or unusable.

    A usable fragment is a JSON object carrying both of knip's top-level keys,
    `files` and `issues`; anything else (e.g. another tool's output) is rejected.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text() or "null")
    except (OSError, ValueError) as exc:
        warn(f"knip fragment {path} is not valid JSON ({exc}); skipped")
        return None
    if not isinstance(data, dict) or not {"files", "issues"} <= data.keys():
        warn(f"knip fragment {path} is empty or not a knip report; skipped")
        return None

    try:
        return _count_knip(data)
    except (TypeError, AttributeError) as exc:
        warn(f"knip fragment {path} has an unexpected shape ({exc}); skipped")
        return None


def _len_of(value: object) -> int:
    """Items in a knip finding list; a dict of {name: [members]} counts its members."""
    if value is None:
        return 0
    if isinstance(value, dict):
        return sum(len(members) for members in value.values())
    if isinstance(value, list):
        return len(value)
    raise TypeError(f"expected list or object, got {type(value).__name__}")


def _count_knip(data: dict) -> dict[str, int]:
    counts = dict.fromkeys(
        ("unused_files", "unused_exports", "unused_dependencies", "unused_symbols"), 0
    )
    counts["unused_files"] = _len_of(data.get("files"))
    for record in data.get("issues") or []:
        for kind, fields in _KNIP_LIST_FIELDS.items():
            counts[kind] += sum(_len_of(record.get(f)) for f in fields)
        counts["unused_symbols"] += sum(
            _len_of(record.get(f)) for f in _KNIP_MEMBER_FIELDS
        )
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
    """Unused-crate count from cargo-machete; None (with a warning) if the run is broken.

    Exit code 1 just means "unused crates found" and is not a failure. Anything
    above 1, a run that never printed machete's "Analyzing dependencies" banner (e.g. the binary is
    not installed), or per-manifest errors on stderr mean the count is unreliable.
    """
    result = run_tool(["cargo-machete", str(repo)])
    if result is None:
        return None
    combined = result.stdout + result.stderr
    detail = (result.stderr.strip().splitlines() or ["no error output"])[0]
    if result.returncode > 1 or "Analyzing dependencies" not in combined:
        warn(
            f"cargo-machete failed or is not installed (exit {result.returncode}: {detail}); skipped"
        )
        return None
    if "error when handling" in combined:
        warn("cargo-machete could not process some manifests; skipped")
        return None
    return count_machete_crates(result.stdout)


def has_cargo_manifest(repo: Path, exclude: list[str]) -> bool:
    """True if a non-excluded Cargo.toml exists, i.e. the Rust scan is expected."""
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = [d for d in dirs if not is_excluded(rel_root / d, exclude)]
        if "Cargo.toml" in files and not is_excluded(rel_root / "Cargo.toml", exclude):
            return True
    return False


def scan_rust(repo: Path, exclude: list[str]) -> dict[str, int] | None:
    """Rust dead-code signals for a repo that has a Cargo.toml; None if cargo-machete is broken."""
    unused = run_cargo_machete(repo)
    if unused is None:
        return None
    return {
        "suppressed": count_dead_code_allows(repo, exclude),
        "unused_dependencies": unused,
    }


def python_sources(repo: Path, exclude: list[str]) -> list[Path]:
    """Repo-relative paths of non-excluded *.py files, matched like every other exclude."""
    sources: list[Path] = []
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = sorted(d for d in dirs if not is_excluded(rel_root / d, exclude))
        sources.extend(
            rel_root / name
            for name in sorted(files)
            if name.endswith(".py") and not is_excluded(rel_root / name, exclude)
        )
    return sources


def count_vulture_file_errors(stderr: str) -> int:
    """Files vulture reported it could not parse or read; Python warnings do not count."""
    return sum(1 for line in stderr.splitlines() if _VULTURE_FILE_ERROR.match(line))


def run_vulture(repo: Path, exclude: list[str], min_confidence: int = 80) -> int | None:
    """Findings at or above `min_confidence` from vulture; None (with a warning) if the run is broken.

    vulture gets the explicit list of non-excluded *.py files in one run (so usage across
    files resolves) instead of its own `--exclude`, which treats a plain name as a
    substring. No such file means nothing to scan: 0, without running vulture.
    Exit 0 (nothing found) and 3 (dead code found) are success, but vulture also exits 3
    when it skipped files it could not parse or read, so any such file on stderr makes
    the count partial and therefore None. Anything else, such as a missing binary or a
    file list too long for the command line, is None too.
    """
    sources = python_sources(repo, exclude)
    if not sources:
        return 0
    args = ["vulture", "--min-confidence", str(min_confidence), "--"]
    args += [str(s) for s in sources]
    result = run_tool(args, cwd=repo)
    if result is None:
        return None
    if result.returncode not in (0, _VULTURE_FOUND_EXIT):
        detail = (result.stderr.strip().splitlines() or ["no error output"])[0]
        warn(f"vulture failed (exit {result.returncode}: {detail}); skipped")
        return None
    unparseable = count_vulture_file_errors(result.stderr)
    if unparseable:
        warn(f"vulture could not parse or read {unparseable} file(s); skipped")
        return None
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def run_deptry(project: Path) -> int | None:
    """Count of DEP002 (unused dependency) issues deptry finds in `project`; None if the run is broken.

    deptry exits 1 when it found issues but still writes its JSON report, so exit 1 with
    a readable report is success; any other failure, or an unreadable report, is not.
    """
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "deptry.json"
        result = run_tool(["deptry", ".", "--json-output", str(report)], cwd=project)
        if result is None:
            return None
        if result.returncode not in (0, _DEPTRY_FOUND_EXIT):
            detail = (result.stderr.strip().splitlines() or ["no error output"])[0]
            warn(
                f"deptry failed in {project} (exit {result.returncode}: {detail}); skipped"
            )
            return None
        try:
            issues = json.loads(report.read_text())
            return sum(
                1 for i in issues if i["error"]["code"] == _DEPTRY_UNUSED_DEPENDENCY
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            warn(
                f"deptry report for {project} is unusable ({type(exc).__name__}); skipped"
            )
            return None


def find_python_projects(repo: Path, exclude: list[str]) -> list[Path]:
    """Non-excluded directories, at most MAX_DEPTH deep, holding a Python manifest."""
    projects: list[Path] = []
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = sorted(
            d
            for d in dirs
            if len(rel_root.parts) < MAX_DEPTH
            and not is_excluded(rel_root / d, exclude)
        )
        if any(
            fnmatch(name, pattern) and not is_excluded(rel_root / name, exclude)
            for name in files
            for pattern in _PYTHON_MANIFESTS
        ):
            projects.append(Path(root))
    return projects


def scan_python(repo: Path, exclude: list[str]) -> dict[str, int] | None:
    """Python dead-code signals (vulture symbols, deptry dependencies); None if any run is broken.

    A repo with no non-excluded *.py file has 0 unused symbols, but that 0 only stands
    if deptry ran on at least one project; with neither, nothing was measured: None.
    """
    projects = find_python_projects(repo, exclude)
    if not projects and not python_sources(repo, exclude):
        warn("python detected but no *.py file or python project to scan; skipped")
        return None
    symbols = run_vulture(repo, exclude)
    if symbols is None:
        return None
    unused_dependencies = 0
    for project in projects:
        unused = run_deptry(project)
        if unused is None:
            return None
        unused_dependencies += unused
    return {"unused_symbols": symbols, "unused_dependencies": unused_dependencies}


def parse_go_deadcode(path: Path) -> dict[str, int] | None:
    """Dead-code counts from a `deadcode -json` fragment; None if absent or unusable.

    The report is a list of packages, each with a `Funcs` list of unreachable functions
    carrying a `Generated` flag. Generated functions are not the author's to fix, so
    only the others count. A clean run prints `null`, which is a real zero; an empty
    file means the tool never ran to completion, so it is None.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text()
    except OSError as exc:
        warn(f"go fragment {path} could not be read ({type(exc).__name__}); skipped")
        return None
    if not text.strip():
        warn(f"go fragment {path} is empty; skipped")
        return None
    try:
        data = json.loads(text)
    except ValueError as exc:
        warn(f"go fragment {path} is not valid JSON ({type(exc).__name__}); skipped")
        return None
    if data is None:
        return {"unused_symbols": 0}
    if not isinstance(data, list):
        warn(f"go fragment {path} is not a deadcode report; skipped")
        return None
    try:
        unused = sum(
            1
            for package in data
            for func in package.get("Funcs") or []
            if not func["Generated"]
        )
    except (TypeError, AttributeError, KeyError):
        warn(f"go fragment {path} is not a deadcode report; skipped")
        return None
    return {"unused_symbols": unused}


def merge_dead_code(parts: list[dict[str, int] | None]) -> dict | None:
    """Sum per-kind counts across the *expected* parts; None if any is broken or none is expected.

    Each entry is the part of one detected language: a count dict, or None when that
    part is missing or unusable (no knip or Go fragment, crashed knip, broken
    cargo-machete or vulture). A partial total would look like an improvement to the
    ratchet, so one missing or broken part makes the whole figure null.
    """
    if not parts or any(p is None for p in parts):
        return None
    by_kind = {kind: sum(p.get(kind, 0) for p in parts) for kind in KINDS}
    return {"total": sum(by_kind.values()), "by_kind": by_kind}
