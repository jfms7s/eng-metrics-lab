"""Fetch quality-report artifacts from the GitHub Actions API."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

API = "https://api.github.com"
TIMEOUT = 30
PER_PAGE = 100
REPORT_FILE = "report.json"
_AUTH_STATUSES = (401, 403, 404)


class CollectorError(Exception):
    """GitHub refused or could not serve a listing; the whole repo is unusable."""


class BadReportError(ValueError):
    """An artifact did not contain a usable report.json."""


@dataclass(frozen=True)
class Artifact:
    id: int
    created_at: datetime
    expired: bool
    head_branch: str
    head_sha: str


def new_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return session


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _to_artifact(raw: dict) -> Artifact:
    run = raw.get("workflow_run") or {}
    return Artifact(
        id=raw["id"],
        created_at=_parse_time(raw["created_at"]),
        expired=bool(raw.get("expired")),
        head_branch=run.get("head_branch") or "",
        head_sha=run.get("head_sha") or "",
    )


def list_report_artifacts(
    session: requests.Session, owner: str, repo: str, since: datetime,
    name: str = "quality-report",
) -> list[Artifact]:
    """Unexpired artifacts called `name` created at or after `since`, newest first."""
    url = f"{API}/repos/{owner}/{repo}/actions/artifacts"
    found: list[Artifact] = []
    page = 1
    while True:
        try:
            resp = session.get(
                url, params={"name": name, "per_page": PER_PAGE, "page": page}, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise CollectorError(f"{owner}/{repo}: listing failed ({type(exc).__name__})") from None
        if resp.status_code in _AUTH_STATUSES:
            raise CollectorError(f"{owner}/{repo}: GitHub returned HTTP {resp.status_code}")
        if not resp.ok:
            raise CollectorError(f"{owner}/{repo}: listing failed with HTTP {resp.status_code}")
        batch = [_to_artifact(raw) for raw in resp.json().get("artifacts", [])]
        found.extend(a for a in batch if not a.expired and a.created_at >= since)
        # Newest first: once a whole page predates `since`, later pages can only be older.
        if len(batch) < PER_PAGE or all(a.created_at < since for a in batch):
            return found
        page += 1


def download_report(
    session: requests.Session, owner: str, repo: str, artifact_id: int
) -> dict:
    """Download an artifact zip and return its parsed report.json."""
    resp = session.get(
        f"{API}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip", timeout=TIMEOUT
    )
    resp.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            data = archive.read(REPORT_FILE)
    except zipfile.BadZipFile:
        raise BadReportError("artifact is not a valid zip") from None
    except KeyError:
        raise BadReportError(f"artifact has no {REPORT_FILE}") from None
    try:
        report = json.loads(data)
    except ValueError:
        raise BadReportError(f"{REPORT_FILE} is not valid JSON") from None
    if not isinstance(report, dict):
        raise BadReportError(f"{REPORT_FILE} is not a JSON object")
    return report
