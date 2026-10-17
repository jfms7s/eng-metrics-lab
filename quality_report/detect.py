from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path

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
