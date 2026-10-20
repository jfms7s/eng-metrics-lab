from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from quality_report.detect import LANGUAGES

DEFAULT_EXCLUDES: tuple[str, ...] = (
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    ".venv",
    "coverage",
    "*.d.ts",
    "*.min.js",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "go.sum",
    "**/migrations/**",
    "**/__snapshots__/**",
    # Generated protobuf/gRPC code, by the generators' own file names (ruling R35).
    "*.pb.go",
    "*.pb.gw.go",
    "*_pb.ts",
    "*_pb.js",
    "*_pb2.py",
    "*_pb2_grpc.py",
    ".quality",
)

CONFIG_FILENAME = ".quality.yml"


@dataclass
class Config:
    exclude: list[str]
    complexity_threshold: int = 10
    cognitive_threshold: int = 15
    duplication_min_tokens: int = 50
    duplication_formats: list[str] | None = None
    coverage_languages: list[str] | None = None
    languages: list[str] | None = None
    """Languages declared in `.quality.yml`, added to the ones manifests reveal."""


def load_config(repo: Path, path: Path | None = None) -> Config:
    """Load `<repo>/.quality.yml` (or `path`); repo excludes are added to the defaults."""
    config_path = path if path is not None else repo / CONFIG_FILENAME
    raw: dict = {}
    if config_path.is_file():
        loaded = yaml.safe_load(config_path.read_text())
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError(f"{config_path}: expected a mapping at top level")
            raw = loaded

    known = {
        "exclude",
        "complexity_threshold",
        "cognitive_threshold",
        "duplication_min_tokens",
        "duplication_formats",
        "coverage_languages",
        "languages",
    }
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{config_path}: unknown key(s): {', '.join(unknown)}")

    extra = raw.get("exclude") or []
    if not isinstance(extra, list):
        raise TypeError(f"{config_path}: 'exclude' must be a list")
    exclude = list(DEFAULT_EXCLUDES) + [e for e in extra if e not in DEFAULT_EXCLUDES]

    formats = raw.get("duplication_formats")
    if formats is not None and (
        not isinstance(formats, list) or not all(isinstance(f, str) for f in formats)
    ):
        raise TypeError(
            f"{config_path}: 'duplication_formats' must be a list of strings"
        )

    coverage_languages = _language_list(raw, "coverage_languages", config_path)
    languages = _language_list(raw, "languages", config_path)

    defaults = Config(exclude=exclude)
    return Config(
        exclude=exclude,
        complexity_threshold=int(
            raw.get("complexity_threshold", defaults.complexity_threshold)
        ),
        cognitive_threshold=int(
            raw.get("cognitive_threshold", defaults.cognitive_threshold)
        ),
        duplication_min_tokens=int(
            raw.get("duplication_min_tokens", defaults.duplication_min_tokens)
        ),
        duplication_formats=formats,
        coverage_languages=coverage_languages,
        languages=languages,
    )


def _language_list(raw: dict, key: str, config_path: Path) -> list[str] | None:
    """`raw[key]` as a list of known language names; None if the key is absent."""
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TypeError(f"{config_path}: '{key}' must be a list of strings")
    bad = sorted(set(value) - set(LANGUAGES))
    if bad:
        raise ValueError(
            f"{config_path}: unknown {key}: {', '.join(bad)} "
            f"(known: {', '.join(LANGUAGES)})"
        )
    return value
