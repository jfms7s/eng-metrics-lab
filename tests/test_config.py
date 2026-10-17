import pytest

from quality_report.config import DEFAULT_EXCLUDES, load_config


def test_load_config_defaults_when_no_file(tmp_path):
    c = load_config(tmp_path)
    assert c.complexity_threshold == 10 and c.duplication_min_tokens == 50
    assert set(DEFAULT_EXCLUDES) <= set(c.exclude)


def test_load_config_merges_repo_excludes(tmp_path):
    (tmp_path / ".quality.yml").write_text('exclude: ["**/generated/**"]\ncomplexity_threshold: 7\n')
    c = load_config(tmp_path)
    assert "**/generated/**" in c.exclude and "node_modules" in c.exclude
    assert c.complexity_threshold == 7


def test_load_config_rejects_unknown_key(tmp_path):
    (tmp_path / ".quality.yml").write_text("complexity: 7\n")
    with pytest.raises(ValueError, match="complexity"):
        load_config(tmp_path)
