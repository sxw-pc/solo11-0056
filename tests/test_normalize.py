from schema_extractor.header_infer import infer_column
from schema_extractor.normalize import (
    DEFAULT_DATE_FORMATS,
    canonical_decimal,
    clean_text,
    is_currency,
    is_date,
    is_decimal,
    is_integer,
    is_percent,
    normalize_cell,
    parse_currency,
    parse_date,
    parse_percent,
    parse_plain_number,
)


def test_clean_text_collapses_all_whitespace():
    assert clean_text("  示例\t项目\n  甲  ") == "示例 项目 甲"
    assert clean_text(" 甲 乙 ") == "甲 乙"
    assert clean_text(None) == ""


def test_currency_parsing_and_units():
    assert parse_currency("¥1,234.50") is not None
    number, unit = parse_currency("¥1,234.50")
    assert canonical_decimal(number) == "1234.5"
    assert unit == "CNY"
    assert parse_currency("$1,000")[1] == "USD"
    assert parse_currency("300元")[1] == "CNY"
    assert parse_currency("100") is None
    assert is_currency("￥12") is True
    assert is_currency("12") is False


def test_percent_parsing():
    assert canonical_decimal(parse_percent("12.5%")) == "12.5"
    assert canonical_decimal(parse_percent("-2.5%")) == "-2.5"
    assert parse_percent("12") is None
    assert is_percent("0%") is True


def test_plain_numbers():
    assert parse_plain_number("1,234")[1] == "integer"
    assert parse_plain_number("1,234.50")[1] == "decimal"
    assert parse_plain_number("1,23") is None
    assert is_integer("1,000") is True
    assert is_decimal("1.5") is True
    assert is_decimal("1,000") is False


def test_canonical_decimal_removes_trailing_zeroes():
    assert canonical_decimal(parse_plain_number("1,234.50")[0]) == "1234.5"
    assert canonical_decimal(parse_plain_number("-0.0")[0]) == "0"
    assert canonical_decimal(parse_plain_number("42.000")[0]) == "42"
    assert canonical_decimal(parse_plain_number("-3.25")[0]) == "-3.25"


def test_date_formats_and_invalid_values():
    assert parse_date("2026-01-02") == "2026-01-02"
    assert parse_date("2026/1/2") == "2026-01-02"
    assert parse_date("2026年1月2日") == "2026-01-02"
    assert parse_date("2026-13-40") is None
    assert parse_date("2026-01-02", DEFAULT_DATE_FORMATS) == "2026-01-02"
    assert is_date("2026/01/02") is True


def test_normalize_cell_successes():
    formats = DEFAULT_DATE_FORMATS
    assert normalize_cell("1,234.50", "decimal", formats) == ("1234.5", "", None)
    assert normalize_cell("¥120", "currency", formats) == ("120", "CNY", None)
    assert normalize_cell("12.5%", "percent", formats) == ("12.5", "%", None)
    assert normalize_cell("2026/1/2", "date", formats) == ("2026-01-02", "", None)
    assert normalize_cell("  A-01 ", "identifier", formats) == ("A-01", "", None)


def test_normalize_cell_failures_do_not_raise():
    formats = DEFAULT_DATE_FORMATS
    assert normalize_cell("坏日期", "date", formats) == (
        "坏日期",
        "",
        "date_unparsable",
    )
    assert normalize_cell("十二", "integer", formats) == (
        "十二",
        "",
        "number_unparsable",
    )
    assert normalize_cell("十二%", "percent", formats) == (
        "十二%",
        "%",
        "percent_unparsable",
    )
    assert normalize_cell("一百元", "currency", formats) == (
        "一百元",
        "",
        "number_unparsable",
    )


def test_empty_cell_is_always_empty_string():
    assert normalize_cell(None, "date") == ("", "", None)
    assert normalize_cell("   ", "currency") == ("", "", None)
    assert normalize_cell("", "text") == ("", "", None)


def test_accounting_negative_currency_forms():
    number, unit = parse_currency("(¥1,234.50)")
    assert canonical_decimal(number) == "-1234.5"
    assert unit == "CNY"
    number, unit = parse_currency("-¥1,200")
    assert canonical_decimal(number) == "-1200"
    assert unit == "CNY"
    number, unit = parse_currency("￥-300")
    assert canonical_decimal(number) == "-300"
    assert unit == "CNY"
    number, unit = parse_currency("($1,000.00)")
    assert canonical_decimal(number) == "-1000"
    assert unit == "USD"
    number, unit = parse_currency("(300元)")
    assert canonical_decimal(number) == "-300"
    assert unit == "CNY"
    number, unit = parse_currency("€2,500.75-")
    assert canonical_decimal(number) == "-2500.75"
    assert unit == "EUR"
    assert is_currency("(¥1,234.50)") is True
    assert is_currency("-¥1,200") is True


def test_accounting_negative_plain_numbers():
    number, kind = parse_plain_number("(1,234.50)")
    assert kind == "decimal"
    assert canonical_decimal(number) == "-1234.5"
    number, kind = parse_plain_number("1234.5-")
    assert kind == "decimal"
    assert canonical_decimal(number) == "-1234.5"
    number, kind = parse_plain_number("(1,200)")
    assert kind == "integer"
    assert canonical_decimal(number) == "-1200"
    number, kind = parse_plain_number("+1,200")
    assert kind == "integer"
    assert canonical_decimal(number) == "1200"
    assert is_integer("(1,200)") is True
    assert is_decimal("1234.5-") is True
    assert is_integer("1234.5-") is False


def test_accounting_negative_percent():
    assert canonical_decimal(parse_percent("(2.5%)")) == "-2.5"
    assert canonical_decimal(parse_percent("2.5%-")) == "-2.5"
    assert canonical_decimal(parse_percent("-2.5%")) == "-2.5"
    assert is_percent("(0%)") is True


def test_pseudo_negatives_are_rejected():
    # 双重符号/括号指示（--5、(-5)、(+5)、-5- 等）一律不合法
    for raw in ("--5", "(-5)", "(+5)", "-5-", "5--", "++5", "-(5)", "(5)-"):
        assert parse_plain_number(raw) is None, raw
    assert parse_currency("(-¥5)") is None
    assert parse_currency("--¥5") is None
    assert parse_currency("(¥-5)") is None
    assert parse_percent("(-5%)") is None
    # 括号不成对、为空或嵌套同样不合法
    assert parse_plain_number("(5") is None
    assert parse_plain_number("5)") is None
    assert parse_plain_number("()") is None
    assert parse_plain_number("( )") is None
    assert parse_plain_number("((5))") is None


def test_parenthesized_plain_text_is_not_numeric():
    # 纯括号文本没有数字核心，不得被当成数字
    assert parse_plain_number("(备注)") is None
    assert parse_currency("(备注)") is None
    assert parse_percent("(备注)") is None
    assert is_integer("(备注)") is False
    assert is_decimal("(备注)") is False
    assert is_currency("(备注)") is False


def test_normalize_cell_accounting_negatives():
    formats = DEFAULT_DATE_FORMATS
    assert normalize_cell("(1,234.50)", "decimal", formats) == ("-1234.5", "", None)
    assert normalize_cell("-¥1,200", "currency", formats) == ("-1200", "CNY", None)
    assert normalize_cell("￥-300", "currency", formats) == ("-300", "CNY", None)
    assert normalize_cell("1234.5-", "decimal", formats) == ("-1234.5", "", None)
    assert normalize_cell("(2.5%)", "percent", formats) == ("-2.5", "%", None)
    assert normalize_cell("(1,200)", "integer", formats) == ("-1200", "", None)


def test_normalize_cell_pseudo_negative_reason_is_distinct():
    formats = DEFAULT_DATE_FORMATS
    assert normalize_cell("(--5)", "decimal", formats) == (
        "(--5)",
        "",
        "accounting_negative_unparsable",
    )
    assert normalize_cell("(-5)", "integer", formats) == (
        "(-5)",
        "",
        "accounting_negative_unparsable",
    )
    assert normalize_cell("(-5%)", "percent", formats) == (
        "(-5%)",
        "%",
        "accounting_negative_unparsable",
    )
    assert normalize_cell("(¥-5)", "currency", formats) == (
        "(¥-5)",
        "",
        "accounting_negative_unparsable",
    )
    # 无数字的括号文本与纯文本仍走原有 reason，便于区分定位
    assert normalize_cell("(备注)", "decimal", formats) == (
        "(备注)",
        "",
        "number_unparsable",
    )
    assert normalize_cell("十二", "integer", formats) == (
        "十二",
        "",
        "number_unparsable",
    )


def test_accounting_column_infers_decimal(default_config):
    profile = infer_column(
        "金额",
        ["(1,234.50)", "(2,000.00)", "3,400.00", "(800.00)"],
        False,
        default_config.inference,
        default_config.normalization,
    )
    assert profile.type == "decimal"
    assert profile.match_ratio == 1.0


def test_accounting_currency_column_infers_currency(default_config):
    profile = infer_column(
        "金额",
        ["(¥1,200)", "-¥300", "￥-400", "¥500"],
        False,
        default_config.inference,
        default_config.normalization,
    )
    assert profile.type == "currency"
    assert profile.match_ratio == 1.0


def test_parenthesized_note_column_stays_text(default_config):
    profile = infer_column(
        "备注",
        ["(备注一)", "(备注二)", "(备注三)"],
        False,
        default_config.inference,
        default_config.normalization,
    )
    assert profile.type == "text"


def test_single_bad_cell_keeps_column_type_and_recovers(default_config):
    # 单个伪负数单元格不拖垮整列判定，也不让规范化抛异常
    profile = infer_column(
        "金额",
        ["(1,234.50)", "2,000.00", "3,400.00", "(--5)"],
        False,
        default_config.inference,
        default_config.normalization,
    )
    assert profile.type == "decimal"
    assert profile.match_ratio == 0.75
    assert normalize_cell("(--5)", "decimal") == (
        "(--5)",
        "",
        "accounting_negative_unparsable",
    )
    assert normalize_cell("(1,234.50)", "decimal") == ("-1234.5", "", None)
