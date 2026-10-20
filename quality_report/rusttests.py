"""Rust test code: the lines and files coverage must leave out (ruling R35).

cargo-llvm-cov instruments every compiled line, and Rust keeps unit tests beside
production code, so inline test modules inflate a Rust coverage total. This finds
test code by scanning the source:

- items under an outer `#[cfg(test)]` (or a `cfg` that needs `test`, such as
  `cfg(all(test, unix))`) or a test attribute (`#[test]`, `#[tokio::test]`, ...);
- the block of an inner `#![cfg(test)]`, or the whole file when it is at top level;
- the file of an out-of-line `mod x;` declared inside test code, and every module
  below it;
- integration tests: files under a crate's `tests/` directory.

Comments and string and char literals are blanked first, so brackets or `#[` inside
them never count; the blanked text keeps every offset and newline of the source.
"""

from __future__ import annotations

import os
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality_report.detect import is_excluded
from quality_report.tools import warn

_IDENT_CHAR = re.compile(r"\w")
_RAW_STRING = re.compile(r'[bc]?r(#*)"')
_CHAR = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}|.)|[^\\'\n])'")
_ATTR = re.compile(r"#\s*(!)?\s*\[")
_MOD_DECL = re.compile(r"\bmod\s+(?:r#)?([A-Za-z_]\w*)\s*;")
_INLINE_MOD = re.compile(r"\bmod\s+(?:r#)?([A-Za-z_]\w*)\s*$")
_PATH_ATTR = re.compile(r'^\s*path\s*=\s*"([^"]*)"\s*$')
_ATTR_NAME = re.compile(r"\s*([A-Za-z_][\w:]*)")
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {v: k for k, v in _OPENERS.items()}
_ROOT_FILES = {"lib.rs", "main.rs", "mod.rs", "build.rs"}
_ROOT_DIRS = {"bin", "tests", "examples", "benches"}


@dataclass
class RustTestCode:
    files: set[str] = field(default_factory=set)
    """Repo-relative files that are test code from top to bottom."""
    ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    """Inclusive 1-based line ranges of test code inside the other files."""

    def is_test_line(self, path: str, line: int) -> bool:
        return path in self.files or any(
            start <= line <= end for start, end in self.ranges.get(path, ())
        )


def mask(src: str) -> str:
    """`src` with comments and string, byte-string and char literals blanked to spaces.

    Newlines are kept, so offsets and line numbers match the source. Block comments
    nest, as in Rust; a lifetime (`'a`) is not a char literal and stays.
    """
    out = list(src)
    n = len(src)

    def blank(start: int, end: int) -> None:
        for k in range(start, end):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            end = src.find("\n", i)
            end = n if end < 0 else end
        elif src.startswith("/*", i):
            depth, end = 1, i + 2
            while end < n and depth:
                if src.startswith("/*", end):
                    depth, end = depth + 1, end + 2
                elif src.startswith("*/", end):
                    depth, end = depth - 1, end + 2
                else:
                    end += 1
        elif c in "bcr" and (raw := _raw_string_at(src, i)) is not None:
            closing = '"' + raw.group(1)
            end = src.find(closing, raw.end())
            end = n if end < 0 else end + len(closing)
        elif c == '"':
            end = i + 1
            while end < n and src[end] != '"':
                end += 2 if src[end] == "\\" else 1
            end = min(end + 1, n)
        elif c == "'" and (char := _CHAR.match(src, i)) is not None:
            end = char.end()
        else:
            i += 1
            continue
        blank(i, end)
        i = end
    return "".join(out)


def _raw_string_at(src: str, i: int) -> re.Match[str] | None:
    """A raw string literal's opening (`r#"`, `br"`, ...) at `i`, not inside an identifier."""
    if i > 0 and _IDENT_CHAR.match(src[i - 1]):
        return None
    return _RAW_STRING.match(src, i)


def _bracket_pairs(masked: str) -> dict[int, int]:
    """Offset of each opening bracket to its closing one (the end of text if unclosed)."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for i, c in enumerate(masked):
        if c in _OPENERS:
            stack.append(i)
        elif c in _CLOSERS and stack and masked[stack[-1]] == _CLOSERS[c]:
            pairs[stack.pop()] = i
    for i in stack:
        pairs[i] = len(masked) - 1
    return pairs


def _skip_space(masked: str, i: int) -> int:
    while i < len(masked) and masked[i].isspace():
        i += 1
    return i


def _cfg_needs_test(predicate: str) -> bool:
    """True if a `cfg(...)` predicate can only hold when `test` is set.

    `test` does; `all(...)` does if any part does; `any(...)` only if every part does;
    `not(...)` and every other option never do.
    """
    predicate = predicate.strip()
    if predicate == "test":
        return True
    match = re.fullmatch(r"(all|any)\s*\((.*)\)", predicate, re.DOTALL)
    if match is None:
        return False
    parts = [p for p in _split_top_level(match[2]) if p.strip()]
    if not parts:
        return False
    if match[1] == "all":
        return any(_cfg_needs_test(p) for p in parts)
    return all(_cfg_needs_test(p) for p in parts)


def _split_top_level(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def is_test_attribute(content: str) -> bool:
    """True if an attribute's content (between `#[` and `]`) marks test-only code."""
    name = _ATTR_NAME.match(content)
    if name is None:
        return False
    path = name[1]
    if path == "test" or path.endswith("::test"):
        return True
    if path != "cfg":
        return False
    rest = content[name.end() :].strip()
    if not (rest.startswith("(") and rest.endswith(")")):
        return False
    return _cfg_needs_test(rest[1:-1])


@dataclass
class _Attribute:
    start: int
    end: int
    """Offset of the closing `]`."""
    inner: bool
    content: str
    """Blanked content: literals are spaces."""
    source: str
    """Original content, literals included."""


def _attributes(src: str, masked: str, pairs: dict[int, int]) -> list[_Attribute]:
    found = []
    for match in _ATTR.finditer(masked):
        open_bracket = match.end() - 1
        close = pairs.get(open_bracket)
        if close is None:
            continue
        found.append(
            _Attribute(
                start=match.start(),
                end=close,
                inner=match[1] is not None,
                content=masked[open_bracket + 1 : close],
                source=src[open_bracket + 1 : close],
            )
        )
    return found


def _item_end(masked: str, start: int, pairs: dict[int, int]) -> int:
    """Offset of the last character of the item or statement starting at `start`.

    It ends at a `;` or `,` outside brackets, after its first top-level `{...}` block
    (and a `;` right after it), or just before the bracket that closes its parent.
    """
    i, n = start, len(masked)
    while i < n:
        c = masked[i]
        if c in _OPENERS:
            close = pairs[i]
            if c == "{":
                after = _skip_space(masked, close + 1)
                return after if after < n and masked[after] == ";" else close
            i = close + 1
        elif c in ";,":
            return i
        elif c in _CLOSERS:
            return max(start, i - 1)
        else:
            i += 1
    return n - 1


def _enclosing_braces(offset: int, masked: str, pairs: dict[int, int]) -> list[int]:
    """Opening `{` offsets that contain `offset`, outermost first."""
    return sorted(
        open_
        for open_, close in pairs.items()
        if masked[open_] == "{" and open_ < offset <= close
    )


@dataclass
class _ModDecl:
    name: str
    line: int
    path_attr: str | None
    """The `#[path = "..."]` value, if any."""
    inline_parents: list[str]
    """Names of the inline `mod x { ... }` blocks it sits in, outermost first."""


@dataclass
class _FileScan:
    whole_file: bool
    ranges: list[tuple[int, int]]
    mod_decls: list[_ModDecl]


class _Lines:
    def __init__(self, src: str) -> None:
        self._starts = [0] + [i + 1 for i, c in enumerate(src) if c == "\n"]

    def of(self, offset: int) -> int:
        """The 1-based line of a character offset."""
        return bisect_right(self._starts, offset)


def _group_attributes(
    attributes: list[_Attribute], masked: str
) -> dict[int, list[_Attribute]]:
    """Outer attributes grouped by the offset of the item they annotate."""
    groups: dict[int, list[_Attribute]] = {}
    pending: list[_Attribute] = []
    for attr in attributes:
        if attr.inner:
            continue
        pending.append(attr)
        after = _skip_space(masked, attr.end + 1)
        following = _ATTR.match(masked, after)
        if following is not None and following[1] is None:
            continue  # another outer attribute of the same item follows
        groups[after] = pending
        pending = []
    return groups


def _inline_parents(offset: int, masked: str, pairs: dict[int, int]) -> list[str]:
    parents = []
    for open_ in _enclosing_braces(offset, masked, pairs):
        inline = _INLINE_MOD.search(masked[max(0, open_ - 200) : open_])
        if inline is not None:
            parents.append(inline[1])
    return parents


def scan_source(src: str) -> _FileScan:
    """Test line ranges and out-of-line module declarations of one Rust source file."""
    masked = mask(src)
    pairs = _bracket_pairs(masked)
    lines = _Lines(src)
    attributes = _attributes(src, masked, pairs)

    whole_file = False
    ranges: list[tuple[int, int]] = []
    for attr in attributes:
        if attr.inner and is_test_attribute(attr.content):
            braces = _enclosing_braces(attr.start, masked, pairs)
            if not braces:
                whole_file = True
            else:
                ranges.append((lines.of(braces[-1]), lines.of(pairs[braces[-1]])))

    groups = _group_attributes(attributes, masked)
    for item, attrs in groups.items():
        if any(is_test_attribute(a.content) for a in attrs):
            end = _item_end(masked, item, pairs)
            ranges.append((lines.of(attrs[0].start), lines.of(end)))

    decls = []
    for match in _MOD_DECL.finditer(masked):
        attrs = groups.get(match.start(), [])
        path_attr = next(
            (m[1] for a in attrs if (m := _PATH_ATTR.match(a.source))), None
        )
        decls.append(
            _ModDecl(
                name=match[1],
                line=lines.of(match.start()),
                path_attr=path_attr,
                inline_parents=_inline_parents(match.start(), masked, pairs),
            )
        )
    return _FileScan(whole_file, ranges, decls)


def _rust_files(repo: Path, exclude: list[str]) -> list[str]:
    found = []
    for root, dirs, names in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = sorted(d for d in dirs if not is_excluded(rel_root / d, exclude))
        for name in names:
            rel = rel_root / name
            if name.endswith(".rs") and not is_excluded(rel, exclude):
                found.append(rel.as_posix())
    return sorted(found)


def _crate_roots(repo: Path, exclude: list[str]) -> list[PurePosixPath]:
    """Repo-relative directories holding a Cargo.toml, deepest first."""
    roots = []
    for root, dirs, names in os.walk(repo):
        rel_root = Path(root).relative_to(repo)
        dirs[:] = [d for d in dirs if not is_excluded(rel_root / d, exclude)]
        if "Cargo.toml" in names:
            roots.append(PurePosixPath(rel_root.as_posix()))
    return sorted(roots, key=lambda p: len(p.parts), reverse=True)


def _is_integration_test(rel: str, crates: list[PurePosixPath]) -> bool:
    path = PurePosixPath(rel)
    for crate in crates:
        if crate == PurePosixPath(".") or crate in path.parents:
            inside = path.relative_to(crate) if crate != PurePosixPath(".") else path
            return inside.parts[0] == "tests" and len(inside.parts) > 1
    return False


def _module_dir(rel: str) -> PurePosixPath:
    """The directory a file's `mod x;` declarations resolve in."""
    path = PurePosixPath(rel)
    if path.name in _ROOT_FILES or path.parent.name in _ROOT_DIRS:
        return path.parent
    return path.parent / path.stem


def _targets(rel: str, decl: _ModDecl, files: set[str]) -> list[str]:
    """The existing file(s) a `mod x;` declaration names.

    A `#[path]` outside inline modules is relative to the declaring file's directory;
    otherwise the module directory (`foo/` for `foo.rs`, the directory itself for
    `mod.rs` and crate roots) plus the inline module names is the base.
    """
    if decl.path_attr is not None and not decl.inline_parents:
        candidates = [PurePosixPath(rel).parent / decl.path_attr]
    else:
        base = _module_dir(rel).joinpath(*decl.inline_parents)
        if decl.path_attr is not None:
            candidates = [base / decl.path_attr]
        else:
            candidates = [base / f"{decl.name}.rs", base / decl.name / "mod.rs"]
    normalised = (PurePosixPath(os.path.normpath(c)).as_posix() for c in candidates)
    return [path for path in normalised if path in files]


def find_rust_test_code(repo: Path, exclude: list[str]) -> RustTestCode:
    """Test files and test line ranges of every non-excluded Rust file in `repo`."""
    rel_files = _rust_files(repo, exclude)
    file_set = set(rel_files)
    crates = _crate_roots(repo, exclude)
    scans: dict[str, _FileScan] = {}
    for rel in rel_files:
        try:
            scans[rel] = scan_source((repo / rel).read_text(errors="replace"))
        except OSError as exc:
            warn(f"{rel} is unreadable ({type(exc).__name__}); its test code counts")

    test_files = {
        rel
        for rel, scan in scans.items()
        if scan.whole_file or _is_integration_test(rel, crates)
    }
    for rel, scan in scans.items():
        for decl in scan.mod_decls:
            if any(start <= decl.line <= end for start, end in scan.ranges):
                test_files.update(_targets(rel, decl, file_set))
    # Every module below a test module is test code too.
    queue = list(test_files)
    while queue:
        rel = queue.pop()
        for decl in scans[rel].mod_decls if rel in scans else ():
            for target in _targets(rel, decl, file_set):
                if target not in test_files:
                    test_files.add(target)
                    queue.append(target)

    ranges = {
        rel: scan.ranges
        for rel, scan in scans.items()
        if scan.ranges and rel not in test_files
    }
    return RustTestCode(files=test_files, ranges=ranges)
