import pytest

from quality_report.config import DEFAULT_EXCLUDES
from quality_report.detect import detect_languages, is_test_file


def test_detect_languages_nested_manifests(tmp_path):
    (tmp_path / "Cargo.toml").touch()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "package.json").write_text("{}")
    assert detect_languages(tmp_path, list(DEFAULT_EXCLUDES)) == ["rust", "typescript"]


def test_detect_languages_ignores_excluded_dirs(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "package.json").write_text("{}")
    assert detect_languages(tmp_path, list(DEFAULT_EXCLUDES)) == []


@pytest.mark.parametrize(
    "path",
    [
        "src/app.test.ts",
        "src/ui/button.spec.tsx",
        "lib/util.test.mjs",
        "src/__tests__/app.ts",
        "src/__mocks__/fs.ts",
        "plugin/test/helpers/fake.ts",
        "tests/setup.js",
        "test_app.py",
        "pkg/app_test.py",
        "tests/conftest.py",
        "scripts/tests/fixtures.py",
        "pkg/test/helpers.py",
        "server/api/handler_test.go",
        "server/api/testdata/sample.go",
    ],
)
def test_is_test_file(path):
    assert is_test_file(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/app.ts",
        "src/latest.ts",
        "src/contest.py",
        "src/testing.py",
        "src/attest/app.ts",
        "pkg/tests.py",
        "server/api/handler.go",
        # Go and TS name conventions do not carry over to other languages.
        "server/test/helpers.go",
        "server/tests/helpers.go",
        "app_test.ts",
        "app.test.py",
        "testdata/x.py",
        # Rust test code is found by a source scan (rusttests), not by path.
        "tests/it.rs",
        "src/tests/mod.rs",
        "README.md",
    ],
)
def test_is_not_test_file(path):
    assert not is_test_file(path)
