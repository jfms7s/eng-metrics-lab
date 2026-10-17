from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

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
    ".quality",
)

CONFIG_FILENAME = ".quality.yml"


@dataclass
class Config:
    exclude: list[str]
    complexity_threshold: int = 10
    duplication_min_tokens: int = 50


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

    known = {"exclude", "complexity_threshold", "duplication_min_tokens"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{config_path}: unknown key(s): {', '.join(unknown)}")

    extra = raw.get("exclude") or []
    if not isinstance(extra, list):
        raise TypeError(f"{config_path}: 'exclude' must be a list")
    exclude = list(DEFAULT_EXCLUDES) + [e for e in extra if e not in DEFAULT_EXCLUDES]

    defaults = Config(exclude=exclude)
    return Config(
        exclude=exclude,
        complexity_threshold=int(raw.get("complexity_threshold", defaults.complexity_threshold)),
        duplication_min_tokens=int(raw.get("duplication_min_tokens", defaults.duplication_min_tokens)),
    )
