from pathlib import Path

from quality_report.ci import RunContext, resolve_context, tool_versions


def test_resolve_context_github_pull_request_uses_head():
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "jfms7s/replica-sync",
           "GITHUB_SHA": "merge123", "GITHUB_REF_NAME": "12/merge", "GITHUB_HEAD_REF": "feat/x",
           "GITHUB_RUN_ID": "99", "QUALITY_COMMIT": "head456"}
    ctx = resolve_context(env, Path("."))
    assert (ctx.repo, ctx.commit, ctx.branch, ctx.ci_system) == ("replica-sync", "head456", "feat/x", "github")
    assert ctx.run == "99"


def test_resolve_context_woodpecker():
    env = {"CI": "woodpecker", "CI_REPO_NAME": "firstdime", "CI_COMMIT_SHA": "abc",
           "CI_COMMIT_BRANCH": "main", "CI_PIPELINE_NUMBER": "1234"}
    assert resolve_context(env, Path(".")) == RunContext("firstdime", "abc", "main", "woodpecker", "1234")


def test_resolve_context_woodpecker_pull_request_prefers_source_branch():
    env = {"CI": "woodpecker", "CI_REPO_NAME": "r", "CI_COMMIT_SHA": "abc",
           "CI_COMMIT_BRANCH": "main", "CI_COMMIT_SOURCE_BRANCH": "feat/y", "CI_PIPELINE_NUMBER": "5"}
    assert resolve_context(env, Path(".")).branch == "feat/y"


def test_resolve_context_quality_overrides_win_without_calling_git(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")  # git unavailable: must not be needed
    env = {"QUALITY_REPO": "r", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b",
           "CI": "woodpecker", "CI_REPO_NAME": "ignored", "CI_COMMIT_SHA": "ignored"}
    assert resolve_context(env, tmp_path) == RunContext("r", "c", "b", "woodpecker", None)
    env = {"QUALITY_REPO": "r", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b"}
    assert resolve_context(env, tmp_path) == RunContext("r", "c", "b", "local", None)


def test_resolve_context_falls_back_to_local_git(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    ctx = resolve_context({}, tmp_path)
    assert (ctx.repo, ctx.commit, ctx.branch, ctx.ci_system, ctx.run) == (tmp_path.name, sha, "trunk", "local", None)


def test_resolve_context_without_git_is_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    ctx = resolve_context({}, tmp_path)
    assert (ctx.commit, ctx.branch) == ("unknown", "unknown")
    assert "warning" in capsys.readouterr().err


def test_tool_versions_with_empty_path_is_empty(monkeypatch):
    monkeypatch.setenv("PATH", "")
    assert tool_versions() == {}
