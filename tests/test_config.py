import pytest
import yaml

from schema_extractor.config import ConfigError, load_config


def _write_config(tmp_path, data: dict) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return str(path)


def test_normalization_defaults_include_partial_and_time(tmp_path):
    config = load_config(_write_config(tmp_path, {}))
    assert config.normalization.date_formats == ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日")
    assert config.normalization.partial_date_formats == (
        "%Y-%m",
        "%Y/%m",
        "%Y年%m月",
        "%Y",
        "%Y年",
    )
    assert config.normalization.time_formats == ("%H:%M:%S", "%H:%M")


def test_existing_date_formats_key_still_accepted_alone(tmp_path):
    config = load_config(
        _write_config(tmp_path, {"normalization": {"date_formats": ["%d/%m/%Y"]}})
    )
    assert config.normalization.date_formats == ("%d/%m/%Y",)
    # 未显式配置时新选项取默认值
    assert "%Y-%m" in config.normalization.partial_date_formats
    assert config.normalization.time_formats


def test_empty_lists_disable_new_date_forms(tmp_path):
    config = load_config(
        _write_config(
            tmp_path,
            {"normalization": {"partial_date_formats": [], "time_formats": []}},
        )
    )
    assert config.normalization.partial_date_formats == ()
    assert config.normalization.time_formats == ()


def test_partial_date_formats_reject_bad_items(tmp_path):
    with pytest.raises(ConfigError, match="partial_date_formats"):
        load_config(
            _write_config(tmp_path, {"normalization": {"partial_date_formats": [""]}})
        )
    with pytest.raises(ConfigError, match="partial_date_formats"):
        load_config(
            _write_config(tmp_path, {"normalization": {"partial_date_formats": [1]}})
        )


def test_partial_date_formats_reject_time_directives(tmp_path):
    with pytest.raises(ConfigError, match="时间指令"):
        load_config(
            _write_config(
                tmp_path, {"normalization": {"partial_date_formats": ["%H:%M"]}}
            )
        )


def test_time_formats_require_time_directives(tmp_path):
    with pytest.raises(ConfigError, match="时间指令"):
        load_config(
            _write_config(tmp_path, {"normalization": {"time_formats": ["%Y-%m-%d"]}})
        )


def test_time_formats_reject_non_list(tmp_path):
    with pytest.raises(ConfigError, match="time_formats"):
        load_config(
            _write_config(tmp_path, {"normalization": {"time_formats": "%H:%M"}})
        )
