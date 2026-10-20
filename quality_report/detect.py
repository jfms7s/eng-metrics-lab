from __future__ import annotations

import os
from fnmatch import fnmatch, fnmatchcase
from pathlib import Path, PurePosixPath

MAX_DEPTH = 3

_MANIFESTS: tuple[tuple[str, str], ...] = (
    ("package.json", "typescript"),
    ("tsconfig.json", "typescript"),
    ("Cargo.toml", "rust"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("requirements*.txt", "python"),
    ("go.mod", "go"),
)

LANGUAGES: tuple[str, ...] = tuple(sorted({language for _, language in _MANIFESTS}))
"""Every language name `detect_languages` can return."""

_EXTENSIONS: dict[str, str] = {
    **dict.fromkeys((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"), "typescript"),
    ".rs": "rust",
    ".py": "python",
    ".go": "go",
}


def language_of(path: str) -> str | None:
    """The language a source file belongs to, by extension; None if unknown."""
    return _EXTENSIONS.get(Path(path).suffix)


_TEST_FILE_NAMES: dict[str, tuple[str, ...]] = {
    "typescript": ("*.test.*", "*.spec.*"),
    "python": ("test_*.py", "*_test.py", "conftest.py"),
    "go": ("*_test.go",),
}
_TEST_DIRS: dict[str, frozenset[str]] = {
    "typescript": frozenset({"test", "tests", "__tests__", "__mocks__"}),
    "python": frozenset({"test", "tests"}),
    "go": frozenset({"testdata"}),
}


def is_test_file(path: str) -> bool:
    """True if a repo-relative source file is test code by its language's conventions.

    A file is test code if its name follows the language's test naming (`*.test.ts`,
    `test_*.py`, `*_test.go`, ...) or it lies under one of the language's test
    directories (`tests/`, `__tests__/`, Go's `testdata/`). Rust is never judged by
    path: its tests live inside source files, which `rusttests` scans.
    """
    language = language_of(path)
    rel = PurePosixPath(path)
    names = _TEST_FILE_NAMES.get(language, ())
    dirs = _TEST_DIRS.get(language, frozenset())
    return any(fnmatchcase(rel.name, n) for n in names) or not dirs.isdisjoint(
        rel.parts[:-1]
    )


def is_excluded(rel: Path, exclude: list[str]) -> bool:
    """True if a repo-relative path matches an exclude pattern.

    Each pattern is tried against the whole relative path (with and without a
    leading slash, so `**/x/**` also matches a top-level `x/`) and against
    every path component.
    """
    rel_posix = rel.as_posix()
    candidates = [rel_posix, "/" + rel_posix, *rel.parts]
    return any(fnmatch(c, pattern) for pattern in exclude for c in candidates)


def detect_languages(repo: Path, exclude: list[str]) -> list[str]:
    """Languages inferred from manifests found up to MAX_DEPTH levels deep."""
    found: set[str] = set()
    for root, dirs, files in os.walk(repo):
        root_path = Path(root)
        rel_root = root_path.relative_to(repo)
        depth = len(rel_root.parts)
        dirs[:] = [
            d
            for d in dirs
            if depth < MAX_DEPTH and not is_excluded(rel_root / d, exclude)
        ]
        for name in files:
            if is_excluded(rel_root / name, exclude):
                continue
            for pattern, language in _MANIFESTS:
                if fnmatch(name, pattern):
                    found.add(language)
    return sorted(found)
