import pytest

from quality_report.config import DEFAULT_EXCLUDES, load_config


def test_load_config_defaults_when_no_file(tmp_path):
    c = load_config(tmp_path)
    assert c.complexity_threshold == 10 and c.duplication_min_tokens == 50
    assert set(DEFAULT_EXCLUDES) <= set(c.exclude)


def test_load_config_merges_repo_excludes(tmp_path):
    (tmp_path / ".quality.yml").write_text(
        'exclude: ["**/generated/**"]\ncomplexity_threshold: 7\n'
    )
    c = load_config(tmp_path)
    assert "**/generated/**" in c.exclude and "node_modules" in c.exclude
    assert c.complexity_threshold == 7


def test_load_config_rejects_unknown_key(tmp_path):
    (tmp_path / ".quality.yml").write_text("complexity: 7\n")
    with pytest.raises(ValueError, match="complexity"):
        load_config(tmp_path)


def test_duplication_formats_default_none_and_override(tmp_path):
    assert load_config(tmp_path).duplication_formats is None
    (tmp_path / ".quality.yml").write_text("duplication_formats: [typescript, sql]\n")
    assert load_config(tmp_path).duplication_formats == ["typescript", "sql"]


@pytest.mark.parametrize("value", ["typescript", "[1, 2]", "{a: b}"])
def test_duplication_formats_must_be_list_of_strings(tmp_path, value):
    (tmp_path / ".quality.yml").write_text(f"duplication_formats: {value}\n")
    with pytest.raises(TypeError, match="duplication_formats"):
        load_config(tmp_path)
