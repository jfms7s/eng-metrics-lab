"""Line and branch coverage from the test step's fragments in `.quality/` (spec section 4).

Each language's fragments are parsed, their paths normalised to repo-relative files,
and merged by max hits. Coverage is None (with a warning) unless every expected
language has a usable fragment with at least one instrumented line naming a repo file.
"""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality_report.detect import is_excluded
from quality_report.tools import warn

FRAGMENT_DIR = ".quality"
HOST_WORKSPACE_VARS = ("QUALITY_HOST_WORKSPACE", "CI_WORKSPACE")


@dataclass
class FileCoverage:
    lines: dict[int, int] = field(default_factory=dict)
    """Hit count per instrumented line."""
    branches: dict[str, int] = field(default_factory=dict)
    """Hit count per branch, keyed `line,block,branch`."""

    def merge(self, other: FileCoverage) -> None:
        """Fold `other` in, keeping the max hits per line and per branch."""
        _merge_max(self.lines, other.lines)
        _merge_max(self.branches, other.branches)


@dataclass
class CoverageData:
    files: dict[str, FileCoverage]
    languages: list[str]
    untested_languages: list[str]
    branch_languages: list[str]
    unmatched_files: int


def _merge_max(into: dict, hits: Mapping) -> None:
    for key, count in hits.items():
        into[key] = max(into.get(key, count), count)


def _merge_file(files: dict[str, FileCoverage], path: str, cov: FileCoverage) -> None:
    files.setdefault(path, FileCoverage()).merge(cov)


def _hits(value: str) -> int:
    """An lcov hit count; `-` (never evaluated) is 0. Raises ValueError if malformed."""
    return 0 if value == "-" else int(value)


def parse_lcov(text: str) -> dict[str, FileCoverage]:
    """Files to coverage from `SF`, `DA` and `BRDA` records; repeated files merge by max.

    Summary and function records (`LF`, `LH`, `BRF`, `FN`, ...) are ignored: totals are
    recomputed from the records. Raises ValueError on a malformed record.
    """
    files: dict[str, FileCoverage] = {}
    current: FileCoverage | None = None
    source = ""
    for number, line in enumerate(text.splitlines(), start=1):
        tag, _, value = line.strip().partition(":")
        if tag == "SF":
            if current is not None:
                raise ValueError(f"lcov line {number}: SF before end_of_record")
            source, current = value, FileCoverage()
        elif tag == "end_of_record":
            if current is not None:
                _merge_file(files, source, current)
            current = None
        elif tag in ("DA", "BRDA"):
            if current is None:
                raise ValueError(f"lcov line {number}: {tag} outside an SF record")
            try:
                if tag == "DA":
                    fields = value.split(",")
                    current.lines[int(fields[0])] = max(
                        current.lines.get(int(fields[0]), 0), int(fields[1])
                    )
                else:
                    # line,block,branch,taken; lcov 2.x may name the branch with an
                    # expression that contains commas, so only the last field splits.
                    key, _, taken = value.rpartition(",")
                    if key.count(",") < 2:
                        raise ValueError("expected line,block,branch,taken")
                    current.branches[key] = max(
                        current.branches.get(key, 0), _hits(taken)
                    )
            except (IndexError, ValueError) as exc:
                raise ValueError(
                    f"lcov line {number}: bad {tag} record ({exc})"
                ) from exc
    if current is not None:
        raise ValueError("lcov ends inside an SF record (no end_of_record)")
    return files


_GO_BLOCK = re.compile(
    r"^(?P<file>.+):(?P<start>\d+)\.\d+,(?P<end>\d+)\.\d+ (?P<stmts>\d+) (?P<count>\d+)$"
)


def parse_go_coverprofile(text: str) -> dict[str, FileCoverage]:
    """Files to line coverage from `go test -coverprofile` blocks; no branch data.

    A line's hits are the max over the blocks covering it, so blocks repeated by
    `-coverpkg` merge correctly. Blocks with no statements are skipped. Raises
    ValueError on a malformed block.
    """
    files: dict[str, FileCoverage] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("mode:"):
            continue
        match = _GO_BLOCK.match(line)
        if match is None:
            raise ValueError(f"coverprofile line {number}: bad block {line!r}")
        if int(match["stmts"]) == 0:
            continue
        count = int(match["count"])
        block = {n: count for n in range(int(match["start"]), int(match["end"]) + 1)}
        _merge_file(files, match["file"], FileCoverage(lines=block))
    return files


_GO_MODULE = re.compile(r"^module\s+\"?([^\s\"]+)\"?")


def _go_ignores(name: str) -> bool:
    """Directories the go tool skips for `./...` (and that hold test fixtures)."""
    return name == "testdata" or name.startswith((".", "_"))


def _module_path(go_mod: Path) -> str | None:
    for line in go_mod.read_text(errors="replace").splitlines():
        match = _GO_MODULE.match(line.strip())
        if match:
            return match[1]
    return None


def go_module_roots(repo: Path, exclude: list[str]) -> dict[str, str]:
    """Module path to repo-relative directory (`""` for the root) for every go.mod.

    A module path declared by more than one go.mod is ambiguous: it is left out (with
    a warning), so its files come out unmatched instead of mapped to a guess.
    """
    found: dict[str, list[str]] = {}
    for root, dirs, names in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = sorted(
            d
            for d in dirs
            if not _go_ignores(d) and not is_excluded(rel_root / d, exclude)
        )
        if "go.mod" not in names or is_excluded(rel_root / "go.mod", exclude):
            continue
        module = _module_path(Path(root) / "go.mod")
        if module is not None:
            directory = "" if rel_root == Path() else rel_root.as_posix()
            found.setdefault(module, []).append(directory)
    roots: dict[str, str] = {}
    for module, directories in found.items():
        if len(directories) > 1:
            listed = ", ".join(d or "." for d in directories)
            warn(
                f"Go module {module} is declared in several go.mod files ({listed}); "
                "its coverage paths stay unmatched"
            )
            continue
        roots[module] = directories[0]
    return roots


def _inside(rel: str) -> str | None:
    """`rel` with `.`/`..` collapsed, or None if it is empty or leaves the root."""
    clean = posixpath.normpath(rel)
    if clean in (".", "..") or clean.startswith(("../", "/")):
        return None
    return clean


def _under(path: str, root: str) -> str | None:
    """`path` relative to `root` (whole components only), or None if not under it."""
    try:
        rel = PurePosixPath(posixpath.normpath(path)).relative_to(
            posixpath.normpath(root)
        )
    except ValueError:
        return None
    return _inside(rel.as_posix())


def _go_module(rel: str, go_modules: Mapping[str, str]) -> str | None:
    """The module `rel` is an import path under (the longest match), or None."""
    for module in sorted(go_modules, key=len, reverse=True):
        if rel.startswith(module + "/"):
            return module
    return None


def _candidates(
    raw: str, repo: Path, prefixes: list[str], go_modules: Mapping[str, str]
) -> Iterator[str]:
    """Repo-relative readings of a coverage path, in the spec's normalisation order."""
    if raw.startswith("/"):
        for root in (str(repo.absolute()), str(repo.resolve()), *prefixes):
            rel = _under(raw, root) if root else None
            if rel is not None:
                yield rel
        return
    rel = _inside(raw)
    if rel is None:
        return
    module = _go_module(rel, go_modules)
    if module is None:
        yield rel
        return
    # An import path is only ever read through its module: its literal reading may
    # name an unrelated repo file (module `app` in server/, a repo dir app/).
    rest = rel[len(module) + 1 :]
    mapped = _inside(posixpath.join(go_modules[module], rest))
    if mapped is not None:
        yield mapped


def normalize_path(
    raw: str, repo: Path, prefixes: list[str], go_modules: dict[str, str]
) -> str | None:
    """The repo-relative POSIX path a coverage path names, or None if no repo file matches.

    Tried in order: relative to the repo root (`./` stripped); absolute under the repo
    root, then under each of `prefixes`. A relative path under a module in `go_modules`
    is a Go import path and is read only as that module's directory plus the rest.
    Only a path naming an existing file inside the repo matches.
    """
    for rel in _candidates(raw, repo, prefixes, go_modules):
        if (repo / rel).is_file():
            return rel
    return None


def _excluded_elsewhere(
    raw: str,
    language: str,
    repo: Path,
    prefixes: list[str],
    go_modules: Mapping[str, str],
    exclude: list[str],
) -> bool:
    """True if an unmatched path would be excluded had its file been present here.

    Build output and dependencies (`dist/`, `node_modules`) are often absent where the
    report runs; they are excluded, not unmatched. Go paths are import paths, so a
    Go path under no known module is not a repo path to judge: it stays unmatched.
    """
    if language == "go" and _go_module(raw, go_modules) is None:
        return False
    readings = _candidates(raw, repo, prefixes, go_modules)
    return any(is_excluded(Path(rel), exclude) for rel in readings)


Parser = Callable[[str], dict[str, FileCoverage]]

FRAGMENTS: dict[str, tuple[str, Parser]] = {
    "typescript": ("coverage-ts*.lcov", parse_lcov),
    "rust": ("coverage-rust*.lcov", parse_lcov),
    "python": ("coverage-py*.lcov", parse_lcov),
    "go": ("coverage-go*.out", parse_go_coverprofile),
}


def _read_language(repo: Path, language: str) -> dict[str, FileCoverage] | None:
    """The merged raw-path coverage of one language's fragments; None (with a warning)
    if it has no fragment, an unreadable or malformed one, or no file records at all."""
    pattern, parse = FRAGMENTS[language]
    paths = sorted((repo / FRAGMENT_DIR).glob(pattern))
    if not paths:
        warn(
            f"{language} expected but {FRAGMENT_DIR}/{pattern} is missing; coverage null"
        )
        return None
    merged: dict[str, FileCoverage] = {}
    for path in paths:
        try:
            parsed = parse(path.read_text())
        except (OSError, ValueError) as exc:
            warn(f"{FRAGMENT_DIR}/{path.name} is unreadable ({exc}); coverage null")
            return None
        for raw, cov in parsed.items():
            _merge_file(merged, raw, cov)
    if not merged:
        warn(f"{language} coverage fragments hold no file records; coverage null")
        return None
    return merged


def load_coverage(
    repo: Path,
    languages: list[str],
    expected: list[str] | None,
    exclude: list[str],
    env: Mapping[str, str],
) -> CoverageData | None:
    """Coverage over the expected languages, keyed by repo-relative path.

    Expected languages are `languages` (the detected ones) narrowed by `expected`
    (`.quality.yml` `coverage_languages`) when set; the rest are `untested_languages`.
    Excluded paths are dropped silently; paths naming no repo file are counted as
    unmatched. Returns None (with a warning) when an expected language has no usable
    fragment, when nothing is expected, or when no instrumented line of some expected
    language matched a repo file.
    """
    covered = [lang for lang in languages if expected is None or lang in expected]
    untested = [lang for lang in languages if lang not in covered]
    if not covered:
        warn("no language is expected to have coverage; coverage null")
        return None

    per_language = {lang: _read_language(repo, lang) for lang in covered}
    if any(data is None for data in per_language.values()):
        return None

    prefixes = [str(repo.resolve())] + [env.get(var, "") for var in HOST_WORKSPACE_VARS]
    prefixes = [p for p in prefixes if p]
    go_modules = go_module_roots(repo, exclude) if "go" in covered else {}

    files: dict[str, FileCoverage] = {}
    unmatched: set[str] = set()
    silent: list[str] = []
    for language, data in per_language.items():
        modules = go_modules if language == "go" else {}
        matched_lines = False
        for raw, cov in data.items():
            rel = normalize_path(raw, repo, prefixes, modules)
            if rel is not None:
                if not is_excluded(Path(rel), exclude):
                    _merge_file(files, rel, cov)
                    matched_lines = matched_lines or bool(cov.lines)
            elif not _excluded_elsewhere(
                raw, language, repo, prefixes, modules, exclude
            ):
                unmatched.add(raw)
        if not matched_lines:
            silent.append(language)

    if unmatched:
        warn(
            f"{len(unmatched)} coverage file(s) name no file in the repo; "
            "counted in coverage.unmatched_files"
        )
    if silent:
        # A language whose fragment matched nothing would leave a total over the
        # other languages while still listing it: a plausible wrong number.
        for language in silent:
            warn(
                f"{language} coverage matched no file in the repo "
                "(no instrumented line); coverage null"
            )
        return None
    return CoverageData(
        files=files,
        languages=covered,
        untested_languages=untested,
        branch_languages=[
            lang
            for lang, data in per_language.items()
            if any(cov.branches for cov in data.values())
        ],
        unmatched_files=len(unmatched),
    )


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 2) if whole else None


def summarize_coverage(data: CoverageData | None) -> dict | None:
    """The report's `coverage` block; branches are over `branch_languages` (None if none)."""
    if data is None:
        return None
    lines = [hits for cov in data.files.values() for hits in cov.lines.values()]
    branches = [hits for cov in data.files.values() for hits in cov.branches.values()]
    lines_covered = sum(1 for hits in lines if hits > 0)
    branches_percent = (
        _percent(sum(1 for hits in branches if hits > 0), len(branches))
        if data.branch_languages
        else None
    )
    return {
        "lines_percent": _percent(lines_covered, len(lines)),
        "lines_covered": lines_covered,
        "lines_total": len(lines),
        "branches_percent": branches_percent,
        "branch_languages": data.branch_languages,
        "languages": data.languages,
        "untested_languages": data.untested_languages,
        "unmatched_files": data.unmatched_files,
    }
