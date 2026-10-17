"""Where a run came from (repo, commit, branch, CI system) and which tools it used."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quality_report.tools import run_tool, warn

_VERSION_TIMEOUT = 10
_TOOL_VERSION_COMMANDS: dict[str, list[str]] = {
    "jscpd": ["jscpd", "--version"],
    "lizard": ["lizard", "--version"],
    "cargo-machete": ["cargo-machete", "--version"],
}


@dataclass
class RunContext:
    repo: str
    commit: str
    branch: str
    ci_system: str
    run: int | str | None


def _git(repo_path: Path, *args: str) -> str | None:
    result = run_tool(["git", *args], cwd=repo_path, timeout=30)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _ci_fields(env: Mapping[str, str]) -> dict[str, str | None]:
    """Repo/commit/branch/system/run as reported by the CI environment (None where unknown)."""
    if env.get("CI") == "woodpecker":
        return {
            "repo": env.get("CI_REPO_NAME"),
            "commit": env.get("CI_COMMIT_SHA"),
            "branch": env.get("CI_COMMIT_SOURCE_BRANCH") or env.get("CI_COMMIT_BRANCH"),
            "ci_system": "woodpecker",
            "run": env.get("CI_PIPELINE_NUMBER"),
        }
    if env.get("GITHUB_ACTIONS") == "true":
        return {
            "repo": (env.get("GITHUB_REPOSITORY") or "").rpartition("/")[2] or None,
            "commit": env.get("GITHUB_SHA"),
            "branch": env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME"),
            "ci_system": "github",
            "run": env.get("GITHUB_RUN_ID"),
        }
    return {}


def resolve_context(env: Mapping[str, str], repo_path: Path) -> RunContext:
    """QUALITY_* overrides, then Woodpecker, then GitHub, then local git for what is still missing."""
    ci = _ci_fields(env)
    override = {
        "repo": env.get("QUALITY_REPO"),
        "commit": env.get("QUALITY_COMMIT"),
        "branch": env.get("QUALITY_BRANCH"),
        "ci_system": env.get("QUALITY_CI_SYSTEM"),
        "run": env.get("QUALITY_RUN"),
    }
    fields = {key: override[key] or ci.get(key) for key in override}

    repo = fields["repo"] or repo_path.resolve().name
    commit = fields["commit"] or _git(repo_path, "rev-parse", "HEAD")
    branch = fields["branch"] or _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    if commit is None:
        warn("could not determine the commit; using 'unknown'")
    if branch is None:
        warn("could not determine the branch; using 'unknown'")
    return RunContext(
        repo=repo,
        commit=commit or "unknown",
        branch=branch or "unknown",
        ci_system=fields["ci_system"] or "local",
        run=fields["run"],
    )


def tool_versions() -> dict[str, str]:
    """Versions of the tools the quality step runs; tools that are missing or fail are omitted."""
    versions: dict[str, str] = {}
    for name, cmd in _TOOL_VERSION_COMMANDS.items():
        result = run_tool(cmd, timeout=_VERSION_TIMEOUT)
        if result is None or result.returncode != 0:
            continue
        lines = result.stdout.strip().splitlines()
        if lines:
            versions[name] = lines[-1].split()[-1]
    return versions
