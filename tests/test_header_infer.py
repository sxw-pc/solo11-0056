from schema_extractor.header_infer import (
    ColumnProfile,
    infer_column,
    infer_columns,
    type_distribution,
)


def _infer(name, values, config):
    return infer_column(
        name,
        values,
        False,
        config.inference,
        config.normalization,
    )


def test_identifier_type(default_config):
    profile = _infer("编号", ["A-1001", "B-2002", "C-3003"], default_config)
    assert profile.type == "identifier"
    assert profile.match_ratio == 1.0


def test_currency_type(default_config):
    profile = _infer("金额", ["¥1,200", "300元", "$9.5"], default_config)
    assert profile.type == "currency"
    assert profile.sample_values == ["¥1,200", "300元", "$9.5"]


def test_percent_type(default_config):
    profile = _infer("比例", ["5%", "-2.5%", "0%"], default_config)
    assert profile.type == "percent"
    assert profile.match_ratio == 1.0


def test_date_type(default_config):
    profile = _infer(
        "日期", ["2026-01-01", "2026/01/02", "2026年1月3日"], default_config
    )
    assert profile.type == "date"


def test_integer_type(default_config):
    profile = _infer("数量", ["1,000", "800", "42"], default_config)
    assert profile.type == "integer"


def test_mixed_numeric_becomes_decimal(default_config):
    profile = _infer("销售额", ["1,000", "800", "600.50"], default_config)
    assert profile.type == "decimal"


def test_enum_requires_repetition_or_small_set(default_config):
    enum_profile = _infer("状态", ["正常", "停用", "正常"], default_config)
    text_profile = _infer("名称", ["甲", "乙", "丙", "丁"], default_config)
    assert enum_profile.type == "enum"
    assert text_profile.type == "text"


def test_empty_column_is_text_with_zero_ratio(default_config):
    profile = _infer("空列", ["", "  ", None], default_config)
    assert profile.type == "text"
    assert profile.match_ratio == 0.0
    assert profile.sample_values == []


def test_samples_are_limited_and_unnamed_is_preserved(default_config):
    profile = infer_column(
        "col_2",
        ["一", "二", "三", "四"],
        True,
        default_config.inference,
        default_config.normalization,
    )
    assert profile.sample_values == ["一", "二", "三"]
    assert profile.unnamed is True


def test_date_ratio_can_be_partial(default_config):
    profile = _infer(
        "日期",
        ["2026-01-01", "2026-01-02", "2026-01-03", "不是日期"],
        default_config,
    )
    assert profile.type == "date"
    assert profile.match_ratio == 0.75


def test_sparse_column_falls_back_to_text(default_config):
    profile = _infer("备注", ["值", "", "", ""], default_config)
    assert profile.type == "text"
    assert profile.match_ratio == 0.25


def test_infer_columns_and_distribution(default_config):
    profiles = infer_columns(
        ["编号", "金额"],
        [False, False],
        [["A-01", "¥1"], ["A-02", "¥2"]],
        default_config.inference,
        default_config.normalization,
    )
    assert [profile.type for profile in profiles] == ["identifier", "currency"]
    assert type_distribution(profiles) == {"identifier": 1, "currency": 1}


def test_column_profile_to_dict_is_stable():
    profile = ColumnProfile("列", "text", 1.0, ["值"], False)
    assert profile.to_dict() == {
        "name": "列",
        "type": "text",
        "match_ratio": 1.0,
        "sample_values": ["值"],
        "unnamed": False,
    }
