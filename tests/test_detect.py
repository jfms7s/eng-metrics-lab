from quality_report.config import DEFAULT_EXCLUDES
from quality_report.detect import detect_languages


def test_detect_languages_nested_manifests(tmp_path):
    (tmp_path / "Cargo.toml").touch()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "package.json").write_text("{}")
    assert detect_languages(tmp_path, list(DEFAULT_EXCLUDES)) == ["rust", "typescript"]


def test_detect_languages_ignores_excluded_dirs(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "package.json").write_text("{}")
    assert detect_languages(tmp_path, list(DEFAULT_EXCLUDES)) == []
