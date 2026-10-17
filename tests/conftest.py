import json
import os
import secrets
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from collector.migrate import migrate

FIXTURES = Path(__file__).parent / "fixtures"
_PASSWORDS = {"writer": "w-test-pw", "collector": "c-test-pw", "grafana": "g-test-pw"}
_ROLES = {
    "writer": "quality_writer",
    "collector": "quality_collector",
    "grafana": "quality_grafana",
}


@pytest.fixture(scope="session")
def sample() -> dict:
    return json.loads((FIXTURES / "sample-report.json").read_text())


@pytest.fixture(scope="session")
def passwords() -> dict[str, str]:
    return dict(_PASSWORDS)


@pytest.fixture(scope="session")
def _server_url() -> str:
    url = os.environ.get("QUALITY_TEST_ADMIN_URL")
    if not url:
        pytest.skip("QUALITY_TEST_ADMIN_URL is not set")
    return url


@pytest.fixture(scope="session")
def admin_url(_server_url, passwords):
    """URL of a fresh, migrated database; dropped at teardown."""
    name = f"quality_test_{secrets.token_hex(4)}"
    with psycopg.connect(_server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(_server_url, dbname=name)
    migrate(url, passwords)
    yield url
    with psycopg.connect(_server_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )


def _role_conn(admin_url: str, role: str, password: str):
    info = conninfo_to_dict(admin_url)
    url = make_conninfo(
        "",
        host=info.get("host"),
        port=info.get("port"),
        dbname=info["dbname"],
        user=role,
        password=password,
    )
    return psycopg.connect(url)


@pytest.fixture(scope="session")
def writer_conn(admin_url, passwords):
    with _role_conn(admin_url, _ROLES["writer"], passwords["writer"]) as conn:
        yield conn


@pytest.fixture(scope="session")
def grafana_conn(admin_url, passwords):
    with _role_conn(admin_url, _ROLES["grafana"], passwords["grafana"]) as conn:
        yield conn
