import io
import json
import threading
import zipfile
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests
import responses

import collector.github as cgithub
import collector.main as cmain
from collector.github import (
    CollectorError,
    download_report,
    list_report_artifacts,
    new_session,
)
from collector.main import collect, main

NOW = datetime.now(UTC)
OWNER, REPO = "jfms7s", "replica-sync"
LIST_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/artifacts"
TOKEN = "ghp_supersecret_token_value"


def zip_bytes(
    report: dict | None = None, name: str = "report.json", raw: bytes | None = None
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, raw if raw is not None else json.dumps(report))
    return buf.getvalue()


def artifact(
    id_,
    age_days=0.0,
    expired=False,
    branch="main",
    sha=None,
    repo_id=None,
    head_repo_id=None,
):
    run = {"head_branch": branch, "head_sha": sha or f"c{id_}"}
    if repo_id is not None:
        run["repository_id"] = repo_id
    if head_repo_id is not None:
        run["head_repository_id"] = head_repo_id
    return {
        "id": id_,
        "created_at": (NOW - timedelta(days=age_days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expired": expired,
        "workflow_run": run,
    }


def session():
    return new_session(TOKEN)


def since():
    return NOW - timedelta(days=3)


def add_zip(id_, body):
    responses.add(responses.GET, f"{LIST_URL}/{id_}/zip", body=body, status=200)


@pytest.fixture
def report(sample):
    def make(commit, repo=REPO):
        return {**sample, "commit": commit, "repo": repo}

    return make


@pytest.fixture
def stored(monkeypatch):
    """Fake insert_report backed by a set keyed on (repo, commit, branch)."""
    keys: set[tuple] = set()

    def fake_insert(conn, rep):
        key = (rep["repo"], rep["commit"], rep["branch"])
        if key in keys:
            return False
        keys.add(key)
        return True

    monkeypatch.setattr(cmain, "insert_report", fake_insert)
    return keys


@pytest.fixture
def gh_mock(report):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as r:
        r.add(
            responses.GET,
            LIST_URL,
            json={
                "artifacts": [
                    artifact(3, 0.5),
                    artifact(2, 1, expired=True),
                    artifact(1, 10),
                ]
            },
        )
        r.add(responses.GET, f"{LIST_URL}/3/zip", body=zip_bytes(report("c3")))
        yield r


@responses.activate
def test_lists_only_recent_unexpired():
    responses.add(
        responses.GET,
        LIST_URL,
        json={
            "artifacts": [
                artifact(3, 0.5, branch="main", sha="deadbeef"),
                artifact(2, 1, expired=True),
                artifact(1, 10),
            ]
        },
    )
    arts = list_report_artifacts(session(), OWNER, REPO, since=since())
    assert [a.id for a in arts] == [3]
    assert (arts[0].head_branch, arts[0].head_sha) == ("main", "deadbeef")
    assert arts[0].created_at.tzinfo is not None
    req = responses.calls[0].request
    assert "name=quality-report" in req.url and "per_page=100" in req.url
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"


@responses.activate
def test_list_paginates_until_short_page():
    first = [artifact(i, 0.1) for i in range(100, 200)]
    responses.add(responses.GET, LIST_URL, json={"artifacts": first})
    responses.add(responses.GET, LIST_URL, json={"artifacts": [artifact(7, 0.2)]})
    arts = list_report_artifacts(session(), OWNER, REPO, since=since())
    assert len(arts) == 101
    assert "page=2" in responses.calls[1].request.url


@responses.activate
def test_list_stops_when_page_entirely_older():
    responses.add(
        responses.GET,
        LIST_URL,
        json={"artifacts": [artifact(i, 20) for i in range(100)]},
    )
    assert list_report_artifacts(session(), OWNER, REPO, since=since()) == []
    assert len(responses.calls) == 1


def test_download_extracts_report_json(gh_mock):
    assert download_report(session(), OWNER, REPO, 3)["schema"] == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not a zip",
        zip_bytes(name="other.json", report={}),
        zip_bytes(raw=b"{broken"),
        zip_bytes(raw=b"[1, 2]"),
    ],
)
@responses.activate
def test_download_bad_artifact_raises_value_error(body):
    add_zip(9, body)
    with pytest.raises(ValueError):
        download_report(session(), OWNER, REPO, 9)


def test_collect_overlapping_window_counts_skipped(gh_mock, stored):
    first = collect(None, session(), OWNER, [REPO], since())
    second = collect(None, session(), OWNER, [REPO], since())
    assert (first.inserted, second.inserted, second.skipped) == (1, 0, 1)


def test_collect_prints_summary_line(gh_mock, stored, capsys):
    collect(None, session(), OWNER, [REPO], since())
    assert (
        capsys.readouterr().out.strip()
        == f"{REPO} inserted=1 skipped=0 rejected=0 failed=0"
    )


@responses.activate
def test_collect_continues_after_bad_zip(stored, report):
    responses.add(
        responses.GET,
        LIST_URL,
        json={"artifacts": [artifact(5, 0.1), artifact(4, 0.2)]},
    )
    add_zip(5, b"corrupt")
    add_zip(4, zip_bytes(report("c4")))
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.failed) == (1, 1)


@responses.activate
def test_collect_counts_download_http_error_as_failed(stored):
    responses.add(responses.GET, LIST_URL, json={"artifacts": [artifact(5, 0.1)]})
    responses.add(responses.GET, f"{LIST_URL}/5/zip", status=500)
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.failed) == (0, 1)


@pytest.mark.parametrize("status", [401, 403, 404])
@responses.activate
def test_list_auth_errors_raise_collector_error(status):
    responses.add(responses.GET, LIST_URL, status=status)
    with pytest.raises(CollectorError, match=str(status)):
        collect(None, session(), OWNER, [REPO], NOW)


@responses.activate
def test_list_server_error_raises_collector_error():
    responses.add(responses.GET, LIST_URL, status=502)
    with pytest.raises(CollectorError, match="502"):
        list_report_artifacts(session(), OWNER, REPO, since())


@responses.activate
def test_list_network_error_raises_collector_error():
    responses.add(
        responses.GET, LIST_URL, body=requests.ConnectionError(f"boom {TOKEN}")
    )
    with pytest.raises(CollectorError) as info:
        list_report_artifacts(session(), OWNER, REPO, since())
    assert TOKEN not in str(info.value)


# --- main ---

ENV = {
    "QUALITY_COLLECTOR_URL": "postgresql://u:dbpass@h/quality",
    "GITHUB_TOKEN": TOKEN,
    "QUALITY_GITHUB_OWNER": OWNER,
    "QUALITY_GITHUB_REPOS": f" {REPO} , ,other ",
}


class FakeConn:
    rollbacks = 0

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def main_env(monkeypatch, stored):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cmain, "connect", lambda url: FakeConn())


@pytest.mark.parametrize("missing", list(ENV))
def test_collect_missing_env_exits_2(main_env, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing)
    assert main(["collect"]) == 2
    assert missing in capsys.readouterr().err


@responses.activate
def test_main_collect_success_trims_repos(main_env, report, capsys):
    for repo, sha in ((REPO, "c1"), ("other", "c2")):
        url = f"https://api.github.com/repos/{OWNER}/{repo}/actions/artifacts"
        responses.add(
            responses.GET, url, json={"artifacts": [artifact(1, 0.1, sha=sha)]}
        )
        responses.add(responses.GET, f"{url}/1/zip", body=zip_bytes(report(sha, repo)))
    assert main(["collect"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"{REPO} inserted=1 skipped=0 rejected=0 failed=0",
        "other inserted=1 skipped=0 rejected=0 failed=0",
    ]


@responses.activate
def test_main_collect_exits_1_on_failed_but_processes_other_repos(
    main_env, report, capsys
):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/artifacts"
    responses.add(responses.GET, url, json={"artifacts": [artifact(1, 0.1)]})
    responses.add(responses.GET, f"{url}/1/zip", body=b"corrupt")
    other = f"https://api.github.com/repos/{OWNER}/other/actions/artifacts"
    responses.add(responses.GET, other, json={"artifacts": [artifact(2, 0.1)]})
    responses.add(
        responses.GET, f"{other}/2/zip", body=zip_bytes(report("c2", "other"))
    )
    assert main(["collect"]) == 1
    assert "other inserted=1" in capsys.readouterr().out


@responses.activate
def test_main_collect_401_exits_1_and_never_leaks_token(main_env, report, capsys):
    responses.add(
        responses.GET, LIST_URL, status=401, json={"message": "Bad credentials"}
    )
    other = f"https://api.github.com/repos/{OWNER}/other/actions/artifacts"
    responses.add(responses.GET, other, json={"artifacts": [artifact(2, 0.1)]})
    responses.add(
        responses.GET, f"{other}/2/zip", body=zip_bytes(report("c2", "other"))
    )
    assert main(["collect"]) == 1
    captured = capsys.readouterr()
    assert "401" in captured.err
    assert "other inserted=1" in captured.out
    assert TOKEN not in captured.out + captured.err
    assert "dbpass" not in captured.out + captured.err


def test_main_collect_db_error_is_secret_safe(monkeypatch, capsys):
    import psycopg

    for k, v in ENV.items():
        monkeypatch.setenv(k, v)

    def boom(url):
        raise psycopg.OperationalError(f"could not connect {url}")

    monkeypatch.setattr(cmain, "connect", boom)
    assert main(["collect"]) == 1
    err = capsys.readouterr().err
    assert "OperationalError" in err and "dbpass" not in err


MIGRATE_ENV = {
    "QUALITY_ADMIN_URL": "postgresql://admin:adminpw@h/quality",
    "QUALITY_WRITER_PASSWORD": "w",
    "QUALITY_COLLECTOR_PASSWORD": "c",
    "QUALITY_GRAFANA_PASSWORD": "g",
}


def test_main_migrate_success(monkeypatch, capsys):
    for k, v in MIGRATE_ENV.items():
        monkeypatch.setenv(k, v)
    calls = []
    monkeypatch.setattr(cmain, "migrate", lambda url, pw: calls.append((url, pw)))
    assert main(["migrate"]) == 0
    assert capsys.readouterr().out.strip() == "migrated"
    assert calls == [
        (
            MIGRATE_ENV["QUALITY_ADMIN_URL"],
            {"writer": "w", "collector": "c", "grafana": "g"},
        )
    ]


def test_main_migrate_missing_env_exits_2(monkeypatch, capsys):
    for k, v in MIGRATE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("QUALITY_GRAFANA_PASSWORD")
    assert main(["migrate"]) == 2
    assert "QUALITY_GRAFANA_PASSWORD" in capsys.readouterr().err


def test_main_migrate_db_error_is_secret_safe(monkeypatch, capsys):
    import psycopg

    for k, v in MIGRATE_ENV.items():
        monkeypatch.setenv(k, v)

    def boom(url, pw):
        raise psycopg.OperationalError("password=adminpw")

    monkeypatch.setattr(cmain, "migrate", boom)
    assert main(["migrate"]) == 1
    err = capsys.readouterr().err
    assert "OperationalError" in err and "adminpw" not in err


@responses.activate
def test_db_rejected_report_is_failed_rolled_back_and_run_continues(
    monkeypatch, report, capsys
):
    import psycopg

    conn = FakeConn()
    seen = []

    def fake_insert(c, rep):
        seen.append(rep["commit"])
        if rep["commit"] == "bad":
            raise psycopg.errors.DataError("invalid input postgresql://u:dbpass@h/q")
        return True

    monkeypatch.setattr(cmain, "insert_report", fake_insert)
    monkeypatch.setattr(cmain, "connect", lambda url: conn)
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/artifacts"
    responses.add(
        responses.GET,
        url,
        json={"artifacts": [artifact(1, 0.1, sha="bad"), artifact(2, 0.2, sha="good")]},
    )
    responses.add(responses.GET, f"{url}/1/zip", body=zip_bytes(report("bad")))
    responses.add(responses.GET, f"{url}/2/zip", body=zip_bytes(report("good")))
    other = f"https://api.github.com/repos/{OWNER}/other/actions/artifacts"
    responses.add(
        responses.GET, other, json={"artifacts": [artifact(3, 0.1, sha="next")]}
    )
    responses.add(
        responses.GET, f"{other}/3/zip", body=zip_bytes(report("next", "other"))
    )

    assert main(["collect"]) == 1
    captured = capsys.readouterr()
    assert f"{REPO} inserted=1 skipped=0 rejected=0 failed=1" in captured.out
    assert "other inserted=1 skipped=0 rejected=0 failed=0" in captured.out
    assert conn.rollbacks == 1
    assert seen == ["bad", "good", "next"]
    assert "DataError" in captured.err and "dbpass" not in captured.out + captured.err


# --- untrusted artifact contents (ruling R19) ---


def _one_artifact(art, body):
    responses.add(responses.GET, LIST_URL, json={"artifacts": [art]})
    add_zip(art["id"], body)


@responses.activate
def test_list_captures_repository_ids():
    responses.add(
        responses.GET,
        LIST_URL,
        json={
            "artifacts": [
                artifact(1, 0.1, repo_id=10, head_repo_id=10),
                artifact(2, 0.1),
            ]
        },
    )
    a, b = list_report_artifacts(session(), OWNER, REPO, since())
    assert (a.repository_id, a.head_repository_id, a.from_trusted_repo) == (
        10,
        10,
        True,
    )
    assert (b.repository_id, b.head_repository_id, b.from_trusted_repo) == (
        None,
        None,
        True,
    )


@pytest.mark.parametrize("repo_id,head_repo_id", [(10, 99), (10, None), (None, 10)])
@responses.activate
def test_fork_and_unverifiable_artifacts_are_rejected_without_download(
    stored, report, capsys, repo_id, head_repo_id
):
    responses.add(
        responses.GET,
        LIST_URL,
        json={
            "artifacts": [artifact(1, 0.1, repo_id=repo_id, head_repo_id=head_repo_id)]
        },
    )
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.rejected, s.failed) == (0, 1, 0)
    assert not stored
    assert len(responses.calls) == 1  # listing only, no zip fetched
    captured = capsys.readouterr()
    assert f"{REPO} inserted=0 skipped=0 rejected=1 failed=0" in captured.out
    assert "artifact 1 rejected" in captured.err and TOKEN not in captured.err


@responses.activate
def test_same_repo_artifact_is_collected(stored, report):
    _one_artifact(
        artifact(1, 0.1, repo_id=10, head_repo_id=10), zip_bytes(report("c1"))
    )
    assert collect(None, session(), OWNER, [REPO], since()).inserted == 1


@responses.activate
def test_report_naming_another_repo_is_rejected(stored, report, capsys):
    _one_artifact(artifact(1, 0.1), zip_bytes(report("c1", repo="victim")))
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.rejected, s.failed) == (0, 1, 0)
    assert not stored
    assert "different repository" in capsys.readouterr().err


@responses.activate
def test_report_commit_differing_from_run_commit_is_rejected(stored, report, capsys):
    _one_artifact(artifact(1, 0.1, sha="realsha"), zip_bytes(report("forged")))
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.rejected, s.failed) == (0, 1, 0)
    assert not stored
    assert "commit differs" in capsys.readouterr().err


@responses.activate
def test_rejections_do_not_fail_the_run(main_env, report, capsys):
    responses.add(
        responses.GET,
        LIST_URL,
        json={"artifacts": [artifact(1, 0.1, repo_id=1, head_repo_id=2)]},
    )
    other = f"https://api.github.com/repos/{OWNER}/other/actions/artifacts"
    responses.add(responses.GET, other, json={"artifacts": []})
    assert main(["collect"]) == 0


@responses.activate
def test_download_over_cap_by_content_length_is_failed(monkeypatch, stored):
    monkeypatch.setattr("collector.github.MAX_DOWNLOAD_BYTES", 100)
    _one_artifact(artifact(1, 0.1), b"x" * 101)
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.failed, s.rejected) == (0, 1, 0)


def test_streamed_body_over_cap_is_refused_without_content_length(monkeypatch):
    from collector.github import BadReportError, _read_capped

    class Stub:
        headers: dict = {}  # noqa: RUF012

        @staticmethod
        def iter_content(_size):
            yield b"x" * 60
            yield b"x" * 60

    monkeypatch.setattr("collector.github.MAX_DOWNLOAD_BYTES", 100)
    with pytest.raises(BadReportError, match="size limit"):
        _read_capped(Stub)


@responses.activate
def test_report_json_over_cap_is_failed(monkeypatch, stored, report):
    monkeypatch.setattr("collector.github.MAX_REPORT_BYTES", 50)
    _one_artifact(artifact(1, 0.1), zip_bytes(report("c1")))
    s = collect(None, session(), OWNER, [REPO], since())
    assert (s.inserted, s.failed) == (0, 1)
    assert not stored


@responses.activate
def test_report_just_under_cap_is_accepted(report):
    add_zip(1, zip_bytes(report("c1")))
    assert download_report(session(), OWNER, REPO, 1)["commit"] == "c1"


class _ArtifactServer:
    """Local HTTP server answering the artifact zip URL from a scripted status list."""

    def __init__(self, statuses, body):
        self.statuses = list(statuses)
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                status = outer.statuses.pop(0) if outer.statuses else 200
                payload = body if status == 200 else b""
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


def _plain_http_session():
    """A new_session whose retry adapter also serves http:// with no backoff delay."""
    s = session()
    adapter = s.get_adapter("https://")
    adapter.max_retries = adapter.max_retries.new(backoff_factor=0)
    s.mount("http://", adapter)
    return s


def test_session_retry_policy():
    retry = session().get_adapter("https://").max_retries
    assert retry.total == 3
    assert retry.connect == 3
    assert retry.read == 3
    assert retry.status == 3
    assert retry.backoff_factor == 1
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert retry.allowed_methods == frozenset({"GET"})
    assert retry.respect_retry_after_header is True
    assert retry.raise_on_status is False


def test_download_retries_transient_server_error(monkeypatch, sample):
    with _ArtifactServer([503, 503, 200], zip_bytes(sample)) as server:
        monkeypatch.setattr(cgithub, "API", server.url)
        assert download_report(_plain_http_session(), OWNER, REPO, 7) == sample
        assert server.requests == 3


def test_download_does_not_retry_404(monkeypatch):
    with _ArtifactServer([404], b"") as server:
        monkeypatch.setattr(cgithub, "API", server.url)
        with pytest.raises(requests.HTTPError):
            download_report(_plain_http_session(), OWNER, REPO, 7)
        assert server.requests == 1
