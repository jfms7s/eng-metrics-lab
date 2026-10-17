"""End-to-end runs over the fixture repos with the real tools (pytest -m tools)."""

import importlib.metadata
import shutil
from pathlib import Path

import pytest

from quality_report.report import run

pytestmark = pytest.mark.tools

if not all(shutil.which(tool) for tool in ("jscpd", "lizard", "cargo-machete")):
    pytest.skip("jscpd, lizard and cargo-machete are required", allow_module_level=True)

FIX = Path(__file__).parent / "fixtures"
CTX = {"QUALITY_REPO": "t", "QUALITY_COMMIT": "c", "QUALITY_BRANCH": "b"}


@pytest.fixture
def out(tmp_path):
    return tmp_path / "report.json"


def test_ts_mini(out):
    r = run(FIX / "ts-mini", out, env=CTX)
    assert r["duplication"]["clones"] == 1
    assert r["complexity"]["functions_over_threshold"] == 1
    assert r["complexity"]["over"] == {"7": 1, "10": 1, "15": 0}
    assert r["languages"] == ["typescript"]
    assert r["tools"]["quality-report"] == importlib.metadata.version("quality-report")
    assert {"jscpd", "lizard", "cargo-machete"} <= set(r["tools"])


def test_rust_mini(out):
    r = run(FIX / "rust-mini", out, env=CTX)
    assert r["dead_code"]["by_kind"]["unused_dependencies"] == 1
    assert r["dead_code"]["by_kind"]["suppressed"] == 1
    assert r["complexity"]["over"] == {"7": 1, "10": 0, "15": 0}


def test_excludes_apply_to_jscpd_and_lizard(out):
    r = run(FIX / "excludes-mini", out, env=CTX)
    assert r["duplication"]["clones"] == 1  # src/x.ts vs src/y.ts only
    assert r["size"]["files"] == 2
